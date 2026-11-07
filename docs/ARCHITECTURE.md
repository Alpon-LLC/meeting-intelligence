# Architecture and finished experience

## What we are building

A meeting assistant running on an attendee's own device. It watches their Google Calendar, automatically sends their named bot to an explicitly opted-in Google Meet event, captures/transcribes speech, asks Hermes to draft meeting notes, and publishes those notes to the attendee's Notion page only after review.

The building-session demonstration is a shared, consenting room: each attendee configures their own local stack and Google account, adds the event to their calendar and observes their bot joining automatically. Multiple bots, account policies, admission and device architectures require a live rehearsal; the race is a demo goal, not an already-verified capacity claim.

## System map

```text
ATTENDEE'S DEVICE

Normal browser -> Google Calendar OAuth -> private/calendar-token.json
                                            |
                                            v
                                  meeting_app.py run
                                  checks Calendar every ~20s
                                  title contains [record]
                                  consent enabled + time window
                                            |
                                        POST /bots
                                            v
Docker network: meeting-lab_vexa
  gateway -> meeting-api -> runtime -> ephemeral named Vexa bot -> Google Meet
                  |             |                    |
               Postgres       Valkey           authenticated browser
                  |                                  |
                  |                           local CPU transcription
                  |                                  |
                  +------------ transcript ----------+

  local S3-compatible storage: browser session + captured signal audio
  local login container: Google sign-in through localhost:16080/vnc.html

meeting_app.py polls GET /transcripts/by-id/<exact bot ID>
   -> private/meetings/<occurrence>/transcript.json + transcript.md
   -> isolated Hermes CLI + generic meeting-summary skill
   -> summary.md (draft)
   -> HUMAN REVIEW
   -> meeting_app.py publish <directory> --approve
   -> attendee's Notion parent page -> child notes page + local receipt
```

External services: Google Meet/Calendar; the attendee's configured Hermes model provider (unless genuinely local); Notion after approved publication. Vexa, storage and CPU transcription are local services. This is not automatically offline or fully local AI inference.

## Components and entrypoints

- `setup.sh`: guided installation/build/start sequence, pauses for human authentication. Not the running scheduler.
- `setup_local.py`: individual init/doctor/install/fetch/build/up/down/bootstrap/login/notion/consent operations.
- `compose.yaml`: database, queue, S3-compatible storage/bootstrap, local CPU STT, Vexa control services and optional login browser. Host APIs/login bind loopback. Runtime mounts Docker socket, a privileged capability.
- `patches/vexa.patch`: modifications applied to original public Vexa commit `f1a62fbbdfd2c98cb162582d82c31fe8102acc89`; downloaded source lives in ignored `.build/vexa`. Includes lobby-path, vocabulary and capture/lifecycle changes, with generic synthetic fixtures.
- `deploy/Dockerfile.meeting-api`: reviewed source overlay on upstream Vexa meeting-api image.
- `deploy/login.sh`: launches browser viewer and requires a positive durable session upload before writing login success.
- `deploy/init_storage.py`: creates local userdata/signal buckets; no deletion.
- `scripts/install_hermes.py`: fetches exact candidate Hermes source and installs a separate venv/home. Does not modify an existing agent profile.
- `activate.sh`: selects the isolated Hermes home and executable paths for this shell.
- `meeting_app.py`: Calendar OAuth, event eligibility, scheduler, Vexa submit/status/stop, transcript persistence, Hermes draft invocation and approved Notion publication.
- `skills/meeting-summary`: used during automatic post-meeting drafting.
- `skills/meeting-prep`: available generic preparation instructions; **the scheduler does not automatically run a rich prep briefing in this candidate**.
- `skills/meeting-review`: human-review instructions; publication approval is also required by CLI code.
- `docs/DEVIN.md`: reproduction/repair brief for Devin, not application runtime instructions.
- `tests/`: offline unit and synthetic localhost HTTP tests. They are not live provider certification.

## Authentication: three separate setups

1. Vexa Google browser sign-in: the human opens the local login viewer, signs into their Google account, handles MFA. Profile goes into local object storage. Used to join Meet.
2. Calendar OAuth: the human enables Calendar API in their own Google Cloud project, creates a Desktop OAuth client and approves read-only calendar-event access in their normal browser. No DWD/X.509. Vexa sign-in does not grant Calendar access.
3. Optional Notion and model authorization: human configures their own integration/shared parent page and Hermes model account/budget. No company accounts or Composio requirement.

## Scheduler behavior and boundaries

`meeting_app.py run` stays running while the device is awake. Only timed events with valid Google Meet URLs, [record] title marker, explicit consent enablement and eligible time window are considered. Cancelled/self-declined events are skipped. It can join two minutes before start. Event ID plus scheduled start identifies the occurrence; Meet URL alone does not.

The app persists submission intent before POST. A returned bot ID means requested, not admitted. Active status plus start time is admission. At scheduled end, an observed active bot receives a stop request. Terminal transcripts are fetched by exact bot ID and saved. Empty terminal evidence waits up to five minutes before review. Unknown submission outcomes and interrupted side effects stop for review, not blind replay. One unresolved meeting blocks another automatic submission on that local profile.

Current limitations: no automatic schedule-change/cancellation teardown reconciliation, no background OS service installer, no automatic organizer admission, no full production capture-quality/canonical second pass, no automatic rich prep briefing, no Slack approval bot/task creation. Do not describe those as implemented.

## What the attendee should see when done

1. One local stack, healthy according to actual Docker service checks; local login succeeds with a durable profile receipt.
2. A separate Hermes instance authorized for their model, plus Calendar OAuth authorized for their calendar.
3. Their shared Meet event includes [record], real participant consent is confirmed and a unique bot name is configured.
4. With scheduler running, their named bot appears in the meeting automatically. Organizer admits it if required. Saved status distinguishes requested from actually active.
5. Real synthetic discussion appears in the exact occurrence's transcript. After stop/completion, Hermes produces `summary.md` with summary, decisions, proposed actions, unknowns and uncertainty.
6. The human reads/edits the draft, explicitly approves publication and gets a Notion child notes page in their own designated parent. Receipt and remote page identity are verified.
7. The bot is terminal, no duplicate remains, and attendee knows where session/audio/transcript data persists. No tasks or shared messages are silently created.

There is no custom web dashboard in this release. The UI consists of terminal setup/status, the local Google login viewer, the visible Meet participant and the Notion notes page.

## Local artifacts

`.env` and `private/` are ignored, never committed. `private/config.json` holds non-public local settings; token files hold credentials. `private/meetings/<occurrence>/` holds metadata, state, original transcript, draft and publication receipts. Docker volumes retain browser userdata, raw signal audio, database and model cache. Compose down preserves volumes. Recording-disabled does not mean audio-retention-disabled.

## Release proof

See VERIFICATION.md. Offline tests and Compose configuration validation passed. Full Docker image build, fresh Hermes install and live Google/Calendar/STT/Notion flow have not yet been proven in this environment. Devin must exercise and repair that actual path, not merely repeat the setup instructions or declare success from healthy containers.
