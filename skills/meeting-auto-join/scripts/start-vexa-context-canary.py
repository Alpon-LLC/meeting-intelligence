#!/usr/bin/env python3
"""Build meeting STT context and submit a private Vexa context-canary request."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import urllib.request
import urllib.error
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "transcription.state.v1"}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("transcription state must be an object")
    return value


def record_submit_failure(state_path: Path) -> None:
    state = load_state(state_path)
    state.update({
        "live_status": "failed_nonretryable",
        "batch_status": "disabled",
        "canonical_status": "disabled",
        "finalization_ready": True,
        "completed_at": state.get("completed_at") or now(),
        "updated_at": now(),
    })
    atomic_json(state_path, state)


def safe_bot_identity(response: dict[str, Any]) -> str | None:
    for key in ("bot_id", "id"):
        value = response.get(key)
        if isinstance(value, (str, int)):
            return str(value)
    data = response.get("data")
    if isinstance(data, dict):
        return safe_bot_identity(data)
    return None


def emit_alert(
    workspace: Path,
    event_id: str,
    reason: str,
    channel: str | None,
    slack_script: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    message = f"*Vexa transcription alert* — event `{event_id}`: {reason}. Capture/finalization state is preserved."
    status = "pending"
    if channel:
        result = runner(
            ["python3", str(slack_script), channel, message],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        status = "sent" if result.returncode == 0 else "failed"
    atomic_json(workspace / "transcription.alert.json", {
        "version": "transcription.alert.v1",
        "event_id": event_id,
        "reason": reason,
        "delivery_status": status,
        "created_at": now(),
    })
    return status


def submit(*args, **kwargs):
    raise RuntimeError("Configure VEXA_API_ENDPOINT; SSH deployment fallback is not part of the public port")


def bot_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    normalized = endpoint.rstrip("/")
    return normalized if normalized.endswith("/bots") else normalized + "/bots"


class VexaSessionBusy(RuntimeError):
    """The authenticated browser profile is serving another meeting."""


def submit_http(
    payload: bytes,
    endpoint: str,
    api_key: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    endpoint = bot_endpoint(endpoint) or ""
    if not endpoint:
        raise RuntimeError("Vexa endpoint is empty")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=30) as result:
            raw = result.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise VexaSessionBusy("authenticated Vexa session is busy") from exc
        # Never surface the provider response body: validation errors can echo request fields.
        raise RuntimeError("Vexa request failed") from exc
    except Exception as exc:
        # Never surface the provider response body: validation errors can echo request fields.
        raise RuntimeError("Vexa request failed") from exc
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Vexa returned a non-JSON response") from exc
    if not isinstance(response, dict):
        raise RuntimeError("Vexa returned an invalid response object")
    return response


def record_session_wait(state_path: Path) -> None:
    state = load_state(state_path)
    state.update({
        "live_status": "waiting_for_authenticated_session",
        "batch_status": "pending",
        "canonical_status": "pending",
        "readability_status": "pending",
        "finalization_ready": False,
        "updated_at": now(),
    })
    atomic_json(state_path, state)


def run_command(command: Sequence[str], timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, b"", b"")


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-meta", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host", default=os.getenv("VEXA_CANARY_HOST", "localhost"))
    parser.add_argument("--container", default=os.getenv("VEXA_CANARY_CONTAINER", "vexa-lite-context-canary"))
    parser.add_argument("--endpoint", type=bot_endpoint, default=bot_endpoint(os.getenv("VEXA_API_ENDPOINT")))
    default_key_file = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser() / "secrets" / "vexa-context-canary.key"
    parser.add_argument("--api-key-file", type=Path, default=Path(os.getenv("VEXA_API_KEY_FILE", str(default_key_file))))
    parser.add_argument("--builder", type=Path, default=here / "build-transcription-context.py")
    parser.add_argument("--notion-context-builder", type=Path, default=here / "build-notion-transcription-context.py")
    parser.add_argument("--context-input", type=Path)
    parser.add_argument("--notion-timeout", type=int, default=8)
    parser.add_argument("--preparer", type=Path, default=here / "prepare-vexa-bot-request.py")
    parser.add_argument("--glossary", type=Path, default=here.parent / "references" / "transcription-glossary.json")
    parser.add_argument("--alert-channel", default=os.getenv("HERMES_TRANSCRIPTION_ALERT_CHANNEL"))
    parser.add_argument("--slack-script", type=Path, default=here / "slack-dm.py")
    args = parser.parse_args()

    args.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    context_path = args.workspace / "transcription.context.json"
    state_path = args.workspace / "transcription.state.json"
    lifecycle_path = args.workspace / "vexa.bot.response.json"
    event = json.loads(args.event_meta.read_text())
    event_id = str(event.get("event_id") or event.get("id") or "unknown")

    context_input = args.context_input or args.workspace / "transcription.context.input.json"
    notion_status = "reused" if context_input.is_file() else "unavailable"
    if not context_input.is_file() and args.notion_context_builder.is_file():
        notion = run_command([
            "python3", str(args.notion_context_builder), "--event-meta", str(args.event_meta),
            "--output", str(context_input), "--timeout", str(max(1, min(args.notion_timeout, 8))),
        ], timeout=max(2, args.notion_timeout))
        notion_status = "completed" if notion.returncode == 0 and context_input.is_file() else "unavailable"

    build_command = [
        "python3", str(args.builder), "--event-meta", str(args.event_meta),
        "--workspace", str(args.workspace),
    ]
    if context_input.is_file():
        build_command.extend(["--context-input", str(context_input)])
    build = run_command(build_command)
    builder_degraded = build.returncode != 0

    prepared = run_command([
        "python3", str(args.preparer), "--event-meta", str(args.event_meta),
        "--context", str(context_path), "--glossary", str(args.glossary),
        "--state", str(state_path),
    ])
    if prepared.returncode != 0:
        record_submit_failure(state_path)
        alert_status = emit_alert(args.workspace, event_id, "request preparation failed", args.alert_channel, args.slack_script)
        print(json.dumps({"status": "failed", "stage": "request_preparation", "alert_status": alert_status}))
        return 1

    try:
        if args.endpoint:
            api_key = args.api_key_file.read_text().strip()
            if not api_key:
                raise RuntimeError("Vexa API key is empty")
            response = submit_http(prepared.stdout, args.endpoint, api_key)
        else:
            response = submit(prepared.stdout, args.host, args.container)
    except VexaSessionBusy:
        record_session_wait(state_path)
        print(json.dumps({"status": "waiting", "stage": "authenticated_session_busy"}))
        return 75
    except RuntimeError:
        record_submit_failure(state_path)
        alert_status = emit_alert(args.workspace, event_id, "Vexa submission failed", args.alert_channel, args.slack_script)
        print(json.dumps({"status": "failed", "stage": "vexa_submission", "alert_status": alert_status}))
        return 1

    atomic_json(lifecycle_path, response)
    state = load_state(state_path)
    context_degraded = builder_degraded or state.get("context_status") == "degraded"
    state.update({
        "live_status": "running",
        "notion_context_status": notion_status,
        "vexa_lifecycle_path": str(lifecycle_path),
        "vexa_lifecycle_sha256": "sha256:" + hashlib.sha256(lifecycle_path.read_bytes()).hexdigest(),
        "vexa_bot_id": safe_bot_identity(response),
        "updated_at": now(),
    })
    if context_input.is_file():
        state.update({
            "notion_context_path": str(context_input),
            "notion_context_sha256": "sha256:" + hashlib.sha256(context_input.read_bytes()).hexdigest(),
        })
    atomic_json(state_path, state)
    alert_status = None
    if context_degraded:
        alert_status = emit_alert(
            args.workspace, event_id, "dynamic context failed; stable glossary fallback used",
            args.alert_channel, args.slack_script,
        )
    print(json.dumps({
        "status": "requested",
        "context_status": "degraded" if context_degraded else state.get("context_status", "completed"),
        "notion_context_status": notion_status,
        "bot_id": state.get("vexa_bot_id"),
        "lifecycle_path": str(lifecycle_path),
        "alert_status": alert_status,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
