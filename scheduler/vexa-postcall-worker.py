#!/usr/bin/env python3
"""Prepare canonical evidence outside the bounded LLM publication dispatch."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import shutil
import signal
from pathlib import Path
import subprocess
import tempfile


def atomic(path, payload):
    fd, tmp = tempfile.mkstemp(prefix=".postcall-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def ready(state):
    return (state.get("finalization_ready") is True
            and all(state.get(k) in {"completed", "disabled", "failed_nonretryable", "retry_exhausted"}
                    for k in ["live_status", "batch_status", "canonical_status"])
            and state.get("readability_status") in {"completed", "completed_degraded", "canonical_fallback", "disabled"})


def materialized_tape(directory, lifecycle):
    manifest = read(directory / ".vexa-signal-ready.json")
    if (manifest.get("version") != "vexa-signal-ready.v1" or manifest.get("complete") is not True
            or str(manifest.get("meeting_id")) != str(lifecycle.get("id"))
            or manifest.get("native_meeting_id") != lifecycle.get("native_meeting_id")):
        return None
    captures = [item for item in manifest.get("files", []) if isinstance(item, dict)
                and isinstance(item.get("name"), str) and item["name"].endswith(".captured-signal.jsonl")]
    if len(captures) != 1:
        raise ValueError("signal manifest capture count needs review")
    item = captures[0]
    tape = (directory / item["name"]).resolve()
    if tape.parent != directory.resolve() or not tape.is_file() or tape.is_symlink():
        raise ValueError("materialized capture is unsafe or missing")
    if tape.stat().st_size != item.get("size"):
        raise ValueError("materialized capture size mismatch")
    hasher = hashlib.sha256()
    with tape.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    digest = "sha256:" + hasher.hexdigest()
    if digest != item.get("sha256"):
        raise ValueError("materialized capture hash mismatch")
    return tape


PROMOTED_ARTIFACTS = (
    "transcript.canonical.jsonl",
    "transcript.batch.normalized.jsonl",
    "transcript.batch.manifest.json",
    "transcript.readable.md",
    "transcript-readable.md",
    "transcript.edited.md",
    "transcript.edited.v1.jsonl",
    "transcript.edit-ledger.jsonl",
    "transcript.corrections.json",
    "transcript.language-selection.json",
    "transcript.editor.manifest.json",
    "transcript.readability.json",
)


def capture_is_empty(tape):
    """True only for a header-only captured-signal tape.

    Anything that is not recognisably a captured-signal tape returns False, so an
    unknown file is never treated as an empty recording.
    """
    with tape.open() as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except ValueError:
            return False
        if not isinstance(header, dict) or header.get("type") != "captured_signal_header":
            return False
        return not handle.readline().strip()


def file_digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def promotable_archive(directory, native_meeting_id):
    """Newest preserved capture of this meeting that already has a finished transcript.

    A bounded rejoin archives the source capture (tape, STT rows and transcript) and
    then starts a replacement bot. When the replacement captures nothing - the room
    was already over - the finished transcript is the only real evidence and must be
    published instead of being reprocessed from an empty tape.
    """
    root = directory / "captures"
    if not root.is_dir():
        return None
    best = None
    for archive in sorted(root.glob("vexa-meeting-*")):
        if not archive.is_dir() or archive.is_symlink():
            continue
        canonical = archive / "transcript.canonical.jsonl"
        state = read(archive / "transcription.state.json")
        lifecycle = read(archive / "vexa.lifecycle.json").get("meeting") or {}
        if lifecycle.get("native_meeting_id") != native_meeting_id:
            continue
        if not canonical.is_file() or canonical.is_symlink() or canonical.stat().st_size == 0:
            continue
        if state.get("canonical_status") != "completed" or state.get("finalization_ready") is not True:
            continue
        if not (archive / "transcript-readable.md").is_file():
            continue
        stamp = archive.stat().st_mtime
        if best is None or stamp > best[0]:
            best = (stamp, archive, state)
    return best


def promote_archive(directory, archive, source_state, bot_id):
    """Copy a finished transcript from a preserved capture into the event root.

    Provenance keeps both identities: the transcript came from the archived
    occurrence, while the event's current occurrence stays the replacement one so
    the finish gate's signal manifest still matches.
    """
    files = []
    for name in PROMOTED_ARTIFACTS:
        source = archive / name
        if not source.is_file() or source.is_symlink():
            continue
        target = directory / name
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        files.append({"name": name, "size": target.stat().st_size, "sha256": file_digest(target)})
    state = dict(source_state)
    state.update({
        "vexa_bot_id": bot_id,
        "finalization_ready": True,
        "promoted_from_meeting_id": str(source_state.get("vexa_bot_id")),
        "promotion_archive": str(archive),
        "promoted_at": now(),
    })
    manifest = {
        "version": "transcript.capture-promotion.v1",
        "bot_id": bot_id,
        "promoted_from_meeting_id": str(source_state.get("vexa_bot_id")),
        "archive": str(archive),
        "reason": "replacement-capture-empty",
        "files": files,
        "created_at": now(),
    }
    return state, manifest


def run_finalizer(command, timeout, lock_fd=None):
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                          pass_fds=() if lock_fd is None else (lock_fd,)) as process:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the full processing group, not just the Python wrapper, so
            # a retry cannot overlap an orphaned provider/editor subprocess.
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return 124


def prepare(directory, finalizer, timeout=600):
    # All scheduler ticks share this kernel lock. A crash releases it without
    # requiring stale PID guessing or concurrent provider calls.
    with (directory / ".vexa-postcall.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        state_path = directory / "transcription.state.json"
        state = read(state_path)
        if ready(state):
            return "already_ready"
        lifecycle = read(directory / "vexa.lifecycle.json").get("meeting", {})
        if str(lifecycle.get("id")) != str(state.get("vexa_bot_id")) or lifecycle.get("status") not in {"completed", "failed"}:
            return "waiting_terminal"
        try:
            tape = materialized_tape(directory, lifecycle)
        except ValueError:
            receipt = directory / ".vexa-postcall.json"
            atomic(receipt, {"status": "exhausted", "attempt": 3, "reason": "signal-manifest-needs-review", "updated_at": now()})
            return "exhausted"
        if tape is None:
            return "waiting_signal"
        receipt = directory / ".vexa-postcall.json"
        if capture_is_empty(tape):
            # A bounded rejoin that entered an already-finished room records only a
            # header. Publishing that would emit an empty transcript and discard the
            # real one, so promote the preserved finished capture instead.
            source = promotable_archive(directory, lifecycle.get("native_meeting_id"))
            if source is None:
                atomic(receipt, {"status": "exhausted", "attempt": 3,
                                 "reason": "empty-capture-needs-review", "updated_at": now()})
                return "exhausted"
            _, archive, source_state = source
            promoted_state, manifest = promote_archive(directory, archive, source_state,
                                                       str(lifecycle.get("id")))
            atomic(state_path, promoted_state)
            atomic(directory / "transcript.capture-promotion.json", manifest)
            atomic(receipt, {"status": "completed", "promoted_from": manifest["promoted_from_meeting_id"],
                             "reason": "empty-replacement-promoted-preserved-capture",
                             "updated_at": now()})
            return "promoted"
        previous = read(receipt)
        attempt = int(previous.get("attempt", 0)) + 1
        if attempt > 3:
            atomic(receipt, {"status": "exhausted", "attempt": 3, "reason": "worker-attempts-exhausted", "updated_at": now()})
            return "exhausted"
        atomic(receipt, {"status": "running", "attempt": attempt, "started_at": now()})
        # This is the event-scoped bridge directory, not a shared recurring-URL
        # directory. Multiple tapes require explicit provenance-aware recovery.
        tapes = list(directory.glob("*.captured-signal.jsonl"))
        if len(tapes) != 1 or tapes[0].resolve() != tape:
            atomic(receipt, {"status": "exhausted", "attempt": 3, "reason": "capture-count-needs-review", "updated_at": now()})
            return "exhausted"
        try:
            if tape.resolve().parent != directory.resolve():
                raise ValueError("capture outside event directory")
            with tape.open() as f:
                header = json.loads(f.readline())
            if header.get("type") != "captured_signal_header" or header.get("v") != 1 or header.get("native_meeting_id") != lifecycle.get("native_meeting_id"):
                raise ValueError("capture identity mismatch")
            command = ["python3", str(finalizer), "--signal-tape", str(tape), "--context", str(directory / "transcription.context.json"),
                       "--output-dir", str(directory), "--state", str(state_path),
                       "--lifecycle-metadata", str(directory / "vexa.lifecycle.json")]
            if lifecycle.get("end_time"):
                command += ["--meeting-end-at", lifecycle["end_time"]]
            live = tape.with_name(tape.name.replace(".captured-signal.jsonl", ".transcript.jsonl"))
            if live.is_file():
                command += ["--live-transcript", str(live)]
            returncode = run_finalizer(command, timeout, lock.fileno())
            result = "completed" if returncode == 0 and ready(read(state_path)) else "failed"
        except (OSError, ValueError, subprocess.TimeoutExpired):
            result = "failed"
        status = "exhausted" if result == "failed" and attempt == 3 else result
        atomic(receipt, {"status": status, "attempt": attempt, "updated_at": now()})
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting-dir", type=Path, required=True)
    args = parser.parse_args()
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    directory = args.meeting_dir.resolve()
    if directory.parent != (home / "workspace/meetings").resolve():
        parser.error("meeting directory outside canonical workspace")
    print(prepare(directory, home / "skills/meeting/meeting-finalize/scripts/finalize_vexa_transcription.py"))


if __name__ == "__main__":
    main()
