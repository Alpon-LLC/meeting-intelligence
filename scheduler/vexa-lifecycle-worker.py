#!/usr/bin/env python3
"""Deterministic prep/start execution for one fenced calendar dispatch generation."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def generation(job):
    dispatch = job.get("_dispatch") or {}
    return {key: dispatch.get(key) for key in ("key", "phase", "attempt", "delivery_id")}


def current(job):
    dispatch = job.get("_dispatch") or {}; claim = read(dispatch.get("claim_path", ""))
    expected = generation(job)
    return all(claim.get(key) == value for key, value in expected.items())


def receipt(job, status, outcome, retryable=False, details=None):
    dispatch = job.get("_dispatch") or {}
    value = {"version": 1, **generation(job), "status": status, "outcome": outcome,
             "retryable": retryable is True, "updated_at": now()}
    if details: value["details"] = details
    atomic(dispatch["receipt_path"], value)


def run(command, timeout):
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)


def clean_event(job):
    value = {key: item for key, item in job.items() if key != "_dispatch"}
    value.setdefault("event_id", value.get("id")); value.setdefault("id", value.get("event_id"))
    return value


def prep(job, home, event_dir):
    scripts = home / "skills/meeting/meeting-auto-join/scripts"
    event_meta = event_dir / "event_meta.json"
    context = event_dir / "transcription.context.input.json"
    context_status = "reused" if context.is_file() else "unavailable"
    if not context.is_file():
        result = run(["python3", str(scripts / "build-notion-transcription-context.py"),
                      "--event-meta", str(event_meta), "--output", str(context), "--timeout", "8"], 15)
        context_status = "completed" if result.returncode == 0 and context.is_file() else "degraded"
    prep_dir = home / "workspace/meetings-prep" / str(job.get("event_id") or job.get("id"))
    prep_dir.mkdir(parents=True, exist_ok=True, mode=0o700); prep_dir.chmod(0o700)
    atomic(prep_dir / ".lock", {"status": "done", "completed_at": now(), "mode": "deterministic-context"})
    atomic(prep_dir / "summary.json", {"status": "done", "event_id": job.get("event_id") or job.get("id"),
           "title": job.get("title"), "context_status": context_status, "context_path": str(context) if context.is_file() else None,
           "completed_at": now()})
    receipt(job, "succeeded", "prep-done", details={"context_status": context_status})


def start(job, home, event_dir):
    script = home / "skills/meeting/meeting-auto-join/scripts/start-vexa-context-canary.py"
    event_meta = event_dir / "event_meta.json"
    try:
        meeting_end = datetime.fromisoformat(str(job.get("end") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        meeting_end = time.time() + 30 * 60
    retry_seconds = max(1, min(60, int(os.getenv("VEXA_AUTH_SESSION_RETRY_SECONDS", "30"))))
    while current(job):
        result = run(["python3", str(script), "--event-meta", str(event_meta), "--workspace", str(event_dir)], 240)
        if result.returncode == 0:
            if not current(job): return 0
            response = read(event_dir / "vexa.bot.response.json")
            receipt(job, "succeeded", "vexa-dispatched", details={"bot_id": response.get("id") or response.get("bot_id")})
            return 0
        if result.returncode != 75:
            receipt(job, "failed", "vexa-start-failed", retryable=True,
                    details={"exit_code": result.returncode, "stage": "deterministic-start"})
            return 1
        if time.time() >= meeting_end:
            state_path = event_dir / "transcription.state.json"
            state = read(state_path)
            state.update({
                "live_status": "failed_nonretryable",
                "batch_status": "disabled",
                "canonical_status": "disabled",
                "readability_status": "disabled",
                "finalization_ready": True,
                "updated_at": now(),
                "completed_at": now(),
            })
            atomic(state_path, state)
            atomic(event_dir / ".no-join", {
                "reason": "authenticated-session-busy-until-meeting-ended",
                "created_at": now(),
            })
            receipt(job, "skipped", "authenticated-session-busy-until-meeting-ended")
            return 0
        receipt(job, "running", "authenticated-session-busy", details={"retry_after_seconds": retry_seconds})
        time.sleep(min(retry_seconds, max(1, meeting_end - time.time())))
    return 0


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--job", type=Path, required=True); args = parser.parse_args()
    job = read(args.job); dispatch = job.get("_dispatch") or {}; phase = dispatch.get("phase")
    if phase not in {"prep", "start"} or not dispatch.get("receipt_path") or not dispatch.get("claim_path"):
        raise SystemExit("invalid lifecycle job")
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))); event_id = str(job.get("event_id") or job.get("id") or "")
    event_dir = home / "workspace/meetings" / event_id; event_dir.mkdir(parents=True, exist_ok=True, mode=0o700); event_dir.chmod(0o700)
    lock_path = event_dir / f".vexa-{phase}-worker.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not current(job): return 0
        receipt(job, "running", "lifecycle-worker-running")
        atomic(event_dir / "event_meta.json", clean_event(job))
        try:
            if phase == "prep":
                prep(job, home, event_dir)
                return 0
            return start(job, home, event_dir)
        except subprocess.TimeoutExpired:
            receipt(job, "failed", f"{phase}-worker-timeout", retryable=True, details={"stage": "deterministic-lifecycle"})
            return 1
        except Exception as error:
            receipt(job, "failed", f"{phase}-worker-failed", retryable=True,
                    details={"error_type": type(error).__name__, "stage": "deterministic-lifecycle"})
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
