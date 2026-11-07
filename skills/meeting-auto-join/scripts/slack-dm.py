#!/usr/bin/env python3
"""Send a Slack DM via the Web API.

Usage: python3 slack-dm.py <channel_id> <message_text>

Reads SLACK_BOT_TOKEN from ~/.hermes/.env first, then falls back
to Google Secret Manager (CONFIGURED_SLACK_SECRET). The .env
file only contains '***' placeholders in production; the real token
lives in GCP Secret Manager.

Optional adapter: requires explicit Slack enablement and authorized recipient.

Example:
  python3 scripts/slack-dm.py CONFIGURED_SLACK_ID "*AUTH ALERT* — Google Meet session expired"
"""
import json, sys, os, urllib.request, urllib.error
from pathlib import Path

def get_token():
    """Use only the attendee's explicitly configured token or private key file."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if token:
        return token
    token_file = os.getenv("SLACK_TOKEN_FILE")
    if token_file and Path(token_file).is_file():
        return Path(token_file).read_text().strip()
    return None


def send_dm(channel_id, message):
    if os.getenv("MEETING_SLACK_ENABLED") != "true":
        raise PermissionError("Slack delivery is not enabled")
    token = get_token()
    if not token:
        print("ERROR: SLACK_BOT_TOKEN could not be resolved (neither .env nor GCP Secret Manager)", file=sys.stderr)
        sys.exit(1)

    payload = {
        "channel": channel_id,
        "text": message,
        "mrkdwn": True,
    }

    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"Slack DM sent to {channel_id} (ts={result.get('ts')})")
            else:
                print(f"Slack API error: {result.get('error')}", file=sys.stderr)
                sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 slack-dm.py <channel_id> <message_text>", file=sys.stderr)
        sys.exit(1)
    send_dm(sys.argv[1], sys.argv[2])
