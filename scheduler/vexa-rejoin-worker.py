#!/usr/bin/env python3
"""Preserve a terminal Vexa capture and make one bounded meeting rejoin attempt."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone


MAX_REJOINS = 1
REJOIN_OVERRUN_GRACE = timedelta(hours=2)
# The producer writes a frame only when audio rises above its silence gate, so a long
# run of missing frames means the room went quiet. A rejoin after the calendar end is
# only useful if the capture was still receiving audio shortly before it stopped.
DORMANT_GAP_SECONDS = int(os.getenv("VEXA_REJOIN_DORMANT_SECONDS", "600"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def event_end(event: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(event.get("end") or "").replace("Z", "+00:00"))
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def terminal_time(lifecycle: dict) -> datetime | None:
    meeting = lifecycle.get("meeting") if isinstance(lifecycle.get("meeting"), dict) else {}
    for value in (meeting.get("end_time"), meeting.get("updated_at"), meeting.get("created_at")):
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    return None


def last_frame_time(directory: Path) -> datetime | None:
    """Timestamp of the newest captured frame, or None when it cannot be established.

    Frames are only written for non-silent audio, so this is the last moment the room
    was producing sound.
    """
    tapes = sorted(directory.glob("*.captured-signal.jsonl"))
    if len(tapes) != 1:
        return None
    newest = None
    try:
        with tapes[0].open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 512 * 1024))
            if size > 512 * 1024:
                handle.readline()
            for line in handle:
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                stamp = value.get("ts") if isinstance(value, dict) else None
                if isinstance(stamp, (int, float)) and stamp > (newest or 0):
                    newest = stamp
    except OSError:
        return None
    if newest is None:
        return None
    return datetime.fromtimestamp(newest / 1000, timezone.utc)


def room_dormant(directory: Path, lifecycle: dict, after_end: bool) -> bool:
    """True only with positive evidence that the room stopped producing audio.

    Applies after the calendar end only: a mid-meeting exit always stays eligible.
    Unreadable evidence fails open, and so does a size-capped capture, whose missing
    tail means the writer stopped rather than the room going quiet.
    """
    if not after_end:
        return False
    if read(directory / ".vexa-signal-ready.json").get("capped") is True:
        return False
    stopped = terminal_time(lifecycle)
    last_audio = last_frame_time(directory)
    if stopped is None or last_audio is None:
        return False
    return (stopped - last_audio).total_seconds() >= DORMANT_GAP_SECONDS


def terminal_rejoin_candidate(lifecycle: dict) -> tuple[str | None, bool]:
    meeting = lifecycle.get("meeting") if isinstance(lifecycle.get("meeting"), dict) else {}
    meeting_id = meeting.get("id")
    reason = meeting.get("completion_reason") or (meeting.get("data") or {}).get("completion_reason")
    return (
        str(meeting_id) if meeting_id is not None else None,
        meeting.get("status") == "completed" and reason in {"left_alone", "evicted"},
    )


def archive_capture(directory: Path, meeting_id: str) -> Path:
    archive = directory / "captures" / f"vexa-meeting-{meeting_id}"
    archive.mkdir(parents=True, exist_ok=False, mode=0o700)
    exact = {
        "vexa.lifecycle.json", "vexa.bot.response.json", "status.json", "transcription.state.json",
        ".vexa-signal-ready.json", ".vexa-postcall.json", ".vexa-postcall.lock",
        ".review-flag.txt", ".finalizing",
    }
    for child in list(directory.iterdir()):
        if child.name in {"captures", "event_meta.json", "transcription.context.json", "transcription.context.input.json"}:
            continue
        if child.name in exact or child.name.startswith("transcript.") or child.name.startswith("transcript-") \
                or child.name.endswith(".captured-signal.jsonl") or child.name.endswith(".stt.jsonl") \
                or child.name == "provider-responses":
            shutil.move(str(child), str(archive / child.name))
    return archive


def run(directory: Path, home: Path, runner=subprocess.run) -> str:
    # Serialize the checkpoint check/archive with publication's existing lock.
    # Otherwise a page could be created between the check and moving transcripts.
    with (directory / ".vexa-publication.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "publication-in-progress"
        return run_locked(directory, home, runner)


def run_locked(directory: Path, home: Path, runner=subprocess.run) -> str:
    receipt_path = directory / ".vexa-rejoin.json"
    receipt = read(receipt_path)
    handled = [str(item) for item in receipt.get("handled_meeting_ids", [])]
    end = event_end(read(directory / "event_meta.json"))
    if end is None or datetime.now(timezone.utc) >= end + REJOIN_OVERRUN_GRACE:
        return "rejoin-window-ended"

    lifecycle = read(directory / "vexa.lifecycle.json")
    meeting = lifecycle.get("meeting") or {}
    reason = receipt.get("source_completion_reason") if receipt.get("status") == "dispatch_pending" else \
        meeting.get("completion_reason") or (meeting.get("data") or {}).get("completion_reason")
    after_end = datetime.now(timezone.utc) >= end
    if after_end and reason != "evicted":
        return "left-alone-after-end"
    if any((directory / name).exists() for name in
           (".finalized", "triage-context.json", "notion-page.json", ".vexa-publication.json")):
        return "publication-exists-needs-review"

    if receipt.get("status") == "dispatch_pending":
        meeting_id = str(receipt.get("source_meeting_id"))
    else:
        meeting_id, eligible = terminal_rejoin_candidate(read(directory / "vexa.lifecycle.json"))
        if not eligible or meeting_id is None:
            return "not-eligible"
        if meeting_id in handled:
            return "already-handled"
        if len(handled) >= MAX_REJOINS:
            return "limit-reached"
        if read(directory / ".vexa-postcall.json").get("status") != "completed":
            return "capture-not-preserved"
        if room_dormant(directory, read(directory / "vexa.lifecycle.json"), after_end):
            # Record the decision so the scheduler stops re-checking this event.
            receipt.update({"status": "skipped", "reason": "room-dormant-no-rejoin", "updated_at": now()})
            atomic(receipt_path, receipt)
            return "room-dormant-no-rejoin"
        archive = archive_capture(directory, meeting_id)
        handled.append(meeting_id)
        receipt = {
            "status": "dispatch_pending", "source_meeting_id": meeting_id, "source_completion_reason": reason,
            "handled_meeting_ids": handled, "archive": str(archive), "updated_at": now(),
        }
        atomic(receipt_path, receipt)

    # Archiving may cross the boundary; recheck immediately before dispatch too.
    dispatch_at = datetime.now(timezone.utc)
    if dispatch_at >= end + REJOIN_OVERRUN_GRACE:
        return "rejoin-window-ended"
    if dispatch_at >= end and receipt.get("source_completion_reason") != "evicted":
        return "left-alone-after-end"
    script = home / "skills/meeting/meeting-auto-join/scripts/start-vexa-context-canary.py"
    result = runner([
        "python3", str(script), "--event-meta", str(directory / "event_meta.json"),
        "--workspace", str(directory),
    ], capture_output=True, text=True, timeout=300)
    if result.returncode == 75:
        return "auth-session-busy"
    if result.returncode != 0:
        receipt.update({"status": "needs_review", "exit_code": result.returncode, "updated_at": now()})
        atomic(receipt_path, receipt)
        return "needs-review"
    response = read(directory / "vexa.bot.response.json")
    receipt.update({"status": "dispatched", "bot_id": response.get("id") or response.get("bot_id"), "updated_at": now()})
    atomic(receipt_path, receipt)
    return "dispatched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting-dir", type=Path, required=True)
    args = parser.parse_args()
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    directory = args.meeting_dir.resolve()
    if directory.parent != (home / "workspace/meetings").resolve():
        parser.error("meeting directory outside canonical workspace")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / ".vexa-rejoin.lock"
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("already-running")
            return
        print(run(directory, home))


if __name__ == "__main__":
    main()
