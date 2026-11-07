#!/usr/bin/env python3
"""Verify an external backup manifest before enabling raw transcription retention."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_STATE_HASHES = (
    "canonical_transcript_sha256",
    "batch_manifest_sha256",
    "raw_signal_sha256",
    "stt_tape_sha256",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


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


def manifest_hashes(manifest: dict[str, Any]) -> set[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("backup manifest artifacts must be an array")
    result = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("backup manifest artifact must be an object")
        value = artifact.get("sha256")
        if isinstance(value, str) and value.startswith("sha256:"):
            result.add(value)
    return result


def verify(state_path: Path, manifest_path: Path) -> dict[str, Any]:
    state = json.loads(state_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("version") != "transcription-backup-manifest.v1":
        raise ValueError("unsupported transcription backup manifest version")
    if not manifest.get("backup_id"):
        raise ValueError("backup manifest has no backup_id")
    backed_up = manifest_hashes(manifest)
    required = {state[field] for field in REQUIRED_STATE_HASHES if state.get(field)}
    if not state.get("canonical_transcript_sha256"):
        raise ValueError("state has no canonical transcript hash")
    missing = sorted(required - backed_up)
    if missing:
        raise ValueError(f"backup manifest is missing {len(missing)} required artifact hash(es)")
    state.update({
        "backup_id": str(manifest["backup_id"]),
        "backup_manifest_path": str(manifest_path),
        "backup_manifest_sha256": digest(manifest_path),
        "backup_verified_at": now(),
        "updated_at": now(),
    })
    atomic_json(state_path, state)
    return {
        "state_path": str(state_path),
        "backup_id": state["backup_id"],
        "verified_artifact_count": len(required),
        "backup_manifest_sha256": state["backup_manifest_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.state, args.manifest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
