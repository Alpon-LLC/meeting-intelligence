# Architecture and finished result

## Flow

```text
Attendee Google Calendar (ordinary read-only OAuth)
  |
  v
scheduler/run.js -- original JS filters/windows/state/leases
  | prep                                  | start
  v                                       v
signed Hermes webhook                direct Vexa lifecycle worker
/internal-meeting-prep skill          context builder -> request preparer -> POST /bots
  |                                       |
context, agenda, optional DMs              v
                                    authenticated Vexa bot -> Google Meet
                                         | audio + live STT + raw signal
                                         v
                                    signed Vexa completion bridge
                                         | local immutable artifacts + manifests
                                         v
                                    original postcall worker
                                    batch STT -> canonical -> readability
                                         |
                                         v
JS finish readiness + dispatch lease -> signed Hermes finish webhook
                                         |
                                         v
meeting-finalize -> Notion draft + transcript attachments
  -> post-meeting-approval -> triage-context -> human review
  -> retrieve-meeting-flow-context -> approved task reconciliation (optional)
  -> slack-update-from-meeting-notes (optional authorized destinations)
```

Join/status/stop are direct Vexa API operations. Prep and post-meeting composition are agents reached through **webhooks**, not local CLI subprocess replacements. The original start worker's deterministic context-only prep branch is not selected in this public profile; agent prep runs the rich original skill.

## Entrypoints

- Setup: `bash setup.sh`; individual operations in setup_local.py.
- Runtime install: `scripts/configure_runtime.py` installs full original skill family, helpers, loopback gateway configuration and signed prep/finish routes. Private/redacted configuration siblings are generated together.
- Agent: `source activate.sh; hermes gateway run`.
- Scheduler: `npm start` (serialized tick loop) or `npm run once` (original run.js once).
- Vexa: compose.yaml, with local S3-compatible store, CPU STT, PostgreSQL, Valkey, runtime, admin, meeting API and gateway; separate interactive login profile.
- Completion: deploy/bridge.py receives signed provider events and materializes signal; JS scheduler controls finish dispatch after full readiness.

## Original behavior preserved

Prep 30–40 min before start; join from 8 min early to 10 min late; finish 1–40 min after scheduled end, with durable outstanding claims/deferred processing beyond scan windows. Owner involvement, cancellations, skip/review flags, receipt fencing and retry bounds remain. Public consent selector narrows scope to intentionally opted-in meetings. See PORT.md and the per-file hash manifest.

## Skills, not stubs

The exported original family includes meeting, meeting-auto-join, internal-meeting-prep, meeting-finalize, meeting-transcript-to-notion, post-meeting-approval, retrieve-meeting-flow-context and slack-update-from-meeting-notes. Each carries the original detailed workflow and supporting scripts. The public-runtime contract replaces tenant routing/path/auth requirements, not operational reasoning. Historical private incident references are omitted and inventoried; legacy bot paths are explicitly excluded from Vexa-primary execution.

## Accounts

Human authorizes (1) the Vexa browser's Google account/session, (2) separate Calendar read-only OAuth with their Desktop client, (3) their own Hermes model provider and (4) optional Notion/Slack integrations. No DWD, X.509, company credentials, private speech router or shared Google client embedded in the package.

## Finished experience

Attendee configures a unique bot name, confirms real consent, adds [record] event to their authorized calendar, starts the local gateway and JS scheduler. A prep webhook produces type-aware context when in its prep window. Vexa joins automatically near start; provider identity/start time establish admission. Speech becomes durable signal/transcript evidence; original postcall pipeline creates canonical/readable artifacts. Finish webhook produces structured notes with proposed actions and review context. Human reviews before task changes/distribution. Optional adapters must be explicitly configured; no other person's workspace or channel is a fallback.

No custom dashboard is included. UI is terminal status/logs, localhost Google-login viewer, Meet bot participant, and Notion notes when enabled. Device must remain awake. Container health alone is not end-to-end success. Same-room bot race/device capacity remain unverified until rehearsal.

## Data and limits

All credentials and meeting state live under ignored private/ and Docker volumes. Captured-signal audio persists even when ordinary recording is disabled. Local STT does not make configured Hermes model inference local. Down preserves volumes; provider/Notion copies require separate retention actions. No automatic training hook.

Retained approval/task prose is not a verified sender-authenticated approval service. Task writes remain disabled by default. Parent-page notes versus rich database schema need explicit setup choices. Full Docker/gateway/login/Calendar/provider/Notion rehearsal is still required. The first Python scheduler draft is retired, not the workflow being advertised.
