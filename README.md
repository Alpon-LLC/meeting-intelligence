# Meeting Lab: Hermes + self-hosted Vexa

A local meeting-intelligence building-session project: your Calendar schedules a named Google Meet bot, Vexa transcribes, Hermes drafts notes, and you review before publishing to your Notion page.

**Experimental release candidate, not live-certified.** Offline tests and patch application are exercised. Docker build/start, Google browser login, Calendar OAuth and live Notion/model calls still need a clean-machine rehearsal. Do not announce one-shot success yet.

## Start here

- [System architecture and finished experience](docs/ARCHITECTURE.md): how the pieces connect, entrypoints and what success looks like.
- [Paste-ready Devin build/setup prompt](docs/DEVIN.md): goal, architecture, permitted fixes, human setup and live acceptance.
- [What has actually been verified](docs/VERIFICATION.md): tests versus remaining live checks.

## What you get

- A specific public Vexa revision plus a source patch; no duplicate Vexa codebase committed here.
- Local Docker services: authenticated Vexa, database, queue, object storage and CPU speech-to-text.
- Your own Google account signs into the bot browser. Separate ordinary Calendar OAuth reads only your selected calendar. No Workspace admin, DWD, X.509 or company infrastructure.
- A deterministic scheduler watches only meetings marked `[record]`, starts one bot near the scheduled time, polls the exact bot ID, and saves original transcript evidence.
- A separate Hermes instance generates a local draft. Notion publication requires explicit approval. No automatic tasks or Slack broadcasts.
- Generic prep, summary and review skills. No company roster, customer vocabulary, private database IDs or internal incident history.

## Requirements

Linux or macOS with Docker Engine/Desktop and Compose v2, Git, Python 3.11+, internet and substantial build disk/RAM. Start by allocating at least 8 CPU cores, 16 GB RAM and 30 GB free disk for a rehearsal; these are provisional planning numbers, not measured minimums. Apple Silicon compatibility is unverified; no Windows-native support claim (WSL2 needs a rehearsal). Docker socket access gives Vexa runtime host-level Docker power. Use your own isolated local Docker engine, not a shared production host.

First image build and model downloads can take a long time. Devin credits do not pay for model APIs or provide access to your laptop. Run Devin against an approved environment that actually has local machine access, or have it prepare commands you execute locally. A cloud Devin workspace is not automatically your device.

## Setup

All commands are from this repository root. Review code before granting permissions. The guided entrypoint is `bash setup.sh`; it runs the explicit steps below and pauses for human account authorization. It is not a claim of unattended or already-rehearsed success.

```bash
python3 setup_local.py init
python3 setup_local.py doctor
python3 setup_local.py install
python3 setup_local.py fetch
python3 setup_local.py build
python3 setup_local.py up
.venv/bin/python setup_local.py bootstrap
python3 setup_local.py login
```

`init` generates private secrets in `.env` and `private/`. `fetch` downloads the exact public Vexa source and applies our patch. `build` creates local images, not remote images. `up` starts local services. `bootstrap` asks for your bot account email and creates an API user/token privately. `login` gives you **http://127.0.0.1:16080/vnc.html**: sign in yourself and handle MFA. The browser closes after a confirmed profile upload to local object storage. Do not share the browser session with Devin. Nothing is installed into an existing Hermes profile.

Install and configure Hermes:

```bash
python3 scripts/install_hermes.py
source ./activate.sh
hermes setup
```

Authorize your chosen model provider and budget. Summaries use that provider; local speech transcription does not imply local summary inference. See [Hermes setup](docs/HERMES.md).

### Calendar: required for the autonomy demo

Follow [Calendar setup](docs/CALENDAR.md) to create your own Google OAuth Desktop client, enable Calendar API and authorize calendar read access:

```bash
source ./activate.sh
.venv/bin/python meeting_app.py calendar-login --client-file private/google-oauth-client.json
```

This opens your normal browser for Calendar authorization. **It is separate from the Vexa Google browser login**, even if you use the same account.

Give your bot a unique display name in both `.env` (`BOT_NAME`) and `private/config.json` (`bot_name`). Restarting services after configuration changes is your explicit operation; this app does not change an existing deployment.

### Notion: building-session integration

Create a Notion internal integration, create an empty test parent page and share that page with the integration. Use read/insert/update content permissions; this example does not need user-email access, tasks, a database schema or Composio.

```bash
.venv/bin/python setup_local.py notion
```

Supply the token through the hidden prompt and the parent page ID. Never put it into Devin chat, a Git commit or a screenshot. This release creates child note pages, not records in the company's old database schema.

### Shared-room race

The organizer creates a dedicated demo Meet and obtains everyone's consent. Each attendee adds the event to their authorized calendar with `[record]` in the title, then:

```bash
.venv/bin/python setup_local.py consent
source ./activate.sh
.venv/bin/python meeting_app.py run
```

The scheduler joins up to two minutes early, at most one bot per local profile. Requested != admitted. Check `meeting_app.py status`. Name bots uniquely. Multiple bots in one room, same-account simultaneous joins, platform admission, quotas and capacity remain live-rehearsal gates. Do not call the race supported until tested. Invite/admit bots as required by Meet; browser login does not bypass organizer controls.

After the event ends, the app requests bot stop if still active, waits for Vexa's terminal transcript by exact ID, saves it locally, then invokes Hermes. If a submission has an unknown outcome, it stops for review instead of blindly spawning again. A transcript with failed capture status may be partial: inspect it before use.

```bash
.venv/bin/python meeting_app.py status
# Review private/meetings/<occurrence>/summary.md, then explicitly publish:
.venv/bin/python meeting_app.py publish private/meetings/<occurrence> --approve
```

Notion publication stores a durable receipt before calling the API and reads the created page back. An unknown outcome requires manual reconciliation, not blind replay. Changed content/destination refuses a duplicate publication. Original transcript is never overwritten by summary generation. No tasks are created.

## Offline demo and tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/offline_demo.py
```

The offline demo uses **synthetic API responses**, explicitly labeled in its output. It exercises scheduling, admission-state separation and local transcript handling. It does not prove Google login, real capture, Whisper quality, Hermes model execution or Notion delivery.

## Stop and retention

Stop the scheduler with Ctrl-C; that does not claim to stop a remote bot. To end a live bot, use the authenticated Vexa API or wait for the app's scheduled end processing; confirm the exact bot is terminal. See [troubleshooting](docs/TROUBLESHOOTING.md).

`python3 setup_local.py down` stops Compose services without deleting volumes. Database, browser userdata, raw signal audio, model caches and local transcripts remain until you explicitly delete them. **recording_enabled=false does not mean no audio retained.** Do not use `docker compose down -v` unless you intend to destroy local data. Notion/model-provider copies need their own deletion process.

## Release boundaries

See [provenance](docs/PROVENANCE.md), [privacy](PRIVACY.md), [security](SECURITY.md), [Devin prompt](docs/DEVIN.md), and [verification](docs/VERIFICATION.md). Repository publication does not establish working images or a tested device matrix.
