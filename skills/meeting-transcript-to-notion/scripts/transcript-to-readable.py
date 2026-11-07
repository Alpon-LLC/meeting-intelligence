#!/usr/bin/env python3
"""
transcript-to-readable.py — Transform a raw JSON Lines transcript into a
human-readable text file suitable for attaching to a Notion meeting notes page.

Usage:
  python3 transcript-to-readable.py <transcript.json> [--output <output.txt>] [--title "Meeting Title"] [--timezone Asia/Bangkok] [--roster team-roster.yaml]

If --output is omitted, writes to <transcript.json>.readable.txt alongside the input.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

# ── team roster for speaker normalization ──────────────────────────────
# Maps from any known variant (lowercase, partial name, full legal name)
# to the canonical display name used in meeting notes.
DEFAULT_ROSTER = {"you": None}  # Extend from the attendee-provided roster, never company aliases.


def load_roster_from_yaml(yaml_path):
    """Extend the default roster with names from a team-roster.yaml file."""
    roster = dict(DEFAULT_ROSTER)
    if not yaml_path or not os.path.isfile(yaml_path):
        return roster
    try:
        with open(yaml_path) as f:
            for line in f:
                m = re.match(r'^\s*-\s*name:\s*(.+)$', line)
                if m:
                    name = m.group(1).strip()
                    roster[name.lower()] = name
        return roster
    except Exception:
        return roster


def normalize_speaker(raw_speaker, roster):
    """Map a raw speaker label to canonical display name."""
    if not raw_speaker:
        return None
    raw_lower = raw_speaker.strip().lower()
    if raw_lower in roster:
        return roster[raw_lower]
    # Partial match: check if any roster key is a substring of the raw label
    # (handles cases like "Operator Nguyen (He/Him)" → "Operator")
    for key, canonical in roster.items():
        if key and raw_lower.startswith(key):
            return canonical
    # No match found — return as-is but title-cased
    return raw_speaker.strip().title()


def format_timestamp(ts_iso, tz_offset_hours=7):
    """Convert ISO timestamp to HH:MM format in the given timezone."""
    try:
        dt = datetime.fromisoformat(ts_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(timezone(timedelta(hours=tz_offset_hours)))
        return local.strftime("%H:%M")
    except (ValueError, TypeError):
        # If timestamp is just HH:MM:SS, return as-is
        if re.match(r'^\d{1,2}:\d{2}', ts_iso):
            return ts_iso[:5]
        return ts_iso


def transform_transcript(transcript_path, output_path=None, title=None,
                         tz_offset_hours=7, roster=None):
    """Main transformation logic."""
    if roster is None:
        roster = DEFAULT_ROSTER

    # Read the JSON Lines transcript — handle multi-line JSON objects
    # (text fields with embedded newlines) using brace-depth tracking
    entries = []
    with open(transcript_path) as f:
        raw = f.read()

    lines = [l.strip() for l in raw.split('\n') if l.strip()]
    buf = ''
    depth = 0
    in_string = False
    escaped = False
    for line in lines:
        if not buf and not line.startswith('{'):
            continue
        buf += line
        if not in_string:
            for ch in line:
                if escaped:
                    escaped = False
                    continue
                if ch == '\\' and in_string:
                    escaped = True
                    continue
                if ch == '"' and not escaped:
                    in_string = not in_string
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
        if depth == 0 and buf.startswith('{') and buf.endswith('}'):
            try:
                entry = json.loads(buf)
                entries.append(entry)
            except json.JSONDecodeError as e:
                print(f"Warning: skipping unparseable JSON: {e}", file=sys.stderr)
            buf = ''
            depth = 0
            in_string = False
            escaped = False
    # Try leftover buffer
    if buf:
        try:
            entries.append(json.loads(buf))
        except json.JSONDecodeError:
            print(f"Warning: leftover unparseable JSON: {buf[:100]}...", file=sys.stderr)

    if not entries:
        print("Warning: transcript is empty", file=sys.stderr)
        return None

    # Determine date from first entry
    first_ts = entries[0].get("ts", "")
    try:
        dt = datetime.fromisoformat(first_ts)
        date_str = dt.strftime("%A, %B %d, %Y")
    except (ValueError, TypeError):
        date_str = "Unknown date"

    # Build output
    lines = []
    lines.append("=" * 60)
    lines.append("  MEETING TRANSCRIPT")
    if title:
        lines.append(f"  {title}")
    lines.append(f"  Date: {date_str}")
    lines.append(f"  Source: Google Meet Captions")
    lines.append(f"  Lines: {len(entries)}")
    lines.append("=" * 60)
    lines.append("")

    # Process each entry
    prev_speaker = None
    for entry in entries:
        ts = entry.get("ts", "")
        raw_speaker = entry.get("speaker", "")
        text = entry.get("text", "")

        time_str = format_timestamp(ts, tz_offset_hours)
        speaker = normalize_speaker(raw_speaker, roster)

        # Skip bot-self utterances (label "You")
        if speaker is None:
            continue

        # Add speaker separator when speaker changes
        if speaker != prev_speaker and prev_speaker is not None:
            lines.append("")

        # Format: [HH:MM] Speaker: text
        line = f"[{time_str}] {speaker}: {text}"
        lines.append(line)
        prev_speaker = speaker

    # Footer
    lines.append("")
    lines.append("-" * 60)
    lines.append(f"  End of transcript — {len(entries)} utterances")
    lines.append("=" * 60)

    output = "\n".join(lines) + "\n"

    # Write output
    if output_path is None:
        output_path = transcript_path + ".readable.txt"

    with open(output_path, "w") as f:
        f.write(output)

    print(f"Transformed transcript written to: {output_path}", file=sys.stderr)
    print(f"  Input:  {transcript_path} ({len(entries)} lines)", file=sys.stderr)
    print(f"  Output: {output_path} ({len(output)} chars)", file=sys.stderr)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Transform JSON Lines transcript to human-readable text"
    )
    parser.add_argument("transcript", help="Path to transcript.json (JSON Lines)")
    parser.add_argument("--output", "-o", help="Output file path (default: <transcript>.readable.txt)")
    parser.add_argument("--title", "-t", default=None, help="Meeting title")
    parser.add_argument("--timezone", default="Asia/Bangkok", help="Timezone (default: Asia/Bangkok, UTC+7)")
    parser.add_argument("--roster", default=None, help="Path to team-roster.yaml for speaker normalization")
    parser.add_argument("--json", action="store_true",
                        help="Output result info as JSON (for use by calling scripts)")

    args = parser.parse_args()

    if not os.path.isfile(args.transcript):
        print(f"Error: transcript not found: {args.transcript}", file=sys.stderr)
        sys.exit(1)

    # Timezone offset
    tz_map = {
        "asia/bangkok": 7,
        "asia/ho_chi_operator": 7,
        "asia/saigon": 7,
        "utc": 0,
        "asia/singapore": 8,
        "asia/hong_kong": 8,
        "asia/shanghai": 8,
        "asia/tokyo": 9,
        "asia/seoul": 9,
        "america/new_york": -4,
        "america/chicago": -5,
        "america/denver": -6,
        "america/los_angeles": -7,
        "europe/london": 1,
        "europe/berlin": 2,
        "europe/paris": 2,
    }
    tz_lower = args.timezone.strip().lower()
    tz_offset = tz_map.get(tz_lower, 7)  # default Bangkok

    # Load roster
    roster = load_roster_from_yaml(args.roster)

    result_path = transform_transcript(
        args.transcript,
        output_path=args.output,
        title=args.title,
        tz_offset_hours=tz_offset,
        roster=roster,
    )

    if args.json:
        import json as _json
        info = {
            "input": os.path.abspath(args.transcript),
            "output": os.path.abspath(result_path) if result_path else None,
            "success": result_path is not None,
        }
        print(_json.dumps(info))

    sys.exit(0 if result_path else 1)


if __name__ == "__main__":
    main()
