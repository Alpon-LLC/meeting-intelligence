#!/usr/bin/env python3
"""Locate the newest immutable Vexa signal tape for one native meeting ID."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_header(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.loads(handle.readline())
        if value.get("type") != "captured_signal_header" or value.get("v") != 1:
            return None
        return value
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def locate(root: Path, meeting_id: str, started_after: datetime | None = None, started_before: datetime | None = None) -> dict[str, Any]:
    matches = []
    for path in root.glob("*.captured-signal.jsonl"):
        header = read_header(path)
        if not header or header.get("native_meeting_id") != meeting_id:
            continue
        started = timestamp(str(header.get("started_at")))
        if started_after and started < started_after:
            continue
        if started_before and started > started_before:
            continue
        matches.append((started, path, header))
    if not matches:
        raise FileNotFoundError("no captured signal tape matches the requested meeting and time window")
    started, path, header = max(matches, key=lambda item: item[0])
    trace_id = str(header["trace_id"])
    siblings = {
        "signal_tape": path,
        "live_transcript": root / f"{trace_id}.transcript.jsonl",
        "stt_tape": root / f"{trace_id}.stt.jsonl",
        "bot_log": root / f"{trace_id}.botlog.txt",
    }
    return {
        "version": "vexa-signal-locator.v1",
        "native_meeting_id": meeting_id,
        "trace_id": trace_id,
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "signal_tape": str(path),
        "live_transcript": str(siblings["live_transcript"]) if siblings["live_transcript"].is_file() else None,
        "stt_tape": str(siblings["stt_tape"]) if siblings["stt_tape"].is_file() else None,
        "bot_log": str(siblings["bot_log"]) if siblings["bot_log"].is_file() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--started-after")
    parser.add_argument("--started-before")
    args = parser.parse_args()
    result = locate(
        args.capture_root,
        args.meeting_id,
        timestamp(args.started_after) if args.started_after else None,
        timestamp(args.started_before) if args.started_before else None,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
