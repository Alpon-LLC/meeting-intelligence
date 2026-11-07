#!/usr/bin/env python3
"""Prune expired Vexa raw artifacts only after canonical and backup verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def now() -> datetime:
    return datetime.now(timezone.utc)


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def atomic_json(path: Path, value: dict[str, Any]) -> None:
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


def validate_state(state_path: Path, state: dict[str, Any]) -> list[str]:
    reasons = []
    if state.get("canonical_status") != "completed":
        reasons.append("canonical_not_completed")
    canonical = Path(state.get("canonical_transcript_path", ""))
    if not canonical.is_file():
        reasons.append("canonical_missing")
    elif state.get("canonical_transcript_sha256") != sha256(canonical):
        reasons.append("canonical_hash_mismatch")
    if not state.get("backup_verified_at") or not state.get("backup_manifest_sha256"):
        reasons.append("backup_not_verified")
    if not inside(canonical, state_path.parent):
        reasons.append("canonical_outside_workspace")
    return reasons


def candidates(state_path: Path, state: dict[str, Any], raw_root: Path) -> tuple[list[Path], list[str]]:
    result = []
    reasons = []
    for field, root in (("raw_signal_path", raw_root), ("stt_tape_path", state_path.parent)):
        value = state.get(field)
        if not value:
            continue
        path = Path(value)
        if "replay-baselines" in path.parts:
            reasons.append(f"{field}_immutable_replay")
        elif not inside(path, root):
            reasons.append(f"{field}_outside_approved_root")
        elif path.exists():
            result.append(path)
    return result, reasons


def process(state_path: Path, raw_root: Path, cutoff: datetime, apply: bool) -> dict[str, Any]:
    state = json.loads(state_path.read_text())
    reasons = validate_state(state_path, state)
    paths, path_reasons = candidates(state_path, state, raw_root)
    reasons.extend(path_reasons)
    for path in paths:
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified > cutoff:
            reasons.append(f"not_expired:{path.name}")
    outcome = {
        "state_path": str(state_path),
        "eligible": not reasons and bool(paths),
        "apply": apply,
        "paths": [str(path) for path in paths],
        "reasons": reasons,
    }
    if outcome["eligible"] and apply:
        removed = []
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        outcome["removed"] = removed
        outcome["removed_at"] = now().isoformat().replace("+00:00", "Z")
        atomic_json(state_path.parent / "transcription.retention.json", outcome)
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.retention_days < 1:
        parser.error("retention-days must be at least 1")
    cutoff = now() - timedelta(days=args.retention_days)
    outcomes = [
        process(path, args.raw_root, cutoff, args.apply)
        for path in sorted(args.workspace_root.glob("*/transcription.state.json"))
    ]
    print(json.dumps({
        "apply": args.apply,
        "meeting_count": len(outcomes),
        "eligible_count": sum(item["eligible"] for item in outcomes),
        "outcomes": outcomes,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
