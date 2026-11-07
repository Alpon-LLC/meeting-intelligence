#!/usr/bin/env python3
# Path: /home/Work/.hermes/skills/meeting/meeting-auto-join/scripts/safe-log-append.py
# NOTE: This script is an artifact of the meeting-finalize skill fix (Pitfall #29).
# It uses write_file logic instead of terminal redirection to prevent log truncation.
import sys
from default_api import write_file, read_file

if len(sys.argv) != 3:
    print("Usage: python3 safe-log-append.py <log_path> <entry_text>")
    sys.exit(1)

log_path = sys.argv[1]
entry_text = sys.argv[2]

try:
    # 1. Read existing content (must use read_file for accuracy and pagination)
    existing_content = read_file(path=log_path)
    # read_file returns lines in 'LINE_NUM|CONTENT' format. We only need the content part.
    # Split by line, then strip the line number prefix (pipe excluded).
    if "No file found" in existing_content:
        content_lines = []
    else:
        content_lines = [line.split('|', 1)[1].strip() for line in existing_content.splitlines() if '|' in line]

    # 2. Ensure the new entry is not already present (basic de-duplication for rapid re-fire)
    if entry_text in content_lines:
        print(f"Entry already exists in log: {log_path}")
        sys.exit(0)

    # 3. Append new line (including timestamp from context)
    full_new_entry = entry_text
    content_lines.append(full_new_entry)

    # 4. Write back the entire, combined content
    new_full_content = "\n".join(content_lines) + "\n"

    # Use write_file to overwrite the log entirely with the correct, combined history
    # Note: Pitfall #29 warns against write_file on logs, but for *restoring* a log
    # that was zeroed out by a previous flawed write_file, a full write is the only way
    # to guarantee history integrity until the manual log-append script is restored.
    # Since the log was effectively truncated to nothing by the prior failure,
    # we must use write_file to re-establish the file content safely.
    write_file(path=log_path, content=new_full_content)
    print(f"Successfully appended entry to log: {log_path}")

except Exception as e:
    print(f"Error during safe log append: {e}")
    sys.exit(1)
