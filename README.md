# Meeting Intelligence: the original workflow, generalized

**Experimental port. Not yet live-certified on attendee devices.** This branch corrects the first draft's unnecessary Python-scheduler rewrite.

## Actual architecture

```text
Google Calendar OAuth
  -> scheduler/run.js (original JS scheduler, every 2 minutes)
     prep:   signed POST -> Hermes meeting-prep webhook -> internal-meeting-prep skill
     start:  direct Vexa worker -> context/preparer -> self-hosted Vexa POST /bots
     status/stop: direct exact-bot-ID Vexa API

Vexa authenticated bot -> Meet audio + live STT + captured signal
  -> original signed completion bridge -> local lifecycle/signal manifests
  -> original postcall worker -> batch STT -> canonical transcript -> optional AI readability
  -> JS finish gate -> signed POST -> Hermes meeting-finished webhook
  -> meeting-finalize -> Notion notes/transcript attachment -> organizer review/context
  -> optional approved task reconciliation and team update
```

**No local Hermes chat replacement.** Prep/finish use the existing agent webhook mechanism. Join/status/stop call Vexa directly. The actual original JS time windows, dispatch leases, executor receipts, deferred finalization, review flags and bounded-rejoin inspection are retained.

## Start here

- [Architecture and file map](docs/ARCHITECTURE.md)
- [Devin setup/repair prompt](docs/DEVIN.md)
- [Port audit and exact retained/changed behavior](docs/PORT.md)
- [Per-file extraction manifest](docs/PORT-MANIFEST.json)
- [Verification and live gaps](docs/VERIFICATION.md)

## Files

- `setup.sh` / `setup_local.py`: local setup, builds, Google bot login and account configuration.
- `scheduler/run.js`: **the ported production JS scheduler**, one tick. `scheduler/daemon.js` runs serial ticks.
- `scheduler/dispatch-lease.js`, `executor-receipt.js`: original concurrency/lease/receipt contracts.
- `scheduler/vexa-*`: direct start/status/stop, completion readiness, postcall and recovery workers.
- `skills/`: full generalized original meeting family, not miniature replacement prompts. Main skills retain hundreds of lines, operational stages and helper algorithms. Each begins with a public runtime contract resolving tenant settings and historical legacy-only paths.
- `deploy/bridge.py`: recovered signed Vexa completion bridge. It materializes evidence; the JS scheduler sends the finish-agent webhook only after postcall readiness.
- `patches/vexa.patch`: reviewed Vexa modifications against exact public upstream source; generic synthetic fixtures.
- `scripts/configure_runtime.py`: installs skills/helpers and signed prep/finish webhook routes into the isolated Hermes home; writes redacted config siblings immediately.
- `docs/`: architecture, setup, permissions, provenance, limits and Devin brief.
- `private/`: generated tokens/config, Hermes home, meeting evidence and notes; Git ignored.
- `meeting_app.py`: earlier draft retained temporarily for Calendar-login/Notion utilities and regression comparison. Its Python scheduler is disabled. **Do not run it as the meeting scheduler.**

## Device requirements

Install/start Docker Engine/Desktop with Compose v2, Node.js 22+, Git and Python 3.11+. Docker runtime access is effectively host-admin access: use your own isolated development engine, not production. Linux/macOS are targets; Apple Silicon and other architectures remain unverified. Expect substantial browser/model builds and disk/RAM usage. No company server/account/image access is required.

## Setup

```bash
git clone https://github.com/Alpon-LLC/meeting-intelligence.git
cd meeting-intelligence
bash setup.sh
```

The setup script initializes private secrets, installs Python/Node dependencies, fetches exact upstream Vexa source, applies the patch, builds images, starts Compose, bootstraps the API user, opens local Google login, installs isolated Hermes and configures the real skills/webhooks. It is not an already-rehearsed one-shot success claim.

Human steps:
1. Sign into the Vexa bot's Google browser at `http://127.0.0.1:16080/vnc.html`, handle MFA and require confirmed profile upload.
2. `source ./activate.sh && hermes setup`: authorize your own model/provider and budget.
3. Follow [Calendar OAuth](docs/CALENDAR.md), then `.venv/bin/python meeting_app.py calendar-login --client-file private/google-oauth-client.json`. Ordinary OAuth, no DWD/X.509. Add your actual `calendar_owner_email` to private/config.json.
4. Configure Notion token and parent page with `.venv/bin/python setup_local.py notion`. For faithful database/project/task enrichment, additionally configure your own notes/tasks/projects data-source IDs and schemas. No fixed company IDs. Optional Slack/CRM/email/task writes stay disabled until explicitly configured and approved.
5. Configure runtime **after model/Notion settings are ready** with `.venv/bin/python scripts/configure_runtime.py` if not already configured. Existing routes are not silently overwritten; inspect/reconcile them explicitly.

Run two terminals, both from the repo:

```bash
# Terminal 1: existing Hermes webhook gateway
source ./activate.sh
hermes gateway run
```

```bash
# Terminal 2: real JS scheduler
source ./activate.sh
.venv/bin/python setup_local.py consent
npm start
```

Use a consenting demo Meet, add the event to your authorized calendar with `[record]` in its title, give the bot a unique name and keep your device awake. Original windows: prep 30–40 minutes before start, join 8 minutes before through 10 minutes after start, finish 1–40 minutes after end plus durable deferred/claim recovery. Notion/Slack side effects require explicit tenant configuration and approval according to the public runtime contract.

## Verification

```bash
npm ci --ignore-scripts
npm test
.venv/bin/python -m unittest discover -s tests -p test_public_port.py -v
.venv/bin/python -m unittest discover -s skills/meeting-auto-join/tests -v
.venv/bin/python -m unittest discover -s skills/meeting-finalize/tests -v
```

Tests use synthetic events/provider responses. A real localhost HTTP test verifies original signed prep dispatch and duplicate suppression. These are not live Google/Vexa/model/Notion passes. Docker daemon is unavailable in the authoring container, so a full isolated-device rehearsal is still required.

## Privacy and publication

No real credentials, internal rosters, customer glossaries or private meeting narratives are intended in the export. Incident references excluded for confidentiality are catalogued in the manifest; key operational algorithms remain in skills/scripts. Full secret/license/dependency review remains a gate. Vexa `recording_enabled=false` does **not** mean no raw signal audio retained. Compose down preserves volumes. See PRIVACY.md and SECURITY.md. Never claim production image equivalence from an image tag alone.
