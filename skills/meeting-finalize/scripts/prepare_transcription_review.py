#!/usr/bin/env python3
"""Export a canonical transcript for human review or import the reviewed TSV."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


FIELDS = [
    "row",
    "start",
    "end",
    "audio_start",
    "audio_end",
    "speaker",
    "text",
    "mean_confidence",
    "review_priority",
    "review_reason",
    "human_speaker",
    "human_text",
    "overlap",
    "reviewer_notes",
]

NUMBER_OR_DATE = re.compile(r"(?:\$\s?\d|\b\d{1,4}(?:[-/]\d{1,2}){1,2}\b|\b\d)")
SUSPICIOUS_TERM = re.compile(
    r"\b(?:publicity|complexity|chat gbt|claw|agent sack|xydez|nine nine router|four cloud)\b",
    re.IGNORECASE,
)


def review_priority(item: dict[str, Any]) -> tuple[str, str]:
    reasons = []
    confidence = item.get("mean_confidence")
    if item.get("speaker") == "Unknown":
        reasons.append("unknown speaker")
    if isinstance(confidence, (int, float)) and confidence < 0.6:
        reasons.append("very low confidence")
    if SUSPICIOUS_TERM.search(item.get("text", "")):
        reasons.append("possible technical-term error")
    if reasons:
        return "high", "; ".join(reasons)
    if isinstance(confidence, (int, float)) and confidence < 0.8:
        reasons.append("low confidence")
    if NUMBER_OR_DATE.search(item.get("text", "")):
        reasons.append("number/date")
    return ("medium", "; ".join(reasons)) if reasons else ("normal", "")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ValueError(f"invalid transcript row {number}")
        rows.append(value)
    return rows


def private_atomic_text(path: Path, write: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def export_review(transcript: Path, output: Path, audio_origin: float = 0.0) -> int:
    rows = read_jsonl(transcript)

    def write(handle: Any) -> None:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for index, item in enumerate(rows, 1):
            priority, reason = review_priority(item)
            writer.writerow(
                {
                    "row": index,
                    "start": item.get("start", ""),
                    "end": item.get("end", ""),
                    "audio_start": round(float(item.get("start", 0)) - audio_origin, 3),
                    "audio_end": round(float(item.get("end", item.get("start", 0))) - audio_origin, 3),
                    "speaker": item.get("speaker", "Unknown"),
                    "text": item["text"],
                    "mean_confidence": item.get("mean_confidence", ""),
                    "review_priority": priority,
                    "review_reason": reason,
                    "human_speaker": item.get("speaker", "Unknown"),
                    "human_text": item["text"],
                    "overlap": "true" if item.get("overlap") is True else "false",
                    "reviewer_notes": "",
                }
            )

    private_atomic_text(output, write)
    return len(rows)


def import_review(review: Path, output: Path) -> int:
    result = []
    with review.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"review TSV missing columns: {', '.join(sorted(missing))}")
        for number, item in enumerate(reader, 2):
            text = (item.get("human_text") or "").strip()
            speaker = (item.get("human_speaker") or "").strip()
            if not text:
                raise ValueError(f"review row {number} has empty human_text")
            if not speaker:
                raise ValueError(f"review row {number} has empty human_speaker")
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"review row {number} has invalid timing") from exc
            if end < start:
                raise ValueError(f"review row {number} ends before it starts")
            result.append(
                {
                    "start": start,
                    "end": end,
                    "speaker": speaker,
                    "text": text,
                    "overlap": (item.get("overlap") or "").strip().casefold() == "true",
                }
            )

    def write(handle: Any) -> None:
        for item in result:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    private_atomic_text(output, write)
    return len(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--transcript", required=True, type=Path)
    export_parser.add_argument("--output", required=True, type=Path)
    export_parser.add_argument("--audio-origin", type=float, default=0.0)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--review", required=True, type=Path)
    import_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    count = export_review(args.transcript, args.output, args.audio_origin) if args.command == "export" else import_review(args.review, args.output)
    print(json.dumps({"status": "ok", "command": args.command, "rows": count, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
