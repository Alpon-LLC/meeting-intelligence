#!/usr/bin/env python3
"""Merge an early fallback transcript with a later canonical Vexa transcript."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def merge(fallback: list[dict[str, Any]], canonical: list[dict[str, Any]], cutover: datetime) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for index, row in enumerate(fallback):
        when = parse_time(str(row["ts"]))
        if when >= cutover:
            continue
        merged.append({
            "segment_id": f"fallback:{index}",
            "ts": when.isoformat().replace("+00:00", "Z"),
            "speaker": row.get("speaker") or "Unknown",
            "text": str(row.get("text") or "").strip(),
            "source": "legacy_google_captions_fallback",
        })
    for row in canonical:
        copied = dict(row)
        copied["source"] = copied.get("source") or "deepgram_batch_nova3"
        merged.append(copied)
    return sorted(merged, key=lambda row: parse_time(str(row["ts"])))


def markdown(rows: list[dict[str, Any]], cutover: datetime) -> str:
    lines = [
        "# Meeting transcript",
        "",
        f"> Legacy Google captions are used only before {cutover.isoformat().replace('+00:00', 'Z')}; "
        "the remainder is the canonical Vexa/Deepgram batch transcript.",
        "",
    ]
    for row in rows:
        when = parse_time(str(row["ts"]))
        lines.append(f"**{when.strftime('%H:%M:%S')} · {row.get('speaker') or 'Unknown'}**")
        lines.append("")
        lines.append(str(row.get("text") or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fallback", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--cutover")
    args = parser.parse_args()

    fallback = read_jsonl(args.fallback)
    canonical = read_jsonl(args.canonical)
    if not canonical:
        raise SystemExit("canonical transcript is empty")
    cutover = parse_time(args.cutover) if args.cutover else min(parse_time(str(row["ts"])) for row in canonical)
    merged = merge(fallback, canonical, cutover)
    atomic_write(args.output_jsonl, "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in merged))
    atomic_write(args.output_markdown, markdown(merged, cutover))
    print(json.dumps({
        "status": "completed",
        "cutover": cutover.isoformat().replace("+00:00", "Z"),
        "fallback_segments": sum(row.get("source") == "legacy_google_captions_fallback" for row in merged),
        "canonical_segments": sum(row.get("source") != "legacy_google_captions_fallback" for row in merged),
        "total_segments": len(merged),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
