#!/usr/bin/env python3
"""Deterministic log appender — zero Hermes API deps.

Usage:
    python3 append-log.py <log_path> "<entry_text>"

Appends entry_text + newline to log_path. Creates the file if missing.
Exit code 0 on success, 1 on error.

This replaces safe-log-append.py which requires Hermes API bindings
that aren't available in the terminal tool's execution environment.
"""
import sys, os, datetime

def main():
    if len(sys.argv) < 3:
        print("Usage: append-log.py <log_path> <entry_text>", file=sys.stderr)
        sys.exit(1)

    log_path = sys.argv[1]
    entry = sys.argv[2]

    # Ensure parent directory exists
    parent = os.path.dirname(log_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    # Stat the file before writing
    pre_exists = os.path.exists(log_path)
    pre_size = os.path.getsize(log_path) if pre_exists else 0

    with open(log_path, 'a') as f:
        f.write(entry)
        if not entry.endswith('\n'):
            f.write('\n')

    post_size = os.path.getsize(log_path)
    action = "created" if not pre_exists else "appended"
    print(f"LOG_{action.upper()}: path={log_path} before={pre_size} after={post_size}")

if __name__ == "__main__":
    main()
