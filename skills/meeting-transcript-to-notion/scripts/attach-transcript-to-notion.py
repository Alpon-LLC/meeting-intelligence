#!/usr/bin/env python3
"""
attach-transcript-to-notion.py — Transform a transcript, upload it as a
native file to Notion's CDN, and attach it as a file block on the meeting
notes page.

Usage:
  python3 attach-transcript-to-notion.py \\
    <transcript.json> <notion_page_id> \\
    [--title "Meeting Title"] \\
    [--roster /path/to/team-roster.yaml] \\
    [--output /tmp/meeting-readable.txt]

Workflow:
  1. Transform transcript.json → human-readable .txt (transcript-to-readable.py)
  2. POST /v1/file_uploads (mode:"single_part") → get file_upload id
  3. POST /v1/file_uploads/{id}/send with multipart/form-data → upload file
  4. PATCH /v1/blocks/{page_id}/children → append a file block (references file_upload id)

On success prints JSON to stdout, on failure exits with code 1 + error to stderr.

Reads NOTION_API_KEY from os.environ (loaded from Google Secret Manager at
gateway startup), with fallback to ~/.hermes/.env then gcloud secrets.
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
import tempfile
from pathlib import Path

# ── multipart/form-data helper (stdlib only, no requests) ──────────────


def build_multipart(fields, file_path, file_field="file", boundary=None):
    """Build a multipart/form-data body with one file and optional text fields."""
    import uuid
    boundary = boundary or uuid.uuid4().hex

    body_parts = []
    for name, value in fields.items():
        body_parts.append(f"--{boundary}\r\n"
                          f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                          f"{value}\r\n")

    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        file_data = f.read()

    body_parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: text/plain\r\n\r\n".encode()
    )
    body_parts.append(file_data)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())

    # Combine
    body = b"".join(
        p if isinstance(p, bytes) else p.encode("utf-8") for p in body_parts
    )
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


# ── Notion token ────────────────────────────────────────────────────────

def get_notion_token():
    """Fetch Notion API key from the gateway runtime environment."""
    token = os.getenv("NOTION_API_KEY") or os.getenv("NOTION_API_TOKEN")
    if token:
        return token

    token_file = os.getenv("NOTION_TOKEN_FILE")
    if token_file and Path(token_file).is_file():
        return Path(token_file).read_text().strip()

    print("Error: NOTION_API_KEY not found", file=sys.stderr)
    sys.exit(1)


# ── Notion API helpers ──────────────────────────────────────────────────

def notion_request(url, data, token, method="POST", content_type="application/json", timeout=30):
    """Generic Notion API request with error handling."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2025-09-03",
    }
    if content_type:
        headers["Content-Type"] = content_type

    body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Error: Notion API {method} {url} returned {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: network error on {method} {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def atomic_json(path, value):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".notion-attachment-", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def existing_file_block(page_id, file_name, token):
    cursor = None
    while True:
        suffix = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        response = notion_request(
            f"https://api.notion.com/v1/blocks/{page_id}/children{suffix}",
            None, token, method="GET", content_type=None, timeout=20,
        )
        for block in response.get("results", []):
            if block.get("type") == "file" and block.get("file", {}).get("name") == file_name:
                return block.get("id")
        if not response.get("has_more") or not response.get("next_cursor"):
            return None
        cursor = response["next_cursor"]
# ── Main ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage: attach-transcript-to-notion.py <transcript.json> <notion_page_id> [options]", file=sys.stderr)
        sys.exit(1)

    transcript_path = os.path.abspath(sys.argv[1])
    page_id = sys.argv[2].replace("-", "")

    title = None
    roster_path = None
    output_path = None
    receipt_path = None

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--title" and i + 1 < len(sys.argv):
            title = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--roster" and i + 1 < len(sys.argv):
            roster_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--receipt" and i + 1 < len(sys.argv):
            receipt_path = sys.argv[i + 1]
            i += 2
        else:
            print(f"Unknown option: {sys.argv[i]}", file=sys.stderr)
            sys.exit(1)

    if not os.path.isfile(transcript_path):
        # Transcript missing — surface a visible warning on the page instead
        # of silently producing a notes page with no transcript.
        print(f"TRANSCRIPT_MISSING: {transcript_path}", file=sys.stderr)
        token = get_notion_token()
        warning_block = {
            "children": [{
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {
                        "content": "⚠️ Transcript not available — capture failed or bot did not join this meeting."
                    }}],
                    "icon": {"type": "emoji", "emoji": "⚠️"},
                    "color": "yellow_background",
                },
            }]
        }
        notion_request(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            json.dumps(warning_block), token,
            method="PATCH", timeout=20,
        )
        info = {
            "success": False,
            "page_id": page_id,
            "file_name": None,
            "file_upload_id": None,
            "blocks_appended": 1,
            "status": "transcript-missing-warning-posted",
        }
        atomic_json(receipt_path, info)
        print(json.dumps(info))
        sys.exit(0)

    ready_suffixes = {".md", ".txt", ".jsonl"}
    if Path(transcript_path).suffix.casefold() in ready_suffixes:
        readable_path = transcript_path
        source_name = Path(transcript_path).name
        file_name = f"{Path(source_name).stem}.txt" if Path(source_name).suffix.casefold() == ".jsonl" else source_name
    else:
        # ── Step 1: Transform legacy transcript JSON ─────────────────────
        script_dir = os.path.dirname(os.path.abspath(__file__))
        transform_script = os.path.join(script_dir, "transcript-to-readable.py")
        cmd = [sys.executable, transform_script, transcript_path, "--json"]
        if output_path:
            cmd.extend(["--output", output_path])
        if title:
            cmd.extend(["--title", title])
        if roster_path:
            cmd.extend(["--roster", roster_path])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"Transform failed: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        transform_info = None
        for line in result.stdout.strip().split("\n"):
            try:
                transform_info = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if not transform_info or not transform_info.get("success"):
            print(f"Transform did not succeed: {result.stdout.strip()}", file=sys.stderr)
            sys.exit(1)
        readable_path = transform_info["output"]
        if not os.path.isfile(readable_path):
            print(f"Error: transformed file not found: {readable_path}", file=sys.stderr)
            sys.exit(1)
        file_name = "transcript.txt"
        if title:
            safe = title.lower().replace(" ", "-").replace("/", "-")[:40]
            file_name = f"transcript-{safe}.txt"

    # ── Step 2: Get Notion token ─────────────────────────────────────────
    token = get_notion_token()

    existing_id = existing_file_block(page_id, file_name, token)
    if existing_id:
        info = {"success": True, "page_id": page_id, "file_name": file_name,
                "file_upload_id": None, "block_id": existing_id, "blocks_appended": 0,
                "status": "already-attached"}
        atomic_json(receipt_path, info)
        print(json.dumps(info))
        return

    # ── Step 3: Create file upload ──────────────────────────────────────

    create_data = json.dumps({
        "mode": "single_part",
        "filename": file_name,
        "content_type": "text/plain",
    })

    create_resp = notion_request(
        "https://api.notion.com/v1/file_uploads",
        create_data, token
    )

    upload_id = create_resp.get("id")
    if not upload_id:
        print(f"Error: file_upload create response missing 'id': {json.dumps(create_resp)}", file=sys.stderr)
        sys.exit(1)

    upload_status = create_resp.get("status")
    print(f"File upload created: id={upload_id} status={upload_status}", file=sys.stderr)

    # ── Step 4: Send file content via multipart/form-data ────────────────
    body, content_type = build_multipart(
        {}, readable_path, file_field="file"
    )

    send_resp = notion_request(
        f"https://api.notion.com/v1/file_uploads/{upload_id}/send",
        body, token,
        content_type=content_type,
        timeout=60,
    )

    if send_resp.get("status") != "uploaded":
        print(f"Error: file send did not complete: {json.dumps(send_resp)}", file=sys.stderr)
        sys.exit(1)

    # The response includes the file_upload id
    file_upload_id = send_resp.get("id", upload_id)

    print(f"File uploaded: id={file_upload_id} ({send_resp.get('content_length', '?')} bytes)", file=sys.stderr)

    # ── Step 5: Append file block to Notion page ─────────────────────────
    # The file block references the file_upload by its id
    file_block = {
        "children": [{
            "object": "block",
            "type": "file",
            "file": {
                "file_upload": {"id": file_upload_id},
                "name": file_name,
            },
        }]
    }

    append_resp = notion_request(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        json.dumps(file_block), token,
        method="PATCH",
        timeout=20,
    )

    appended_count = len(append_resp.get("results", []))

    # ── Output ──────────────────────────────────────────────────────────
    info = {
        "success": True,
        "page_id": page_id,
        "file_name": file_name,
        "file_upload_id": file_upload_id,
        "blocks_appended": appended_count,
        "status": "transcript-attached-as-file",
    }
    atomic_json(receipt_path, info)
    print(json.dumps(info))
    print(f"Attached transcript '{file_name}' to page {page_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
