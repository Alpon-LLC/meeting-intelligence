#!/usr/bin/env python3
"""Atomically advance transcription.state.json and enforce the finalization terminal-state gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = frozenset({"completed", "failed_nonretryable", "retry_exhausted", "disabled"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{value}"


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "transcription.state.v1", "live_status": "pending", "batch_status": "pending", "canonical_status": "pending"}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("transcription state must be an object")
    return value


def finalization_ready(state: dict[str, Any]) -> bool:
    return all(state.get(field) in TERMINAL for field in ("live_status", "batch_status", "canonical_status"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--live-status", choices=sorted(TERMINAL | {"pending", "running"}))
    parser.add_argument("--batch-status", choices=sorted(TERMINAL | {"pending", "running"}))
    parser.add_argument("--canonical-status", choices=sorted(TERMINAL | {"pending", "running"}))
    parser.add_argument("--context", type=Path)
    parser.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--raw-transcript", type=Path)
    parser.add_argument("--canonical-transcript", type=Path)
    parser.add_argument("--raw-signal", type=Path)
    parser.add_argument("--stt-tape", type=Path)
    parser.add_argument("--meeting-end-at")
    parser.add_argument("--check-ready", action="store_true")
    args = parser.parse_args()

    state = load(args.state)
    for field in ("live_status", "batch_status", "canonical_status"):
        value = getattr(args, field)
        if value:
            state[field] = value
    if args.context:
        state["context_path"] = str(args.context)
        state["context_sha256"] = digest(args.context)
    if args.batch_manifest:
        state["batch_manifest_path"] = str(args.batch_manifest)
        state["batch_manifest_sha256"] = digest(args.batch_manifest)
    if args.raw_transcript:
        state["raw_transcript_path"] = str(args.raw_transcript)
        state["raw_transcript_sha256"] = digest(args.raw_transcript)
    if args.canonical_transcript:
        state["canonical_transcript_path"] = str(args.canonical_transcript)
        state["canonical_transcript_sha256"] = digest(args.canonical_transcript)
    if args.raw_signal:
        state["raw_signal_path"] = str(args.raw_signal)
        state["raw_signal_sha256"] = digest(args.raw_signal) if args.raw_signal.is_file() else None
    if args.stt_tape:
        state["stt_tape_path"] = str(args.stt_tape)
        state["stt_tape_sha256"] = digest(args.stt_tape) if args.stt_tape.is_file() else None
    if args.meeting_end_at:
        datetime.fromisoformat(args.meeting_end_at.replace("Z", "+00:00"))
        state["meeting_end_at"] = args.meeting_end_at
    state["finalization_ready"] = finalization_ready(state)
    if state["finalization_ready"]:
        state["completed_at"] = state.get("completed_at") or now()
    state["updated_at"] = now()
    atomic_json(args.state, state)
    print(json.dumps({"path": str(args.state), "finalization_ready": state["finalization_ready"]}))
    if args.check_ready and not state["finalization_ready"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
