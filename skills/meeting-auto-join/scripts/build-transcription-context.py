#!/usr/bin/env python3
"""Build a compact, reproducible per-meeting Nova-3 vocabulary context."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "meeting-stt-context.v1"
MAX_TERMS = 100
MAX_TERM_LENGTH = 80
# Deepgram enforces 500 provider-token units, whose tokenizer is stricter than the portable
# character estimate available here. Reserve headroom so a locally valid context cannot fail live.
MAX_ESTIMATED_TOKENS = 300
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "was", "we", "with", "you",
})
SOURCE_ORDER = {
    "calendar_attendee": 0,
    "external_organization": 1,
    "project": 2,
    "organization": 3,
    "product": 4,
    "human_correction": 5,
    "agenda": 6,
}


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_fingerprint(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalize_term(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("keyterm must be a string")
    if CONTROL.search(value):
        raise ValueError("keyterm contains control characters")
    term = " ".join(value.split())
    if len(term) < 2:
        raise ValueError("keyterm must contain at least 2 characters")
    if len(term) > MAX_TERM_LENGTH:
        raise ValueError(f"keyterm exceeds {MAX_TERM_LENGTH} characters")
    if term.casefold() in STOP_WORDS:
        raise ValueError("keyterm is a common English stop word")
    return term


def parse_roster(path: Path) -> dict[str, str]:
    """Read the roster's simple email/name pairs without adding a YAML runtime dependency."""
    result: dict[str, str] = {}
    pending_email: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("- email:") or line.startswith("email:"):
            pending_email = line.split(":", 1)[1].strip().strip("'\"").casefold()
        elif pending_email and line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
            if name:
                result[pending_email] = name
            pending_email = None
    return result


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def add_candidate(candidates: list[dict[str, Any]], text: Any, source: str, priority: int) -> None:
    try:
        normalized = normalize_term(text)
    except ValueError:
        return
    candidates.append({"text": normalized, "source": source, "priority": int(priority)})


def select_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        enumerate(candidates),
        key=lambda item: (-item[1]["priority"], SOURCE_ORDER.get(item[1]["source"], 99), item[0]),
    )
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    estimated_tokens = 0
    for _, candidate in ordered:
        folded = candidate["text"].casefold()
        if folded in seen:
            continue
        term_tokens = max(1, (len(candidate["text"]) + 3) // 4)
        if len(selected) >= MAX_TERMS or estimated_tokens + term_tokens > MAX_ESTIMATED_TOKENS:
            continue
        seen.add(folded)
        estimated_tokens += term_tokens
        selected.append(candidate)
    return selected


def validate_context(context: dict[str, Any], schema_path: Path | None = None) -> None:
    required = {"version", "meeting_id", "language", "generated_at", "keyterms", "canonical_entities", "source_fingerprints"}
    if set(context) != required:
        raise ValueError("context shape does not match meeting-stt-context.v1")
    if context["version"] != VERSION or context["language"] != "en":
        raise ValueError("unsupported context version or language")
    if not isinstance(context["meeting_id"], str) or not context["meeting_id"].strip() or len(context["meeting_id"]) > 255:
        raise ValueError("meeting_id is invalid")
    datetime.fromisoformat(context["generated_at"].replace("Z", "+00:00"))
    if len(context["keyterms"]) > MAX_TERMS:
        raise ValueError("too many keyterms")
    seen: set[str] = set()
    estimated_tokens = 0
    for item in context["keyterms"]:
        if set(item) != {"text", "source", "priority"} or item["source"] not in SOURCE_ORDER:
            raise ValueError("invalid keyterm object")
        term = normalize_term(item["text"])
        folded = term.casefold()
        if folded in seen:
            raise ValueError("duplicate keyterm")
        seen.add(folded)
        estimated_tokens += max(1, (len(term) + 3) // 4)
    if estimated_tokens > MAX_ESTIMATED_TOKENS:
        raise ValueError("keyterm budget exceeded")
    for fingerprint in context["source_fingerprints"].values():
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
            raise ValueError("invalid source fingerprint")
    if schema_path is not None:
        try:
            import jsonschema
        except ImportError as exc:
            raise RuntimeError("jsonschema is required to validate meeting-stt-context.v1") from exc
        jsonschema.Draft202012Validator(load_json(schema_path), format_checker=jsonschema.FormatChecker()).validate(context)


def atomic_write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def build_context(args: argparse.Namespace) -> dict[str, Any]:
    event = load_json(args.event_meta) if args.event_meta else {}
    meeting_id = args.meeting_id or event.get("event_id") or event.get("id")
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        raise ValueError("meeting_id is required")
    title = args.event_title if args.event_title is not None else event.get("title", "")
    attendees = event.get("attendees", [])
    if not isinstance(attendees, list):
        raise ValueError("attendees must be an array")

    roster = parse_roster(args.roster) if args.roster and args.roster.exists() else {}
    glossary = load_json(args.glossary)
    if glossary.get("version") != "transcription-glossary.v1" or not isinstance(glossary.get("terms"), list):
        raise ValueError("stable glossary is invalid")

    candidates: list[dict[str, Any]] = []
    people: list[str] = []
    organizations: list[str] = []
    products: list[str] = []
    for attendee in attendees:
        if not isinstance(attendee, dict):
            raise ValueError("attendee entries must be objects")
        email = attendee.get("email")
        supplied_name = attendee.get("name") or attendee.get("displayName")
        canonical_name = roster.get(email.casefold()) if isinstance(email, str) else None
        # The shared roster often stores a short operational display name ("Organizer") while
        # Calendar carries the spoken full name ("Organizer Example"). Use the roster to resolve the
        # identity, but retain the fuller supplied spelling when it clearly contains that canonical
        # display name; ASR vocabulary benefits from the complete spoken form.
        name = canonical_name or supplied_name
        if isinstance(canonical_name, str) and isinstance(supplied_name, str):
            if canonical_name.casefold() in supplied_name.casefold() and len(supplied_name) > len(canonical_name):
                name = supplied_name
        if isinstance(name, str):
            try:
                name = normalize_term(name)
            except ValueError:
                continue
            add_candidate(candidates, name, "calendar_attendee", 100)
            people.append(name)

    title_folded = title.casefold() if isinstance(title, str) else ""
    project_folded = (args.project_id or "").casefold()
    for entry in glossary["terms"]:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        source = entry.get("source")
        if source not in {"organization", "product", "external_organization", "project", "agenda"}:
            continue
        always = entry.get("always") is True
        matches_context = isinstance(text, str) and (text.casefold() in title_folded or text.casefold() in project_folded)
        if always or matches_context:
            add_candidate(candidates, text, source, int(entry.get("priority", 50)))
            if source in {"organization", "external_organization"} and isinstance(text, str):
                organizations.append(text)
            elif source == "product" and isinstance(text, str):
                products.append(text)

    if args.project_id:
        add_candidate(candidates, args.project_id, "project", 92)

    if args.context_input and args.context_input.exists():
        contextual = load_json(args.context_input)
        if contextual.get("version") != "meeting-stt-context-input.v1" or not isinstance(contextual.get("terms"), list):
            raise ValueError("meeting context input is invalid")
        for entry in contextual["terms"]:
            if not isinstance(entry, dict) or entry.get("source") not in SOURCE_ORDER:
                continue
            add_candidate(candidates, entry.get("text"), entry["source"], int(entry.get("priority", 50)))

    if args.corrections and args.corrections.exists():
        corrections = load_json(args.corrections)
        entries = corrections.get("terms", corrections) if isinstance(corrections, dict) else corrections
        if not isinstance(entries, list):
            raise ValueError("correction vocabulary must be an array or contain a terms array")
        for entry in entries:
            if isinstance(entry, str):
                add_candidate(candidates, entry, "human_correction", 80)
            elif isinstance(entry, dict) and entry.get("relevant", True):
                add_candidate(candidates, entry.get("text") or entry.get("replacement"), "human_correction", int(entry.get("priority", 80)))

    selected = select_candidates(candidates)
    event_fingerprint_source = args.event_meta.read_bytes() if args.event_meta else json.dumps({
        "calendar_event_id": args.calendar_event_id,
        "event_title": title,
        "attendees": attendees,
        "project_id": args.project_id,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprints = {
        "calendar_event": sha256_bytes(event_fingerprint_source),
        "stable_glossary": file_fingerprint(args.glossary),
    }
    if args.roster and args.roster.exists():
        fingerprints["staff_registry"] = file_fingerprint(args.roster)
    if args.corrections and args.corrections.exists():
        fingerprints["human_corrections"] = file_fingerprint(args.corrections)
    if args.context_input and args.context_input.exists():
        fingerprints["notion_meeting_context"] = file_fingerprint(args.context_input)

    def unique(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                result.append(value)
        return result

    return {
        "version": VERSION,
        "meeting_id": meeting_id.strip(),
        "language": "en",
        "generated_at": (args.generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
        "keyterms": selected,
        "canonical_entities": {
            "people": unique(people),
            "organizations": unique(organizations),
            "products": unique(products),
        },
        "source_fingerprints": fingerprints,
    }


def parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent.parent
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--meeting-id")
    result.add_argument("--calendar-event-id")
    result.add_argument("--event-title")
    result.add_argument("--event-meta", type=Path)
    result.add_argument("--project-id")
    result.add_argument("--context-input", type=Path)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--glossary", type=Path, default=here / "references" / "transcription-glossary.json")
    result.add_argument("--schema", type=Path, default=here / "references" / "meeting-stt-context.v1.schema.json")
    result.add_argument("--roster", type=Path, default=Path.home() / ".hermes" / "team-roster.yaml")
    result.add_argument("--corrections", type=Path)
    result.add_argument("--generated-at", help=argparse.SUPPRESS)
    return result


def main() -> int:
    args = parser().parse_args()
    context = build_context(args)
    validate_context(context, args.schema)
    output = args.workspace / "transcription.context.json"
    atomic_write_private(output, context)
    print(json.dumps({"path": str(output), "keyterm_count": len(context["keyterms"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
