#!/usr/bin/env python3
"""Inject a fake transcript into a meeting directory for testing.

Usage:
    inject-transcript.py <meeting_id> [--start TIME] [--patch-status] [--dry-read]

Reads event_meta.json from the meeting directory to get attendee names.
Falls back to the daily-standup-transcript.json template for content.
Shifts timestamps relative to --start (or existing meeting start from event_meta.json).
Optionally patches status.json transcriptLines to match.
"""
import json, sys, os, time, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "daily-standup-transcript.json"
MEETINGS_DIR = Path.home() / ".hermes" / "workspace" / "meetings"


def load_event_meta(meeting_dir):
    path = meeting_dir / "event_meta.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def get_attendees(meta):
    """Return list of attendee display names from event metadata."""
    attendees = meta.get("attendees", [])
    names = []
    for a in attendees:
        email = a.get("email", "")
        name = a.get("name")
        if name and name != "Unknown":
            names.append(name)
        elif email:
            # Derive display name from email local part
            local = email.split("@")[0]
            # Handle bot -> John, operator@exampleorg -> Operator
            display = local.split(".")[0].capitalize()
            names.append(display)
    return names or ["Operator", "John"]


def inject_transcript(meeting_id, start_time=None, patch_status=True, dry_read=False):
    meeting_dir = MEETINGS_DIR / meeting_id
    if not meeting_dir.exists():
        print(f"ERROR: meeting directory not found: {meeting_dir}", file=sys.stderr)
        sys.exit(1)

    dest = meeting_dir / "transcript.json"

    # Load event metadata for context
    meta = load_event_meta(meeting_dir)
    attendees = get_attendees(meta)
    title = meta.get("title", "Meeting")
    print(f"Meeting: {title}")
    print(f"Attendees: {', '.join(attendees)}")

    # Determine base timestamp
    if start_time:
        base = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    elif meta.get("start"):
        base = datetime.fromisoformat(meta["start"].replace("Z", "+00:00"))
    else:
        base = datetime.now(timezone.utc)

    # Load template lines
    with open(TEMPLATE_PATH) as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    # Parse template entries and shift timestamps
    entries = []
    for i, raw in enumerate(raw_lines):
        entry = json.loads(raw)
        # Template uses HH:MM:SS — shift relative to base
        t = datetime.strptime(entry["ts"], "%H:%M:%S")
        shifted = base.replace(hour=t.hour, minute=t.minute, second=t.second)
        # Add incremental seconds to preserve turn order
        shifted += timedelta(seconds=i * 7)
        entry["ts"] = shifted.strftime("%Y-%m-%dT%H:%M:%S")
        entries.append(entry)

    if dry_read:
        print(f"\n[DRY RUN] Would write {len(entries)} lines to {dest}")
        for e in entries:
            print(f"  [{e['ts']}] {e['speaker']}: {e['text'][:60]}...")
        return

    # Write transcript
    with open(dest, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Injected {len(entries)} lines into {dest}")

    # Patch status.json
    if patch_status:
        status_path = meeting_dir / "status.json"
        if status_path.exists():
            status = json.loads(status_path.read_text())
            status["transcriptLines"] = len(entries)
            status["lastCaptionAt"] = time.time()
            tmp = status_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
            tmp.replace(status_path)
            print(f"Patched status.json: transcriptLines={len(entries)}")
        else:
            print("WARNING: status.json not found, skipping patch")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject fake transcript into meeting directory")
    parser.add_argument("meeting_id", help="Calendar event / meeting directory ID")
    parser.add_argument("--start", help="ISO start time for timestamp base (default: from event_meta.json)")
    parser.add_argument("--no-patch-status", action="store_true", help="Skip patching status.json")
    parser.add_argument("--dry-read", action="store_true", help="Show what would be written without writing")
    args = parser.parse_args()
    inject_transcript(
        args.meeting_id,
        start_time=args.start,
        patch_status=not args.no_patch_status,
        dry_read=args.dry_read,
    )
