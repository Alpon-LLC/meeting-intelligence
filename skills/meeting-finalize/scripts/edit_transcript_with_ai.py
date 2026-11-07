#!/usr/bin/env python3
"""Create an evidence-linked, AI-edited transcript without changing the canonical record."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from group_transcript_turns import group_turns, render


SCHEMA = "transcript.edited.v1"
NUMBER = re.compile(r"(?<!\w)(?:[$€£]?\d[\d,.]*(?:%|[a-zA-Z]{1,3})?)(?!\w)")
PROTECTED_LITERAL = re.compile(
    r"(?:https?://\S+|www\.\S+|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b)",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(?:no|not|never|neither|nor|without|cannot|can't|won't|wouldn't|shouldn't|"
    r"couldn't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't)\b",
    re.IGNORECASE,
)
WORD_STUTTER = re.compile(r"\b([A-Za-z][A-Za-z'-]*)(?:[ \t,]+\1\b)+", re.IGNORECASE)
WORD_TOKEN = re.compile(r"\b[\w]+(?:['’-][\w]+)*\b", re.UNICODE)
EDITOR_SYSTEM = """You are a conservative transcript editor, not a summarizer. Return valid JSON only.
Edit each supplied turn independently. Remove verbal fillers, abandoned false starts, stutters, and
clearly accidental repeated words, phrases, or sentences, including repetitions separated by short
pauses or punctuation. Preserve deliberate emphasis when it contributes meaning. Repair punctuation,
sentence boundaries, and obvious speech-to-text casing only when the intended wording is certain.
Do not substitute words, correct grammar, paraphrase, or reorder words: this pass is deletion-only
apart from punctuation and casing.
Never remove or invent facts, decisions, questions, dissent,
uncertainty, negation, names, technical terms, URLs, paths, dates, times, quantities, money,
percentages, owners, or action items. Never merge speakers. If wording is unclear, preserve it.
Output {\"turns\":[{\"turn_id\":\"...\",\"edited_text\":\"...\"}]}. Every turn must appear once."""
VERIFIER_SYSTEM = """Verify an edited transcript against its source and return valid JSON only.
Fail any turn that loses or changes meaning, uncertainty, negation, a fact, decision, question, name,
technical term, URL/path, number, date/time, owner, or action item; or invents information. Removal
of fillers, stutters, abandoned false starts, and clearly accidental local repetitions is allowed.
Output {\"verdict\":\"pass\"|\"fail\",\"issues\":[{\"turn_id\":\"...\",\"reason\":\"...\"}]}.
Use an empty issues array on pass. Every issue must identify the supplied turn_id."""


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not an object")
            rows.append(value)
    if not rows:
        raise ValueError("canonical transcript is empty")
    return rows


def chunks(turns: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    result, current, size = [], [], 0
    for turn in turns:
        item_size = len(str(turn.get("text", ""))) + 160
        if current and size + item_size > max_chars:
            result.append(current)
            current, size = [], 0
        current.append(turn)
        size += item_size
    if current:
        result.append(current)
    return result


def parse_json_response(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model response is not an object")
    return value


def parse_completion_response(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8", errors="replace").strip()
    try:
        response = json.loads(text)
        content = response["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        return parse_json_response(str(content))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        pass

    content_parts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        event = json.loads(data)
        choices = event.get("choices") or []
        if not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        part = delta.get("content", message.get("content", ""))
        if isinstance(part, str):
            content_parts.append(part)
        elif isinstance(part, list):
            content_parts.extend(
                str(item.get("text", "")) for item in part if isinstance(item, dict)
            )
    if not content_parts:
        raise ValueError("completion response did not contain assistant content")
    return parse_json_response("".join(content_parts))


def request_completion(endpoint: str, api_key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }).encode()
    request = urllib.request.Request(
        endpoint, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            return parse_completion_response(payload)
        except (KeyError, ValueError, OSError, urllib.error.HTTPError) as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"editor model failed after retries: {type(last_error).__name__}")


def model_messages(system: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def validate_edit(source: str, edited: str, terms: list[str]) -> list[str]:
    reasons: list[str] = []
    if not edited.strip():
        reasons.append("empty_edit")
    if len(edited) > max(len(source) * 1.25, len(source) + 80):
        reasons.append("expanded_beyond_limit")
    if len(source) >= 80 and len(edited) < len(source) * 0.4:
        reasons.append("contracted_beyond_limit")
    if Counter(token.casefold() for token in NUMBER.findall(source)) != Counter(token.casefold() for token in NUMBER.findall(edited)):
        reasons.append("numbers_changed")
    if Counter(token.casefold() for token in PROTECTED_LITERAL.findall(source)) != Counter(
        token.casefold() for token in PROTECTED_LITERAL.findall(edited)
    ):
        reasons.append("url_or_email_changed")
    if Counter(token.casefold() for token in NEGATION.findall(source)) != Counter(
        token.casefold() for token in NEGATION.findall(edited)
    ):
        reasons.append("negation_changed")
    source_tokens = [token.replace("’", "'").casefold() for token in WORD_TOKEN.findall(source)]
    edited_tokens = [token.replace("’", "'").casefold() for token in WORD_TOKEN.findall(edited)]
    source_index = 0
    for token in edited_tokens:
        while source_index < len(source_tokens) and source_tokens[source_index] != token:
            source_index += 1
        if source_index == len(source_tokens):
            reasons.append("words_added_or_reordered")
            break
        source_index += 1
    lower_source, lower_edited = source.casefold(), edited.casefold()
    for term in terms:
        if term.casefold() in lower_source and term.casefold() not in lower_edited:
            reasons.append(f"glossary_term_removed:{term}")
    return reasons


def remove_exact_local_repetitions(value: str) -> str:
    cleaned = WORD_STUTTER.sub(lambda match: match.group(1), value)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned.strip())
    deduplicated: list[str] = []
    previous = ""
    for sentence in sentences:
        normalized = re.sub(r"\s+", " ", sentence).strip().casefold()
        if normalized and normalized == previous:
            continue
        deduplicated.append(sentence)
        previous = normalized
    return " ".join(deduplicated).strip()


def edit_turns(
    turns: list[dict[str, Any]],
    terms: list[str],
    endpoint: str,
    api_key: str,
    model: str,
    max_chars: int = 6_000,
    timeout: int = 120,
    requester: Any = request_completion,
    verifier_model: str | None = None,
):
    edited_turns, ledger = [], []
    indexed = []
    for index, turn in enumerate(turns):
        indexed.append({**turn, "turn_id": str(turn.get("turn_id") or f"turn-{index:06d}")})

    def split(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        midpoint = max(1, len(items) // 2)
        return items[:midpoint], items[midpoint:]

    def request_edits(items: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
        payload = [
            {"turn_id": item["turn_id"], "speaker": item["speaker"], "text": item["text"]}
            for item in items
        ]
        try:
            response = requester(
                endpoint,
                api_key,
                model,
                model_messages(EDITOR_SYSTEM, {"glossary": terms, "turns": payload}),
                timeout,
            )
            raw_edits = response.get("turns", []) if isinstance(response, dict) else []
            edit_map = {
                str(item.get("turn_id")): str(item.get("edited_text"))
                for item in raw_edits
                if isinstance(item, dict) and isinstance(item.get("edited_text"), str)
            }
        except Exception as exc:
            if len(items) > 1:
                left, right = split(items)
                left_edits, left_errors = request_edits(left)
                right_edits, right_errors = request_edits(right)
                return {**left_edits, **right_edits}, {**left_errors, **right_errors}
            return {}, {items[0]["turn_id"]: f"model_error:{type(exc).__name__}"}

        missing = [item for item in items if item["turn_id"] not in edit_map]
        errors: dict[str, str] = {}
        if missing:
            if len(missing) == len(items) and len(items) > 1:
                left, right = split(missing)
                left_edits, left_errors = request_edits(left)
                right_edits, right_errors = request_edits(right)
                edit_map.update(left_edits)
                edit_map.update(right_edits)
                errors.update(left_errors)
                errors.update(right_errors)
            elif len(missing) < len(items):
                retry_edits, retry_errors = request_edits(missing)
                edit_map.update(retry_edits)
                errors.update(retry_errors)
            else:
                errors[items[0]["turn_id"]] = "missing_model_edit"
        return edit_map, errors

    def verify(items: list[dict[str, str]]) -> dict[str, list[str]]:
        if not items:
            return {}
        try:
            verification = requester(
                endpoint,
                api_key,
                verifier_model or model,
                model_messages(VERIFIER_SYSTEM, {"turns": items, "glossary": terms}),
                timeout,
            )
            issues = verification.get("issues", []) if isinstance(verification, dict) else []
            verdict = str(verification.get("verdict") or "").lower() if isinstance(verification, dict) else ""
            if verdict not in {"pass", "fail"} or (verdict == "fail" and not issues):
                raise ValueError("invalid verifier response")
        except Exception as exc:
            if len(items) > 1:
                left, right = split(items)
                return {**verify(left), **verify(right)}
            return {items[0]["turn_id"]: [f"verifier_error:{type(exc).__name__}"]}

        rejected: dict[str, list[str]] = {}
        for issue in issues if isinstance(issues, list) else []:
            turn_id = str(issue.get("turn_id") or "") if isinstance(issue, dict) else ""
            if turn_id:
                rejected.setdefault(turn_id, []).append(
                    str(issue.get("reason") or "semantic_verification_failed")
                )
        return rejected

    for batch in chunks(indexed, max_chars):
        edit_map, edit_errors = request_edits(batch)
        proposed, rejected = [], {}
        for item in batch:
            turn_id = item["turn_id"]
            safe_text = remove_exact_local_repetitions(item["text"])
            text = edit_map.get(turn_id)
            if not isinstance(text, str):
                rejected[turn_id], text = [edit_errors.get(turn_id, "missing_model_edit")], safe_text
            else:
                text = remove_exact_local_repetitions(text)
            reasons = validate_edit(item["text"], text, terms)
            if reasons:
                rejected[turn_id], text = reasons, safe_text
            proposed.append({
                "turn_id": turn_id,
                "source_text": item["text"],
                "edited_text": text,
                "safe_text": safe_text,
            })
        verification_payload = [
            {key: proposal[key] for key in ("turn_id", "source_text", "edited_text")}
            for proposal in proposed
            if proposal["turn_id"] not in rejected
        ]
        for turn_id, reasons in verify(verification_payload).items():
            rejected.setdefault(turn_id, []).extend(reasons)
        for item, proposal in zip(batch, proposed):
            turn_id = item["turn_id"]
            accepted = turn_id not in rejected
            text = proposal["edited_text"] if accepted else proposal["safe_text"]
            status = (
                "edited" if accepted and text != item["text"] else
                "unchanged" if accepted else
                "fallback_safe_cleanup" if text != item["text"] else
                "fallback_original"
            )
            edited_turns.append({**item, "source_text": item["text"], "edited_text": text, "text": text, "edit_status": status})
            ledger.append({"turn_id": turn_id, "status": status, "reasons": rejected.get(turn_id, [])})
    return edited_turns, ledger


def render_markdown(turns: list[dict[str, Any]], title: str, origin: str | None = None) -> str:
    return render(
        turns,
        title=title,
        note="AI-edited for readability. Canonical evidence is retained separately.",
        origin=origin,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", required=True, type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--title", default="Edited meeting transcript")
    parser.add_argument("--origin", help="ISO timestamp used as elapsed-time zero (normally capture start)")
    parser.add_argument("--api-url", default=os.environ.get("TRANSCRIPT_EDITOR_API_URL"))
    parser.add_argument("--api-key-file", type=Path, default=os.environ.get("TRANSCRIPT_EDITOR_API_KEY_FILE"))
    parser.add_argument("--model", default=os.environ.get("TRANSCRIPT_EDITOR_MODEL", "gemini/gemini-3.5-flash-lite"))
    parser.add_argument("--verifier-model", default=os.environ.get("TRANSCRIPT_VERIFIER_MODEL"))
    parser.add_argument("--max-chars", type=int, default=6_000)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not args.api_url or not args.api_key_file:
        parser.error("editor API URL and API key file are required")
    canonical_bytes = args.canonical.read_bytes()
    turns = group_turns(load_jsonl(args.canonical))
    context = json.loads(args.context.read_text()) if args.context and args.context.is_file() else {}
    terms = []
    for item in context.get("keyterms", []):
        term = item.get("text") if isinstance(item, dict) else item
        if str(term or "").strip():
            terms.append(str(term).strip())
    api_key = args.api_key_file.read_text().strip()
    if not api_key:
        raise SystemExit("editor API key file is empty")
    edited, audit = edit_turns(
        turns, terms, args.api_url, api_key, args.model,
        max_chars=args.max_chars, timeout=args.timeout,
        verifier_model=args.verifier_model,
    )
    source = {"path": args.canonical.name, "sha256": hashlib.sha256(canonical_bytes).hexdigest()}
    serializable = []
    for turn in edited:
        row = dict(turn)
        row["start"] = turn["start"].isoformat().replace("+00:00", "Z")
        row["end"] = turn["end"].isoformat().replace("+00:00", "Z")
        row["source_sha256"] = hashlib.sha256(turn["source_text"].encode()).hexdigest()
        serializable.append(row)
    fallback_count = sum(str(item["status"]).startswith("fallback_") for item in audit)
    manifest = {
        "schema": SCHEMA,
        "status": "completed_degraded" if fallback_count == len(edited) else "completed",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "model": args.model,
        "verifier_model": args.verifier_model or args.model,
        "policy": "deletion_only_disfluency_cleanup_v2",
        "elapsed_origin": args.origin,
        "turn_count": len(edited),
        "edited_turn_count": sum(turn["edit_status"] == "edited" for turn in edited),
        "fallback_turn_count": fallback_count,
    }
    output_dir = args.output_dir
    atomic_write(output_dir / "transcript.edited.v1.jsonl", "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in serializable))
    readable_markdown = render_markdown(edited, args.title, args.origin)
    atomic_write(output_dir / "transcript.edited.md", readable_markdown)
    atomic_write(output_dir / "transcript-readable.md", readable_markdown)
    atomic_write(output_dir / "transcript.edit-ledger.jsonl", "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in audit))
    atomic_write(output_dir / "transcript.editor.manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: manifest[key] for key in ("schema", "status", "turn_count", "edited_turn_count", "fallback_turn_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
