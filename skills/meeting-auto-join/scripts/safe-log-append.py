#!/usr/bin/env python3
"""
safe-log-append.py

Robust log appending for cron contexts where shell redirection may be blocked
by security policies (AppArmor, systemd hardening, etc.).

Usage:
    python3 scripts/safe-log-append.py <log_path> <entry_text>

Example:
    python3 scripts/safe-log-append.py ~/.hermes/workspace/meetings/active/meeting-auto-join.log \
        "timestamp: $(date -u -Iseconds)\nevent_id: abc123\nresult: joined"

Writes via Python file ops (respects permissions without hitting shell restrictions).
Exits 0 on success, 1 on error.
"""

import sys
import os
from pathlib import Path

def safe_append(log_path: str, entry_text: str) -> int:
    """Append text to a file safely, creating parent dirs if needed.

    Ensures the entry ends with exactly one trailing newline so consecutive
    calls produce separate log lines even if the caller forgets '\\n'.
    """
    try:
        path = Path(log_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        text = entry_text
        # Ensure exactly one trailing newline
        text = text.rstrip('\n') + '\n'

        with open(path, 'a') as f:
            f.write(text)

        return 0
    except Exception as e:
        print(f"Error appending to {log_path}: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <log_path> <entry_text>", file=sys.stderr)
        sys.exit(1)

    log_path = sys.argv[1]
    entry_text = sys.argv[2]

    # Guard: catch flag-like positional arg (--log, --file, etc.)
    # The script takes ONLY positional args — no named flags exist.
    if log_path.startswith('--'):
        print(f"Error: first argument '{log_path}' looks like a flag. "
              f"This script takes positional args only:\n"
              f"  {sys.argv[0]} <log_path> <entry_text>",
              file=sys.stderr)
        sys.exit(1)

    sys.exit(safe_append(log_path, entry_text))
