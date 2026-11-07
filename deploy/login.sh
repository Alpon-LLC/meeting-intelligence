#!/usr/bin/env bash
set -euo pipefail
umask 077
Xvfb :99 -screen 0 1920x1080x24 >/tmp/display.log 2>&1 &
fluxbox >/tmp/window-manager.log 2>&1 &
pulseaudio --start >/dev/null 2>&1 || true
x11vnc -display :99 -rfbport 5900 -forever -shared -nopw >/tmp/vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/websocket.log 2>&1 &
cd /app/core/meetings/modules/remote-browser
log=$(mktemp)
trap 'rm -f "$log"' EXIT
if ! ./node_modules/.bin/tsx src/provision-cli.ts >"$log" 2>&1; then
  printf '%s\n' 'Google login or durable profile upload failed. No success receipt written.' >&2
  exit 1
fi
if ! grep -Eq '^\[provision-login\] SUCCESS — [1-9][0-9]* auth-essential items at s3://' "$log"; then
  printf '%s\n' 'Profile upload was not confirmed.' >&2
  exit 1
fi
mkdir -p /auth-readiness
printf '{"verified":true}\n' > /auth-readiness/google-login-success.json
printf '%s\n' 'Google session saved in your local Docker storage. Login browser is now closed.'
