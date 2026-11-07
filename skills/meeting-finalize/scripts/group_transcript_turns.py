#!/usr/bin/env python3
"""Convert segment-level JSONL into a readable speaker-turn Markdown transcript."""
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

DANGLING_END = re.compile(r"\b(and|but|because|or|so|then|that|which|to)\s*$", re.IGNORECASE)

def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def elapsed(value: datetime, origin: datetime) -> str:
    seconds = max(0, int((value - origin).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def display_speaker(value: str) -> str:
    speaker = value.strip() or "Unknown"
    letters = "".join(character for character in speaker if character.isalpha())
    if letters and (letters.islower() or letters.isupper()):
        return speaker.title()
    return speaker


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def group_turns(rows: list[dict[str, Any]], max_gap_seconds: float = 45.0) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    ordered = sorted(enumerate(rows), key=lambda item: parse_time(str(item[1]["ts"])))
    for source_index, row in ordered:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = parse_time(str(row["ts"]))
        speaker = str(row.get("speaker") or "Unknown").strip() or "Unknown"
        if turns and turns[-1]["speaker"] == speaker and (start - turns[-1]["end"]).total_seconds() <= max_gap_seconds:
            turns[-1]["text"] = f"{turns[-1]['text']} {text}"
            turns[-1]["end"] = start
            turns[-1]["segments"] += 1
            turns[-1]["source_segment_ids"].append(str(row.get("segment_id") or f"row:{source_index}"))
            turns[-1]["source_segment_sha256"].append(hashlib.sha256(text.encode()).hexdigest())
            turns[-1]["segment_ids"].append(str(row.get("segment_id") or row.get("id") or f"row:{source_index}"))
        else:
            turns.append({
                "speaker": speaker, "start": start, "end": start, "text": text, "segments": 1,
                "source_segment_ids": [str(row.get("segment_id") or f"row:{source_index}")],
                "source_segment_sha256": [hashlib.sha256(text.encode()).hexdigest()],
                "segment_ids": [str(row.get("segment_id") or row.get("id") or f"row:{source_index}")],
            })
    return turns


def render(
    turns: list[dict[str, Any]],
    title: str = "Readable transcript",
    note: str | None = None,
    origin: str | datetime | None = None,
) -> str:
    if not turns:
        return f"# {title}\n\n_No transcript segments._\n"
    elapsed_origin = parse_time(origin) if isinstance(origin, str) else (origin or turns[0]["start"])
    lines = [f"# {title}", ""]
    if note:
        lines.extend([f"> {note}", ""])
    for index, turn in enumerate(turns):
        start = elapsed(turn["start"], elapsed_origin)
        speaker = display_speaker(str(turn["speaker"]))
        text = compact_text(str(turn["text"]))
        if index == len(turns) - 1 and DANGLING_END.search(text):
            text += "…"
        lines.append(f"[{start}] {speaker}: {text}  ")
    return "\n".join(lines).rstrip() + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-gap-seconds", type=float, default=45.0)
    parser.add_argument("--origin", help="ISO timestamp used as elapsed-time zero")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    turns = group_turns(rows, args.max_gap_seconds)
    atomic_write(args.output, render(turns, origin=args.origin))
    print(json.dumps({"status": "completed", "segments": len(rows), "turns": len(turns), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
