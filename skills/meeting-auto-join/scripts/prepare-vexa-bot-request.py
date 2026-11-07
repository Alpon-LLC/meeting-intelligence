#!/usr/bin/env python3
"""Prepare a Vexa /bots payload from meeting context and atomically record live state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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


def meet_id(event: dict[str, Any]) -> str:
    url = event.get("meet_url") or event.get("hangoutLink") or ""
    match = re.search(r"(?:https?://)?meet\.google\.com/([a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3})", str(url).casefold())
    if not match:
        raise ValueError("event metadata has no valid Google Meet URL")
    return match.group(1)


def no_one_joined_timeout_ms(event: dict[str, Any], now: datetime | None = None) -> int:
    """Keep an early bot until 20 minutes after the scheduled start.

    Vexa's default is ten minutes from admission. The scheduler intentionally dispatches before the
    calendar start, so that default can expire only a few minutes into a late-starting meeting.
    This timeout affects startup silence only; the normal everyone-left timeout remains unchanged.
    """
    current = now or datetime.now(timezone.utc)
    raw_start = event.get("start") or event.get("start_time")
    try:
        start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        target = start.astimezone(timezone.utc) + timedelta(minutes=20)
        timeout = int((target - current.astimezone(timezone.utc)).total_seconds() * 1000)
    except (TypeError, ValueError):
        timeout = 20 * 60 * 1000
    return max(10 * 60 * 1000, min(60 * 60 * 1000, timeout))


def load_terms(context_path: Path, glossary_path: Path, expected_meeting_id: str | None) -> tuple[list[str], str, str | None]:
    try:
        raw = context_path.read_bytes()
        context = json.loads(raw)
        if context.get("version") != "meeting-stt-context.v1" or context.get("language") != "en":
            raise ValueError("invalid context")
        if expected_meeting_id and context.get("meeting_id") != expected_meeting_id:
            raise ValueError("context belongs to a different meeting")
        terms = [item["text"] for item in context.get("keyterms", []) if isinstance(item, dict) and isinstance(item.get("text"), str)]
        return terms, "completed", "sha256:" + hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        glossary = json.loads(glossary_path.read_text())
        terms = [
            item["text"] for item in glossary.get("terms", [])
            if isinstance(item, dict) and item.get("always") is True and isinstance(item.get("text"), str)
        ]
        return terms, "degraded", None


def prepare(event_path: Path, context_path: Path, glossary_path: Path, state_path: Path) -> dict[str, Any]:
    event = json.loads(event_path.read_text())
    expected_meeting_id = event.get("event_id") or event.get("id")
    terms, context_status, context_sha = load_terms(context_path, glossary_path, expected_meeting_id)
    if context_status == "degraded":
        # Live and batch must share a persisted context even when enrichment
        # fails. Keep the same stable terms used in this request, not a later
        # Notion lookup that could describe a different meeting context.
        glossary = json.loads(glossary_path.read_text())
        entries = {item["text"]: item for item in glossary.get("terms", []) if isinstance(item, dict) and isinstance(item.get("text"), str)}
        valid_sources = {"calendar_attendee", "external_organization", "project", "organization", "product", "human_correction", "agenda"}
        keyterms = [{"text": term, "source": entries[term].get("source") if entries[term].get("source") in valid_sources else "product", "priority": 50} for term in terms]
        if context_path.is_file():
            rejected = context_path.read_bytes()
            rejected_path = context_path.with_name(context_path.name + ".rejected-" + hashlib.sha256(rejected).hexdigest()[:12])
            try:
                fd = os.open(rejected_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(rejected)
        atomic_json(context_path, {
            "version": "meeting-stt-context.v1", "meeting_id": expected_meeting_id or meet_id(event),
            "language": "en", "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "keyterms": keyterms,
            "canonical_entities": {"people": [], "organizations": [], "products": []},
            "source_fingerprints": {"stable_glossary": "sha256:" + hashlib.sha256(glossary_path.read_bytes()).hexdigest()},
        })
        context_sha = "sha256:" + hashlib.sha256(context_path.read_bytes()).hexdigest()
    state = json.loads(state_path.read_text()) if state_path.exists() else {"version": "transcription.state.v1"}
    state.update({
        "context_status": context_status,
        "context_path": str(context_path),
        "context_sha256": context_sha,
        "live_status": "running",
        "batch_status": "pending",
        "canonical_status": "pending",
        "readability_status": "pending",
        "finalization_ready": False,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    state.pop("completed_at", None)
    atomic_json(state_path, state)
    return {
        "platform": "google_meet",
        "native_meeting_id": meet_id(event),
        "bot_name": os.environ.get("VEXA_BOT_NAME", "Meeting Bot").strip() or "Meeting Bot",
        "language": "en",
        "transcription_keyterms": terms,
        "transcribe_enabled": True,
        "recording_enabled": False,
        "automatic_leave": {"no_one_joined_timeout": no_one_joined_timeout_ms(event)},
        "continue_meeting": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-meta", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--glossary", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.event_meta, args.context, args.glossary, args.state), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
