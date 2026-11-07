#!/usr/bin/env bash
# Run from the repository root. Explicit local installs/build/start; human auth pauses.
set -euo pipefail
cd "$(dirname "$0")"
printf '%s\n' 'This installs dependencies, builds local Vexa images and starts Docker services.'
printf '%s\n' 'It needs your own local Docker engine. No production host, public deployment or uploads.'
read -r -p 'Proceed on this device? Type yes: ' answer
[ "$answer" = yes ] || exit 1
python3 setup_local.py init
python3 setup_local.py doctor
python3 setup_local.py install
python3 setup_local.py fetch
python3 setup_local.py build
python3 setup_local.py up
.venv/bin/python setup_local.py bootstrap
python3 setup_local.py login
python3 scripts/install_hermes.py
printf '%s\n' 'Infrastructure prepared. Next authorize your model and Calendar; these require you.'
printf '%s\n' 'source ./activate.sh && hermes setup'
printf '%s\n' '.venv/bin/python meeting_app.py calendar-login --client-file private/google-oauth-client.json'
printf '%s\n' '.venv/bin/python setup_local.py notion'
printf '%s\n' 'Follow docs/CALENDAR.md. Recording stays disabled until you explicitly confirm consent.'
