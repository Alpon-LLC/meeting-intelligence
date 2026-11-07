#!/usr/bin/env python3
"""Verify terminal Vexa webhooks, materialize signal tapes, and trigger Hermes finalization."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


MEET_ID = re.compile(r"(?:https?://)?meet\.google\.com/([a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3})", re.I)
TERMINAL_EVENTS = frozenset({"meeting.completed", "bot.failed"})


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def slug_from_event(event: dict[str, Any]) -> str | None:
    candidates = [event.get("meet_url"), event.get("hangoutLink")]
    conference = event.get("conferenceData")
    if isinstance(conference, dict):
        candidates.extend(
            item.get("uri") for item in conference.get("entryPoints", [])
            if isinstance(item, dict) and item.get("entryPointType") == "video"
        )
    for value in candidates:
        match = MEET_ID.search(str(value or ""))
        if match:
            return match.group(1).lower()
    return None


def verify_vexa(body: bytes, headers: dict[str, str], secret: str, *, now: int | None = None) -> bool:
    timestamp = headers.get("x-webhook-timestamp", "")
    signature = headers.get("x-webhook-signature", "")
    try:
        numeric_timestamp = int(timestamp)
    except ValueError:
        return False
    if abs((now or int(time.time())) - numeric_timestamp) > 300:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def find_event_dir(root: Path, slug: str, completed_at: datetime) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[bool, float, Path, dict[str, Any]]] = []
    for metadata_path in root.glob("*/event_meta.json"):
        try:
            event = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if slug_from_event(event) != slug:
            continue
        start = parse_time(event.get("start") or event.get("start_time"))
        end = parse_time(event.get("end") or event.get("end_time"))
        anchor = end or start
        if anchor is None:
            continue
        distance = abs((completed_at - anchor).total_seconds())
        if distance > timedelta(hours=24).total_seconds():
            continue
        event_id = str(event.get("event_id") or event.get("id") or metadata_path.parent.name)
        manual = event_id.startswith("manual-") or metadata_path.parent.name.startswith("manual-")
        candidates.append((manual, distance, metadata_path.parent, event))
    if not candidates:
        raise FileNotFoundError(f"no event metadata within 24h for Google Meet {slug}")
    _, _, directory, event = min(candidates, key=lambda item: (item[0], item[1]))
    return directory, event


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_lifecycle(event_dir: Path, meeting: dict[str, Any], event_type: str, envelope: dict[str, Any]) -> None:
    joined = parse_time(meeting.get("start_time"))
    ended = parse_time(meeting.get("end_time") or envelope.get("created_at")) or datetime.now(timezone.utc)
    slug = str(meeting.get("native_meeting_id") or "")
    status = {
        "url": f"https://meet.google.com/{slug}",
        "joinedAt": joined.timestamp() if joined else None,
        "endedAt": ended.timestamp(),
        "exited": True,
        "exitCode": 0 if event_type == "meeting.completed" else 1,
        "exitReason": event_type,
        "source": "vexa_authenticated",
        "vexaMeetingId": meeting.get("id"),
    }
    atomic_bytes(event_dir / "status.json", (json.dumps(status, indent=2, sort_keys=True) + "\n").encode())
    lifecycle = {
        "event_id": envelope.get("event_id"),
        "event_type": event_type,
        "created_at": envelope.get("created_at"),
        "meeting": meeting,
    }
    atomic_bytes(event_dir / "vexa.lifecycle.json", (json.dumps(lifecycle, indent=2, sort_keys=True) + "\n").encode())


def materialize_signal(meeting: dict[str, Any], target: Path, client: Any | None = None) -> list[str]:
    if client is None:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=os.environ["BRIDGE_S3_ENDPOINT"],
            aws_access_key_id=os.environ["BRIDGE_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["BRIDGE_S3_SECRET_KEY"],
            region_name=os.environ.get("BRIDGE_S3_REGION", "us-east-1"),
        )
    user_id, meeting_id = meeting.get("user_id"), meeting.get("id")
    if user_id is None or meeting_id is None:
        raise ValueError("Vexa completion lacks user_id or meeting id")
    bucket = os.environ.get("BRIDGE_S3_BUCKET", "vexa")
    prefix = f"signal/{user_id}/{meeting_id}/"
    deadline = time.monotonic() + int(os.environ.get("SIGNAL_WAIT_SECONDS", "300"))
    objects: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        objects, token = [], None
        while True:
            request = {"Bucket": bucket, "Prefix": prefix}
            if token:
                request["ContinuationToken"] = token
            response = client.list_objects_v2(**request)
            objects.extend(response.get("Contents", []))
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError("S3 list was truncated without a continuation token")
        if any(str(item.get("Key", "")).endswith("/captured-signal.jsonl") for item in objects):
            break
        time.sleep(2)
    if not objects:
        raise FileNotFoundError(f"no signal objects under {prefix}")
    captures = [item for item in objects if str(item.get("Key", "")).endswith("/captured-signal.jsonl")]
    if not captures:
        raise FileNotFoundError(f"no captured-signal part under {prefix}")
    selected = max(captures, key=lambda item: item.get("LastModified") or datetime.min.replace(tzinfo=timezone.utc))
    session_prefix = str(selected["Key"]).rsplit("/", 1)[0] + "/"
    objects = [item for item in objects if str(item.get("Key", "")).startswith(session_prefix)]
    capture_response = client.get_object(Bucket=bucket, Key=str(selected["Key"]))
    capture_data = capture_response["Body"].read()
    try:
        header = json.loads(capture_data.splitlines()[0])
        trace_id = str(header["trace_id"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("captured-signal object has no valid trace_id header") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", trace_id):
        raise ValueError("captured-signal trace_id is unsafe for a local filename")
    written: list[str] = []
    for item in objects:
        key = str(item.get("Key") or "")
        part_name = Path(key).name
        if not part_name or not re.fullmatch(r"[A-Za-z0-9._-]+", part_name):
            continue
        name = f"{trace_id}.{part_name}"
        if key == str(selected["Key"]):
            data = capture_data
        else:
            response = client.get_object(Bucket=bucket, Key=key)
            data = response["Body"].read()
        atomic_bytes(target / name, data)
        written.append(name)
    if not any(name.endswith(".captured-signal.jsonl") for name in written):
        raise FileNotFoundError(f"captured-signal part not materialized from {prefix}")
    return sorted(written)


def write_signal_ready(target: Path, meeting: dict[str, Any], files: list[str]) -> Path:
    artifacts = []
    for name in sorted(files):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError("materialized signal filename is unsafe")
        source = target / name
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError("materialized signal file is missing")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        artifacts.append({
            "name": name,
            "size": source.stat().st_size,
            "sha256": "sha256:" + digest.hexdigest(),
        })
    if not any(item["name"].endswith(".captured-signal.jsonl") for item in artifacts):
        raise FileNotFoundError("materialized signal manifest has no capture tape")
    capture = next(item for item in artifacts if item["name"].endswith(".captured-signal.jsonl"))
    capture_limit = int(os.environ.get("SIGNAL_CAPTURE_MAX_BYTES", str(1024 * 1024 * 1024)))
    if capture_limit <= 1024 * 1024:
        raise ValueError("SIGNAL_CAPTURE_MAX_BYTES must exceed the cap-detection margin")
    capped = capture["size"] >= capture_limit - 1024 * 1024
    ready = target / ".vexa-signal-ready.json"
    atomic_bytes(ready, (json.dumps({
        "version": "vexa-signal-ready.v1",
        "complete": not capped,
        "capped": capped,
        "capture_limit_bytes": capture_limit,
        "meeting_id": str(meeting.get("id")),
        "native_meeting_id": str(meeting.get("native_meeting_id") or ""),
        "files": artifacts,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }, indent=2, sort_keys=True) + "\n").encode())
    return ready


def forward_to_hermes(event: dict[str, Any], envelope: dict[str, Any]) -> int:
    if os.getenv("DEFER_FINISH_TO_SCHEDULER") == "true":
        # Materialization is durable; original JS finish gate dispatches the agent webhook after canonical readiness.
        return 202
    payload = dict(event)
    payload["id"] = payload.get("event_id") or payload.get("id")
    payload["vexa_completion"] = {
        "event_id": envelope.get("event_id"),
        "event_type": envelope.get("event_type"),
        "created_at": envelope.get("created_at"),
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    secret = os.environ["HERMES_WEBHOOK_SECRET"]
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        os.environ["HERMES_WEBHOOK_URL"],
        data=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": signature},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status


def process(
    envelope: dict[str, Any],
    meetings_root: Path,
    state_root: Path,
    materializer: Callable[[dict[str, Any], Path], list[str]] = materialize_signal,
    forwarder: Callable[[dict[str, Any], dict[str, Any]], int] = forward_to_hermes,
) -> dict[str, Any]:
    event_id = str(envelope.get("event_id") or "")
    event_type = str(envelope.get("event_type") or "")
    if not event_id or event_type not in TERMINAL_EVENTS:
        raise ValueError("unsupported or unidentified Vexa event")
    receipt = state_root / f"{hashlib.sha256(event_id.encode()).hexdigest()}.json"
    if receipt.is_file():
        prior = json.loads(receipt.read_text())
        return {**prior, "status": "already_forwarded"}
    meeting = envelope.get("data", {}).get("meeting")
    if not isinstance(meeting, dict):
        raise ValueError("Vexa event has no meeting object")
    slug = str(meeting.get("native_meeting_id") or "").lower()
    if not re.fullmatch(r"[a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3}", slug):
        raise ValueError("Vexa event has no valid Google Meet id")
    completed_at = parse_time(meeting.get("end_time") or envelope.get("created_at")) or datetime.now(timezone.utc)
    event_dir, event = find_event_dir(meetings_root, slug, completed_at)
    # Recurring Meet URLs are not occurrence identities. Never let an old delivery
    # replace the current run's lifecycle or publication inputs.
    bound_ids = []
    for name, keys in (("vexa.bot.response.json", ("id", "bot_id")),
                       ("transcription.state.json", ("vexa_bot_id",))):
        try:
            binding = json.loads((event_dir / name).read_text())
            value = next((binding.get(key) for key in keys if binding.get(key) is not None), None)
            if value is not None:
                bound_ids.append(str(value))
        except (OSError, ValueError):
            pass
    if bound_ids and any(value != str(meeting.get("id")) for value in bound_ids):
        return {"status": "stale_occurrence", "event_id": event_id}
    provenance = meeting.get("service_provenance") or (meeting.get("data") or {}).get("service_provenance") or {}
    data = meeting.get("data") or {}
    def has_transcription(evidence):
        evidence = evidence or {}
        try:
            positive_segments = float(evidence.get("segments_captured") or 0) > 0
        except (TypeError, ValueError):
            positive_segments = False
        return evidence.get("transcription_outcome") == "served" or positive_segments

    produced_transcription = any(has_transcription(evidence) for evidence in
                                 (meeting, data, meeting.get("service_provenance"), data.get("service_provenance")))
    never_admitted = bool(bound_ids) and meeting.get("status") in {"completed", "failed"} \
        and provenance.get("bot_outcome") == "never_admitted" and not meeting.get("start_time") \
        and not produced_transcription \
        and not any(item.get("to") == "active" for item in (meeting.get("data") or {}).get("status_transition", []))
    write_lifecycle(event_dir, meeting, event_type, envelope)
    files = materializer(meeting, event_dir) if event_type == "meeting.completed" and not never_admitted else []
    if event_type == "meeting.completed" and not never_admitted:
        write_signal_ready(event_dir, meeting, files)
    status_code = forwarder(event, envelope)
    if not 200 <= status_code < 300:
        raise RuntimeError(f"Hermes finalization webhook returned HTTP {status_code}")
    result = {
        "status": "forwarded",
        "event_id": event_id,
        "event_type": event_type,
        "meeting_event_id": event.get("event_id") or event.get("id") or event_dir.name,
        "signal_files": files,
        "forwarded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    atomic_bytes(receipt, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "vexa-hermes-bridge/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(json.dumps({"event": "http", "message": fmt % args}), flush=True)

    def send_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.send_json(200 if self.path == "/health" else 404, {"status": "ok" if self.path == "/health" else "not_found"})

    def do_POST(self) -> None:
        if self.path != "/vexa/events":
            self.send_json(404, {"status": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_000_000:
            self.send_json(413, {"status": "invalid_size"})
            return
        body = self.rfile.read(length)
        headers = {key.lower(): value for key, value in self.headers.items()}
        if not verify_vexa(body, headers, os.environ["VEXA_SYSTEM_WEBHOOK_SECRET"]):
            self.send_json(401, {"status": "invalid_signature"})
            return
        try:
            result = process(
                json.loads(body),
                Path(os.environ.get("HERMES_MEETINGS_DIR", "/meetings")),
                Path(os.environ.get("BRIDGE_STATE_DIR", "/state")),
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(json.dumps({"event": "delivery_failed", "error": type(exc).__name__}), flush=True)
            self.send_json(503, {"status": "retryable_failure"})
            return
        self.send_json(200, result)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8091"))), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
