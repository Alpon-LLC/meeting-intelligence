#!/usr/bin/env python3
"""Email a private Notion meeting-notes link to internal attendees, idempotently."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


DEFAULT_DOMAINS = tuple(x.strip() for x in os.getenv("MEETING_INTERNAL_DOMAINS", "").split(",") if x.strip())
BOT_ADDRESSES = {os.getenv("CALENDAR_OWNER_EMAIL", "")}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def attendee_email(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip().casefold() or None
    if isinstance(item, dict):
        value = item.get("email")
        return value.strip().casefold() if isinstance(value, str) and value.strip() else None
    return None


def recipients(event: dict[str, Any], domains: tuple[str, ...] | None = None) -> list[str]:
    if domains is None: domains = DEFAULT_DOMAINS
    candidates: list[Any] = list(event.get("attendees") or [])
    organizer = event.get("organizer")
    if organizer:
        candidates.append(organizer)
    result: set[str] = set()
    for item in candidates:
        if isinstance(item, dict) and str(item.get("responseStatus") or item.get("response_status") or "").casefold() == "declined":
            continue
        email = attendee_email(item)
        if not email or email in BOT_ADDRESSES or "@" not in email:
            continue
        if email.rsplit("@", 1)[1] in domains:
            result.add(email)
    return sorted(result)


def default_sender(google_script: Path, to: list[str], subject: str, body: str) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("hermes_google_api", google_script)
    if not spec or not spec.loader:
        raise RuntimeError("unable to load Google API client")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = SimpleNamespace(
        body=body,
        html=False,
        to=",".join(to),
        subject=subject,
        cc=None,
        from_header=None,
        thread_id=None,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        module.gmail_send(args)
    value = json.loads(output.getvalue())
    if value.get("status") != "sent" or not value.get("id"):
        raise RuntimeError("Gmail client did not return a sent message")
    return value


def deliver(
    event_path: Path,
    triage_path: Path,
    receipt_path: Path,
    google_script: Path,
    dry_run: bool = False,
    sender: Callable[[Path, list[str], str, str], dict[str, Any]] = default_sender,
) -> dict[str, Any]:
    event = json.loads(event_path.read_text())
    triage = json.loads(triage_path.read_text())
    if triage.get("test_session") is True:
        result = {"version": "transcript-email.v1", "status": "skipped", "reason": "test_session", "recipients": []}
        atomic_json(receipt_path, result)
        return result
    to = recipients(event)
    if not to:
        result = {"version": "transcript-email.v1", "status": "skipped", "reason": "no_internal_attendees", "recipients": []}
        atomic_json(receipt_path, result)
        return result
    notion_url = str(triage.get("notion_page_url") or "").strip()
    if not notion_url.startswith("https://"):
        raise ValueError("triage context has no HTTPS Notion page URL")
    title = str(event.get("title") or event.get("summary") or "Meeting").strip()
    summary = str(triage.get("transcript_summary") or "Meeting notes and transcript are available for review.").strip()
    fingerprint = hashlib.sha256(json.dumps({"to": to, "url": notion_url, "summary": summary}, sort_keys=True).encode()).hexdigest()
    if receipt_path.exists():
        prior = json.loads(receipt_path.read_text())
        if prior.get("status") == "sent" and prior.get("fingerprint") == fingerprint:
            return {**prior, "status": "already_sent"}
    # A meeting must produce at most one automatic attendee email even if two publishers choose
    # different receipt filenames or Notion URLs. Corrections require an explicit human follow-up,
    # not another automatic blast.
    for candidate in receipt_path.parent.glob("*email*.json"):
        if candidate == receipt_path:
            continue
        try:
            prior = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if prior.get("status") == "sent" and sorted(prior.get("recipients") or []) == to:
            return {
                **prior,
                "status": "already_sent_other_receipt",
                "blocked_receipt": str(candidate),
            }
    subject = f"Meeting notes: {title}"
    body = (
        f"John has published draft notes for “{title}”.\n\n"
        f"Summary\n{summary}\n\n"
        f"Review the notes and transcript in Notion:\n{notion_url}\n\n"
        "The transcript was produced by the Vexa context-aware transcription workflow. "
        "Please report names, terminology, numbers, or speaker labels that need correction.\n\n"
        "Action items remain subject to organizer approval."
    )
    if dry_run:
        return {"version": "transcript-email.v1", "status": "dry_run", "recipients": to, "fingerprint": fingerprint}
    sent = sender(google_script, to, subject, body)
    result = {
        "version": "transcript-email.v1",
        "status": "sent",
        "recipients": to,
        "fingerprint": fingerprint,
        "message_id": sent["id"],
        "thread_id": sent.get("threadId") or "",
        "notion_page_url": notion_url,
    }
    atomic_json(receipt_path, result)
    return result


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-meta", required=True, type=Path)
    parser.add_argument("--triage-context", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--google-script", type=Path, default=home / "skills/productivity/google-workspace/scripts/google_api.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry_run = args.dry_run or os.environ.get("MEETING_FINALIZE_DRY_RUN") == "1"
    result = deliver(args.event_meta, args.triage_context, args.receipt, args.google_script, dry_run)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
