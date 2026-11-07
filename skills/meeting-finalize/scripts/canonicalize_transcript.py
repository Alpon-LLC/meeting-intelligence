#!/usr/bin/env python3
"""Apply only canonical-entity and human-confirmed alias corrections with a complete ledger."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def canonical_index(context: dict[str, Any]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    entities = context.get("canonical_entities") or {}
    for category, source in (("people", "calendar_attendee"), ("organizations", "organization"), ("products", "product")):
        for value in entities.get(category, []):
            if isinstance(value, str) and value.strip():
                result[value.casefold()] = (value.strip(), source)
    return result


def load_aliases(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    value = json.loads(path.read_text())
    entries = value.get("aliases", value) if isinstance(value, dict) else value
    if not isinstance(entries, list):
        raise ValueError("aliases must be an array or contain an aliases array")
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("original"), str) or not isinstance(entry.get("replacement"), str):
            raise ValueError("each alias requires string original and replacement fields")
        result.append({
            "original": entry["original"],
            "replacement": entry["replacement"],
            "reason": str(entry.get("reason") or "human-confirmed ASR alias"),
            "context_source": str(entry.get("context_source") or "human_correction"),
        })
    return result


def correct_rows(rows: list[dict[str, Any]], context: dict[str, Any], aliases: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    canonical = canonical_index(context)
    rules: list[dict[str, str]] = []
    for folded, (replacement, source) in canonical.items():
        rules.append({"original": replacement, "replacement": replacement, "reason": "canonical capitalization", "context_source": source})
    rules.extend(aliases)
    rules.sort(key=lambda rule: len(rule["original"]), reverse=True)

    applied = []
    review = []
    output = []
    for row in rows:
        updated = dict(row)
        text = str(row.get("text", ""))
        for rule in rules:
            original = rule["original"]
            pattern = re.compile(rf"(?<!\w){re.escape(original)}(?!\w)", re.IGNORECASE)
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            replacement_entry = canonical.get(rule["replacement"].casefold())
            if replacement_entry is None:
                review.append({"segment_id": row.get("segment_id"), **rule, "reason": "replacement is not a canonical entity"})
                continue
            replacement, canonical_source = replacement_entry
            matches = [match for match in matches if match.group(0) != replacement]
            if not matches:
                continue
            if any(char.isdigit() for char in original + replacement):
                review.append({"segment_id": row.get("segment_id"), **rule, "reason": "automatic number-bearing correction is forbidden"})
                continue
            for match in reversed(matches):
                observed = match.group(0)
                if observed == replacement:
                    continue
                text = text[:match.start()] + replacement + text[match.end():]
                applied.append({
                    "segment_id": row.get("segment_id"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "speaker": row.get("speaker"),
                    "original": observed,
                    "replacement": replacement,
                    "reason": rule["reason"],
                    "context_source": rule.get("context_source") or canonical_source,
                    "confidence": 1.0,
                    "automatic": True,
                })
        updated["text"] = text
        output.append(updated)
    return output, {"version": "transcript.corrections.v1", "applied": applied, "review": review}


def readable_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Meeting transcript", ""]
    current_speaker = None
    for row in rows:
        speaker = row.get("speaker") or "Unknown"
        if speaker != current_speaker:
            lines.extend([f"## {speaker}", ""])
            current_speaker = speaker
        lines.append(str(row.get("text", "")).strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.normalized.read_text().splitlines() if line]
    context = json.loads(args.context.read_text())
    canonical, ledger = correct_rows(rows, context, load_aliases(args.aliases))
    atomic_write(args.output_dir / "transcript.canonical.jsonl", b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in canonical
    ))
    atomic_write(args.output_dir / "transcript.readable.md", readable_markdown(canonical).encode())
    atomic_write(args.output_dir / "transcript.corrections.json", (json.dumps(ledger, indent=2, ensure_ascii=False) + "\n").encode())
    print(json.dumps({"segments": len(canonical), "corrections": len(ledger["applied"]), "review": len(ledger["review"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
