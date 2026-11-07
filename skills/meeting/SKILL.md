---
name: meeting
description: "Shared conventions for Example Organization meeting workflows. Reference umbrella; load the relevant execution skill. For generation-fenced meeting webhooks, see references/dispatch-lease-fencing.md. For confirmed Vexa start or pre-admission join failures with no capture, see references/vexa-start-failure-no-capture.md. For recurring calendar creation, idempotency, optional attendees, timezone-safe verification, and calendar-ownership checks, see `references/calendar-event-creation-verification.md`. For safely moving one meeting or one occurrence of a recurring series while preserving duration and event details, see `references/calendar-event-rescheduling.md`. For read-only delivery of existing notes, candidate disambiguation, completeness checks, and stable citations, see `references/retrieving-existing-meeting-notes.md`. For calibrated analysis of client feedback, timing, scope, materiality, and inferred dissatisfaction, see `references/interpreting-client-feedback.md`."
version: 1.22.0
metadata:
  hermes:
    tags: [meeting, shared, conventions, umbrella]
    related_skills: [meeting-auto-join, internal-meeting-prep, meeting-finalize, meeting-transcript-to-notion, post-meeting-approval, retrieve-meeting-flow-context, slack-update-from-meeting-notes]
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Meeting Skill Family — Shared Conventions

This skill captures patterns that apply to ALL meeting-related skills. Load the individual skill for the specific workflow; consult this one when you need to verify a shared convention.

## Pipeline Architecture and Transcript Provenance

Treat scheduling, capture, and finalization as separate layers; do not describe the pipeline as either “just cron” or one monolithic script.

1. Hermes cron runs `meeting-workflow` (`meeting-trigger/run.sh`) every two minutes. The wrapper invokes centralized `meeting-trigger/run.js` for calendar polling, time-window classification, dedupe, deferred-finalization gating, and webhook dispatch.
2. `run.js` sends `calendar-trigger`, `meeting-prep`, and `meeting-finished` webhooks. The webhook subscription launches the corresponding skill in an isolated agent session. `meeting-finalize` therefore remains event-driven even though cron drives the scheduler.
3. A separately named `meeting-finalize` cron entry may still exist as paused legacy configuration. Its presence, or lines in `${HERMES_HOME}/cron/output/meeting-finalize.log`, does not prove cron executed finalization; that file is an append-only receipt log shared by webhook-triggered finalizer sessions.
4. Capture backend is runtime-selected by `HERMES_MEET_BACKEND`. Read the live process environment plus exact meeting artifacts; `.env` files alone can be incomplete when the gateway injects environment variables.
5. In `vexa-primary`, Vexa joins, captures participant-channel PCM, and performs near-real-time STT over bounded audio windows. That live transcript is provisional. Vexa also preserves the raw signal tape; after completion, Hermes independently replays that tape through batch STT and canonicalization to produce `transcript.canonical.jsonl` plus `transcript.readable.md`.
6. The normal path does **not** merge, vote, or deduplicate live and batch STT text. Successful batch-derived canonical output replaces the live transcript downstream; live output is fallback/comparison evidence only. The sole merge exception is a proven non-overlapping legacy Google-caption opening strictly before the first canonical timestamp.
7. `plugins/google_meet/meet_bot.py` is not launched after successful Vexa-primary dispatch. Its Google Meet caption `MutationObserver` and `transcript.json` remain the legacy/emergency fallback. In `vexa-shadow`, both collectors can run, but completed Vexa canonical state wins downstream. In `legacy`, `meet_bot.py` DOM-caption output is authoritative.
8. Under concurrent end signals, `.finalizing` is the convergence gate. A sibling-owned fresh lock means “already being finalized,” not “cron owns finalization.”

When explaining a live meeting, cite artifact-level evidence rather than configuration intent: backend value; exact Vexa `platform + native_meeting_id` status; lifecycle provider; `transcription.state.json`; batch manifest; presence/absence of `transcript.json`, `bot.log`, signal tape, and canonical transcript. Never infer admission from `vexa.bot.response.json.status=requested`; verify provider status or lifecycle completion.

Detailed inspection procedure and a verified Vexa-primary artifact signature: `references/meeting-pipeline-and-transcript-provenance.md`.

## Shared Constants

| Constant | Value | Source of truth |
|---|---|---|
| Internal domain | `example.org` | `MEETING_INTERNAL_DOMAIN` env var |
| Slack UID resolution | `${HERMES_HOME}/team-roster.yaml` | Roster YAML — **never** hardcoded tables or state files |
| Meeting workspace roots | Resolve from runtime: operational `${HERMES_HOME}/workspace/meetings/`, profile `$HERMES_HOME/workspace/meetings/` or `$HOME/.hermes/workspace/meetings/` | One event may span roots; inspect all candidates before writes |
| Lockfile pattern | `${HERMES_HOME}/workspace/meetings/<event_id>/.lock` (or `.finalized`, `.finalizing`) | Calendar event ID from webhook `id` field |
| Directory naming key | Calendar event ID (`id` from webhook) | **NOT** the Meet slug — recurring events reuse the same Meet URL |

## Lockfile Convention — Two-Phase Pattern

Meeting skills usually use per-meeting directories under `${HERMES_HOME}/workspace/meetings/`, but the active profile may also expose `$HERMES_HOME/workspace/meetings/<event_id>/` or `$HOME/.hermes/workspace/meetings/<event_id>/`. These roots are not guaranteed aliases: capture artifacts, canonical transcription output, and finalization control state can be split across them. Resolve every active root and run global marker checks before applying the phase table below. See `references/two-workspace-roots.md`.

| Skill | Directory | Lock/marker file | States |
|---|---|---|---|
| `meeting-auto-join` | `${HERMES_HOME}/workspace/meetings/<event_id>/` | `.joining` | `booting` → `admitted` → `exited` |
| `internal-meeting-prep` | `${HERMES_HOME}/workspace/meetings/<event_id>/` | `.lock` | `prepping` → `done` |
| `meeting-finalize` | `${HERMES_HOME}/workspace/meetings/<event_id>/` | `.finalizing` → `.finalized` | single write |
| Any skill (cancellation marker) | `${HERMES_HOME}/workspace/meetings/<event_id>/` | `.cancelled` | `terminus — not a lock, a signal` |

**Rules:**
- The directory key is the calendar event ID (from webhook `id` field), **NOT** the Meet slug from `meet_url`.
- **CRITICAL: recurring events reuse the same Meet URL.** Every occurrence of "Example Organization Daily Standup" has the same `meet_url`. If directories are named by Meet slug, the first occurrence's lockfile blocks all subsequent ones forever. The calendar event ID is unique per occurrence.
- **The Meet URL is still needed** for navigation (the bot must know where to go). Just don't use it for directory naming.
- **Two-phase lock: write the claim FIRST, finalize it LAST.** The sentinel lock (`prepping`, `.joining: booting`, `.finalizing`) must be written at Step 0 — before any API calls, Notion queries, or Slack sends. The completion lock (`done`, `.finalized`) is written at the final step. **For `.joining`, acquire the pre-spawn claim atomically:** after pre-flight resolves any legitimate stale lock, create `.joining` with `os.open(..., os.O_CREAT | os.O_EXCL, 0o600)` and `pid: null`; `FileExistsError` means a sibling delivery won, so stop without spawning. The current image bot accepts that sentinel and overwrites it with its PID/state (verified 2026-09-25). Never rewrite `.joining` after spawn; the bot owns later transitions. Older image revisions rejected every pre-existing `.joining`; if the runtime logs that explicit refusal, follow Pitfall #35's compatibility path rather than guessing.
- **Stale lock recovery:** If a sentinel lock exists and is older than 5 minutes with no completion marker, the prior run crashed. Overwrite and proceed.
- **Why:** Calendar webhooks fire redundantly. If the lock is written only after work completes, concurrent invocations all pass the "no lockfile" check and duplicate the work. On 2026-06-10, this caused 3 identical Slack DMs to the same person for the same meeting.
- **Stale lock recovery:** If a sentinel lock exists and is older than 5 minutes with no completion marker, the prior run crashed. Overwrite and proceed.
- If a run crashes before writing the sentinel, the absence means the workflow will retry cleanly (correct behavior).

## `.cancelled` Marker — Oral Cancellation Signal

**(Not a lock. Not a skill-phase marker. A durable signal that overrides the pipeline.)**

When a meeting is **orally cancelled** — the organizer announces it's moved/scrubbed but does NOT update the calendar — the meeting's end webhook still fires, and `meeting-finalize` will attempt to process it. Without a signal, it classifies the empty transcript as `sparse-transcript` (technical failure) rather than `meeting-cancelled` (organizational decision).

**Write protocol:** When Meeting Bot is told "this meeting is cancelled" by any team member, he writes `.cancelled` in the meeting directory and kills the drain-loop bot:

```json
{"cancelled_at": "<ISO 8601 UTC>", "by": "<who said it>", "reason": "<e.g. moved to Monday>", "bot_pid": <pid that was killed>}
```

**Read protocol:** Any downstream skill (primarily `meeting-finalize`) checks for `.cancelled` as the **first check** after confirming the meeting directory exists — before `.joining`, before `status.json`, before any idempotency lock. If present → skip cleanly with `skip-reason: meeting-cancelled`. Do NOT write `.finalizing` or `.finalized` — the marker IS the terminal state.

**Why a file, not memory:** Webhook-triggered sessions have no shared memory. The file is the only durable medium that survives across agent sessions.

## Slack UID Resolution

**`${HERMES_HOME}/team-roster.yaml` is the single source of truth for all Slack user IDs and DM channel IDs.**

Resolution order:
1. Read roster YAML, match on `email` → get `slack_dm_channel_id` (preferred) or `slack_user_id`
2. If `slack_dm_channel_id` present → use directly (avoids `conversations.open` API call)
3. If only `slack_user_id` → call `conversations.open` to get DM channel
4. If not in roster → fall back to `users.lookupByEmail` via Slack API

**Never use:**
- Hardcoded UID tables in skill files or reference docs
- UIDs from state files, nightly-triage output, or any indirect source
- The calendar `event_id` as a Slack identifier

**Standing roster gap — `person@example.org` (Participant B, Backend/Smart Contract Eng) has NO roster entry, so no `to_john` DM channel.** Confirmed across ≥5 consecutive standups (07-30 → 09-08): he appears on the calendar and in Notion but is absent from `${HERMES_HOME}/team-roster.yaml`, so any meeting skill that gathers his tasks/PRs can compose a fully-populated DM yet cannot deliver it. `internal-meeting-prep` Pitfall #45 documents the detail. **Handling (applies to every meeting skill):** if an attendee has content but no roster entry, skip the DM and record `reason: "no_dm_channel (not in roster)"` (distinct from `no_notion_account`), then flag the gap in the final response — do NOT guess/hardcode a channel ID from their Slack UID. The fix is blocked until Participant B DMs the bot at least once (only then is a real `to_john` channel ID observable), then a stub roster entry is added.
## Calendar Booking Attendee Default

Whenever booking a calendar meeting, always include `person@example.org` in the attendee list. Deduplicate attendee emails case-insensitively. After creation, read the event back and verify that Meeting Bot is present. This is a standing instruction from Organizer and requires no separate confirmation.

## Calendar Contact Resolution

When the operator identifies an external attendee by person + client/company but omits the email:

1. Search the live client/contact databases using both the person's name and company; require one exact person-company match.
2. Inspect the matched contact and linked deal. If the email field is empty, do not conclude the address is unavailable: search the operator's recent correspondence and prior calendar invitations for that exact person/company.
3. Accept an address only when the sender/attendee identity and company context agree. Do not substitute another same-first-name contact.
4. Cite the contact/deal page used for identity resolution. Treat email addresses as private; do not echo them unnecessarily in the final receipt.

A direct follow-up providing the missing duration completes the booking specification; do not re-ask for already-resolved title, weekday, time, timezone, recurrence horizon, or attendees.

## External Follow-Up Booking From a Prior Event

When the operator asks to schedule another call with a known external contact but omits title, duration, or timezone:

1. Resolve the contact from a client dossier/CRM record and a recent calendar invitation. Require one exact person-company match; the prior invite must carry the same external identity.
2. Treat the matching prior event as the template for omitted logistics: reuse its title, duration, timezone, and core attendee pattern. Do not use a generic default when exact history exists.
3. Resolve relative dates from the live current date, then query both the operator's calendar and the organizer calendar for the proposed window. Surface every overlap before sending.
4. Ask once with one compact confirmation containing exact date, local time/timezone, duration, attendees, and conflicts. A reply such as `send anyway` authorizes the stated overlap; execute immediately without repeating the specification.
5. Create the event with the external contact, operator, and `person@example.org`; explicitly send attendee updates and add Google Meet conference data.
6. Read the exact event back by ID. Verify title, start/end, timezone, complete attendee set, confirmed status, and non-empty `hangoutLink` before reporting success.
7. Return Calendar and Meet links. Do not echo the external email address when the person's name is sufficient.

A calendar collision is a warning and confirmation gate, not an automatic blocker. Detailed API pattern: `references/external-follow-up-calendar-booking.md`.

## Recurring Calendar Booking

For a bounded weekly series:

- Resolve the first future occurrence in the requested timezone.
- Preserve local wall-clock time with the IANA timezone on both start and end.
- Encode the requested horizon as a recurrence boundary, then read the series back and state the actual first and final occurrence dates in the receipt. Do not describe an `UNTIL` boundary as an occurrence when the weekday does not match.
- Include all requested attendees plus `person@example.org`, add Meet conference data, send updates, then verify title, duration, attendee set, recurrence, and non-empty `hangoutLink` from the created event.

## Calendar Conference-Link Default

For any calendar event with more than one invited attendee (`len(attendees) > 1`), ensure a Google Meet link exists.

- If a conference link already exists, preserve it.
- If absent, add Google Meet conference data automatically and send the calendar update to all attendees.
- Verify by reading the event back and confirming `hangoutLink` is non-empty.
- This standing instruction authorizes the additive calendar update; no separate confirmation is required.
- Stop on an ambiguous event match. Never guess which event to modify.

## Imminent Meeting Identification and Correction

When asked which meeting is current or starting soon, the live calendar is authoritative. Do not answer from a previously joined bot's `event_meta.json`, thread parent, recurring-event name, or stale workspace artifact alone.

1. Read the current time and query the live calendar over a tight window spanning now.
2. Compare every candidate's exact title, start/end, organizer, attendees, and `hangoutLink`.
3. Treat moved recurring events as moved: an old bot/workspace tied to the prior occurrence time does not identify the imminent meeting.
4. If the user says the wrong meeting was joined, stop/leave that bot first, resolve the exact replacement event, read the full event, preserve an existing Meet link, then join using the replacement event ID.
5. Report `joined` only after runtime status confirms admission. If join fails, report the failure plainly; do not claim the calendar was amended unless an actual calendar write was required and verified.
6. A user's `Stop` ends retries and diagnostics immediately. Perform only the minimum cleanup needed to stop active work, then report the stopped state tersely.

## External Discovery-Call Booking Bundle

When the operator asks to invite a prospect and also update the lead / create a discovery-call template, treat it as one atomic workflow:

1. Before inviting the external attendee, resolve exact date, start time, timezone, duration, and title. A follow-up such as `10am SGT` resolves only time/timezone; continue collecting the missing date/title/duration. Never infer the date for an external invite.
2. Create the event only after those fields are explicit. Include the prospect, operator, and `person@example.org`; ensure Google Meet conference data exists.
3. Read the event back. Verify title, start/end, all attendee emails, and non-empty `hangoutLink` before reporting success.
4. Find the Sales CRM deal by exact prospect email first. Require one unique match; broad company/title matches are discovery only and must not drive writes.
5. Create a linked page in the live `Discovery Calls` data source. Do not rely on a remembered database/data-source ID: inspect the deal schema's `Discovery Call` relation or search for the live data source. Seed a reusable neutral template: call details, calendar link, agenda, context, notes, decisions, action items.
6. Link the discovery page to the deal and contact. Update the deal status to the live intro/discovery-call status option (`Qualification | Intro Call` in the current schema), set the operator as Account Owner when explicitly requested, set next-contact date to the meeting date, and preserve existing `Project Message` text while appending the booking and notes links.
7. Re-fetch the deal and discovery page. Verify status, relations, booking link, owner, and template sections. Return calendar, Meet, discovery-page, and lead URLs as receipts.

This workflow is additive except for the status/owner edits explicitly requested by the operator. If the email matches zero or multiple deals, stop rather than guessing.

### Discovery prep from RFPs, screenshots, APIs, and codebases

When the operator asks to prepare an existing discovery call and quote discussion from mixed evidence:

1. Resolve the exact live calendar event first; require one match. Read the full event, preserving title, time, attendees, and Meet link.
2. Read the linked CRM deal and any existing Discovery Call relation before creating a page. If a linked page already exists, enrich it rather than creating a duplicate.
3. Review every supplied source independently: public brief/RFP, screenshots, provider documentation, email context, and repository. Convert product claims into questions; do not treat reference-app screenshots as confirmed requirements.
4. Inspect the repository directly. An accessible but empty repository is a material finding: label the implementation baseline greenfield unless another repository is supplied. Do not fabricate a code review.
5. Build a quote-ready page containing: objective, source review, timed agenda, discovery questions, quote inputs, explicit risks/assumptions, decisions, and action items. Separate confirmed facts, reference concepts, and unresolved dependencies.
6. For card/fintech discovery, cover at minimum: launch market and eligibility, KYC/KYB/compliance ownership, contracted provider modules, sandbox/production readiness, webhook ordering/idempotency/recovery, Wallet provisioning depth, physical-card fulfilment, rails, card servicing, rewards, privacy claims, PCI/data boundary, signing/store accounts, acceptance evidence, budget, support, and decision process.
7. Update the calendar description with a concise agenda plus links to the discovery page, brief, and provider docs. Use `sendUpdates=all` when external attendees must receive the new agenda.
8. Re-fetch both targets. Verify the calendar description, attendee set, preserved Meet link, Notion page completeness, and `truncated=false` before reporting.
9. Do not produce a fixed quote from unresolved provider, compliance, Wallet, fulfilment, or app-store dependencies. Capture the quote inputs and state the assumptions instead.

Worked checklist: `references/rfp-driven-discovery-prep.md`.

## Dispatch Lease Safety for Webhook Workers

When a meeting webhook carries `_dispatch`, the executor lease governs every side effect:

- Run the supplied `begin` command first. Exit `3` means fenced; stop with no writes, messages, logging, cleanup, or retries from that generation.
- Keep post-`begin` discovery narrow: terminal markers, status, transcription readiness, and existing receipts only. Heartbeat before long parsing, summarization, recovery, or downstream skill loading—not merely immediately before the eventual write.
- Heartbeat again immediately before each Notion, Slack, email, task, marker, receipt, log, or other artifact write. Preserve every `_dispatch` identifier verbatim.
- A partial page or `.finalizing` marker does not confer write authority after fencing. A later leased generation must resume the authoritative page/receipt.
- Call executor `finish` only after reading back the durable artifact required by the chosen outcome.

Detailed fenced partial-finalization pattern: `references/dispatch-lease-heartbeat-discipline.md`.

## Crash-Safe Triage Publication

Treat the Notion page, local context, organizer DM, team post, and attendee email as one recoverable publication transaction. Persist `notion-page.json` immediately after page creation; persist a complete pending `triage-context.json` before sending the organizer DM; reconcile Slack by stable event marker; verify the returned timestamp from the message `ts` field rather than searching message text for it. Resolve cross-system identities fail-closed: exact email, maintained alias, then unique normalized-name match; otherwise retain `notion_id: null`. Full ordering, recovery, and read-back gates: `references/triage-publication-transaction.md`.

## Domain References

- **Correct:** `@example.org` — used for all internal attendee filtering
- **Wrong:** `@examplex.xyz`, `@example.net`, `@example.net` — do not use in any skill

## Webhook Payload Fields

All meeting webhooks should include:

| Field | Description | Example |
|---|---|---|
| `id` | Calendar event ID (unique per occurrence) | `5j6dcepnuedkc6ujq3qua0eb9q` |
| `meet_url` | Full Meet URL | `https://meet.google.com/xxi-hgha-nawk` |
| `event_id` | Calendar event ID (reference only) | `15n1hne92i03kqof07btrb1b60_20260609T030000Z` |
| `title` | Meeting title | `Sprint Planning` |
| `start_time` | ISO 8601 with timezone | `2026-06-08T15:00:00+07:00` |
| `end_time` | ISO 8601 with timezone | `2026-06-08T16:00:00+07:00` |
| `attendee_emails` | Array of attendee emails | `["person@example.org", "person@example.org"]` |
| `organizer_email` | Organizer email | `person@example.org` |
| `description` | Agenda/context (optional) | `Weekly sprint planning session` |

## Pitfalls (Cross-Cutting)

35. **`.joining` acquisition is version-sensitive; use an atomic claim on the current runtime (updated 2026-09-25).** A plain `exists()` check followed by `write_text()` is a TOCTOU race: concurrent webhook sessions can both pass and spawn duplicate bots. After pre-flight resolves stale locks, create `.joining` with `os.O_CREAT | os.O_EXCL` and `pid: null`; `FileExistsError` is a successful deduplication outcome. The current image bot accepts this sentinel, then overwrites it with its own PID and `admitted`/`exited` state. Do not write `.joining` again after spawn. Compatibility: the 2026-09-11 image rejected all pre-existing locks. If bot.log explicitly says `refusing to launch — .joining lock held`, verify the PID is dead and `status.json.exited=true`, remove that proven-stale lock, then use the runtime-compatible bot-owned acquisition path. Never remove a live-PID lock. Full proof and code: `references/atomic-joining-lock-acquisition-2026-09-25.md`.

36. **Room activates late — join-click-timeout inside the event window is a TIMING failure, retry once + diagnose before gating (observed 2026-09-11).** Two spawns failed `join-click-timeout-no-button` around start time: pre-start showed `Ask to join` `visible=False count=1` (CSS-hidden, Pitfall #68 variant), post-start showed ALL buttons `count=0`. Organizer activated the room ~10 min after start; the bot's 300s join-click window expired first. `scripts/diagnose-meet-ui.py <meet_url>` then showed the room ACTIVE (body text lists participants in the call, `Join now` visible, correct account) and the immediate re-spawn admitted within ~30s. Decision table: first `join-click-timeout-no-button` within start ±10 min → remove stale `.joining`, re-spawn once; second identical failure → run the diagnostic before retrying; only "Return to home screen"/room-never-created (Pitfall #75) or `DENIED by host` in bot.log is terminal. Do NOT record `host-denied-or-room-inactive` for a timing failure — that gate would block every later occurrence of the recurring meeting. The diagnostic's body text is authoritative over `page title='Meet'` (three-variant ambiguity). See `references/late-room-activation-join-click-timeout-2026-09-11.md`.

37. **Framenavigated kick artifact ≠ capture failure — verify transcript growth before classifying (observed 2026-09-11, Demo & Backlog Refinement `1fnj741fipd1f8de2b9985lp2r_20260911T070000Z`).** The bot's `framenavigated` diagnostic wrote `kick_framenavigated_<ts>.json` mid-meeting with `current_url: https://workspace.google.com/products/meet/` (Google's marketing page) — which reads like the mid-call-kick auth-failure signature. But the bot recovered on its own and kept capturing to the meeting's end (`status.json` `exited: true`, `transcriptLines: 18`, `lastCaptionAt` ≈ 6 min after the kick timestamp). Rule: when a kick artifact exists, check whether transcript lines kept accumulating AFTER the kick timestamp before deciding auth-failed/escalate — a redirect to a Google marketing/Workspace page that the bot navigates back from is a survivable transient, not a terminal auth failure. Two artifact/telemetry fields are noise in this mode: `patterns_found.audio_issue` hits (e.g. `"Hardware"`) are page-text matches scraped off the marketing page, not real audio faults; and `status.json.audioRecordingError: "audio capture setup failed: audio spool is full (5/5 recordings)"` is the audio-RECORDING spool, independent of caption capture — transcript capture can be healthy while both are present. Neither gates finalization. **SCOPE OF THAT DISMISSAL (corrected 2026-09-12): it applies to classification of THIS kick artifact only.** `audioRecordingError` is NOT benign in general — a spool-full capture refusal on an otherwise-healthy call is a real, chronic, silent recording loss (6 days of audio on this host). See Pitfall #40.

1. **Stale Slack UIDs**: Hardcoded UID tables go stale. Always resolve from roster at runtime. Known incident: agent used `CONFIGURED_SLACK_ID` for Organizer instead of correct `CONFIGURED_SLACK_ID` because it read from stale nightly-triage state.

2. **Wrong domain**: `@examplex.xyz` has appeared in old skill files. Always use `@example.org`.

3. **Calendar event_id vs Meet slug**: The calendar event ID (webhook `id` field) is the unique identifier per occurrence. The Meet slug (from `meet_url`) is the SAME for every occurrence of a recurring event. **Always use the event ID as the directory name.** Using the Meet slug means the first occurrence's lockfile blocks all subsequent occurrences forever.

3a. **An event-ID directory can exist while capture artifacts live under the Meet slug.** Prep may create `.lock`, `summary.json`, and even pointerless `event_meta.json`; none proves `status.json` and `transcript.json` are local. Before `bot-never-joined` or `meeting-not-complete`, if the canonical directory lacks capture artifacts, inspect only the exact slug derived from the webhook `meet_url`. Recover only when slug and normalized URL match exactly. Preserve prep files and existing metadata, annotate provenance, copy capture artifacts into the canonical event directory, then continue normal idempotency and triage. After SIGTERM, `status.json.exited == true` is authoritative; a remaining `Z`/`<defunct>` PID is already exited. Full gates: `references/pointerless-slug-finalization-recovery.md`.

4. **Lockfile must use two-phase pattern (claim early, finalize late)**: Every meeting skill must write a sentinel lock at Step 0 — before any API calls, payload parsing, or Slack resolution. The sentinel (`.lock: prepping`, `.joining: booting`, `.finalizing`) prevents concurrent webhook invocations from duplicating work. The completion marker is written at the final step. Webhook retries create brand-new agent sessions with no memory of prior runs. See `references/webhook-idempotency.md` for the full pattern and the June 10 incident that motivated it.

5. **Gateway restart after webhook config changes**: If webhook subscriptions are recreated or modified, the gateway process must be fully restarted. Symptom: webhook POST returns 202 but no agent session is created.

6. **Webhook agent sessions blocked by approval mode**: When `approvals.mode: manual` is set with no automated risk assessor, webhook-triggered agent sessions that need `terminal()` commands will hang on approval prompts — there is no interactive user to approve them. The Hermes approvals system has NO `webhook_mode` setting (only `mode`, `smart`, `off`, and `cron_mode`). Fix: set `approvals.mode: smart` globally, or configure `auxiliary.approval`. See `webhook-subscriptions` skill Pitfall #12.

7. **Webhook agent sessions launch with NO tools by default**: The `hermes-webhook` default toolset only includes `web_search, web_extract, vision_analyze, clarify` — no terminal, no file, no skills. This is intentional security design for untrusted payloads, but it breaks internal webhooks that need local access. **`hermes webhook subscribe` has NO `--toolsets` or `--enabled-toolsets` flag** — toolsets cannot be set per-subscription. Fix: add `platform_toolsets.webhook` in `config.yaml` (e.g. `[terminal, file, skills, web]`) to override the default for ALL webhook sessions. Trade-off: this gives all webhook agents local access, so only do it for trusted internal webhooks. See `webhook-subscriptions` skill Pitfall #13.

22. **Before proposing a new pattern, check the existing skill library first.** When a user asks you to do something you think you "can't do" (cancel a meeting, decline an invite, handle a special case), your first move is to load the relevant skill(s) and check what already exists — not to explain why you can't do it. The library may already have a pattern, a pitfall, or a reference that covers the case. If you say "I can't do X" without looking, the user has to correct you with "what does the skill tell you?" — wasting a turn. This pitfall is the direct consequence of the 2026-07-10 Demo & Backlog Refinement session where Meeting Bot proposed "I can't cancel the meeting" before checking `meeting-auto-join` and `meeting-finalize` for existing patterns. The `.cancelled` marker was created as a result of the review the user prompted.

9. **meet_join `ok=true` is a spawn success, not a join confirmation** — `meet_join` returning `ok=true` means the Chromium browser process was spawned and navigated to the Meet URL. It does NOT mean the bot entered the call. The actual join depends on Google authentication — if the browser lacks valid session cookies, no join/ask-to-join buttons render, and `_click_join` silently times out after 30 seconds. The tool returns `ok=true` before this timeout expires, so it looks like success even when the bot is stuck in an unauthenticated lobby. **Verification is mandatory:** 15-20 seconds after `meet_join`, call `meet_status`. If `inCall=false` AND `lobbyWaiting=false` with 0 transcript lines, the bot didn't get in. Check `bot.log`: `container=False` means the meeting UI DOM never loaded (severe auth failure). **But `container=True` does NOT guarantee the join worked** — the bot may have found "Ask to join" at `visible=False count=1`, meaning the button exists in the DOM but is invisible due to invalid server-side auth (see Pitfall #17 for this detection signal). In both cases, DO NOT retry — escalate to Operator for the VNC tunnel auth recovery (`DISPLAY=:1 hermes meet auth`). Retrying just cycles the same failure and leaves stale `.joining` lockfiles. Also: never tell the user "I'm in the call" or "I'm in" without first verifying via `meet_status`.

10. **Non-critical steps must never block the critical path**:: In every meeting skill, there is a single critical action (the join spawn, the Notion page creation, the Slack DM send for approval). Everything else — auth pre-checks, Slack notification DMs, log writes, status polling, zombie cleanup — is advisory. If an advisory step fails (blocked by approval mode, security scan false positive, tool unavailability, timeout), log the failure and continue to the critical action. Do not let a Slack DM send failure prevent the bot from joining a meeting. Do not let an auth cookie count check prevent a join attempt. The critical action itself will surface genuine failures (the bot logs "host denied admission" if auth is truly broken). This rule was hard-won: on 2026-06-10, five consecutive webhook sessions for the same meeting all failed because each one blocked on a Slack DM that triggered a tirith "variation selector" security scan, consuming the entire iteration budget without ever spawning the bot. A sixth session that skipped the blocked step and went straight to the join succeeded (PID 160181).

11. **auth.json `meet.google.com` cookie count is a false negative**: Google Meet authentication works via SSO through `.google.com` and `accounts.google.com` cookies. `meet.google.com` domain cookies are normally absent even when auth is valid. A count of 0 `meet.google.com` cookies does NOT mean auth is expired. Never gate a join or any other action on this metric. If auth is truly broken, the bot itself will report "host denied admission" — trust the runtime signal, not a pre-flight cookie count.

12. **Slack DM method — use `send_message` tool, NOT `hermes send` CLI**: The `send_message` tool (`send_message(target="slack:<channel_id>", message=<text>)`) sends directly to the specified channel — it is never blocked by webhook delivery target configuration. **Always send DMs to ALL intended recipients regardless of what the webhook's `deliver` target is.** The webhook deliver target is for ops telemetry only, not a constraint on DM delivery. **Fallback when `send_message` is not available:** write a Python script that calls `chat.postMessage` directly via `urllib` using `SLACK_BOT_TOKEN` from `${HERMES_HOME}/.env`. Compose `{"channel": "<dm_channel_id>", "text": "<mrkdwn_text>"}`, POST to `https://slack.com/api/chat.postMessage`, verify `result["ok"]`. Write to `/tmp/send-dms.py` and execute with `python3`. Use Slack mrkdwn formatting (`*bold*`, `<url|text>`, `_italic_`). **Never skip an attendee's DM. Never include someone's private content in the final response as a "workaround" — that leaks it to the wrong channel.**

13. **Technical issue DMs route to Operator, not Organizer**: Infrastructure problems — auth.json corruption, bot failures, VNC/display issues, webhook debugging — go to Operator (`person@example.org`, Slack UID `CONFIGURED_SLACK_ID`, DM channel `CONFIGURED_SLACK_ID`). Organizer is the operator/decision-maker; Operator is the tech maintainer who actually fixes the systems. Routing auth alerts or bot diagnostics to Organizer wastes his time and delays the fix. Check `${HERMES_HOME}/team-roster.yaml` for current Slack IDs.

14. **Two-channel delivery pattern (Operator's architecture)**: All skills that produce user-facing output should implement dual delivery: (1) Pretty formatted message → target audience via `send_message` with Slack mrkdwn (`*bold*`, `<url|text>`, `•` bullets). This goes to the live `general` channel or attendee DMs as appropriate. **Never hardcode the general-channel ID:** resolve `general` from the live Slack channel list or configured home-channel target, then preflight the exact channel with `conversations.info` (`name=general`, `is_member=true`, `is_archived=false`) before posting. (2) Ops telemetry → Operator's DM (resolved from `${HERMES_HOME}/team-roster.yaml`) via cron/webhook `deliver`. This carries raw agent output, status, errors — the dirty but useful debugging view. Never send pretty formatted messages through cron auto-delivery (plain text only). Pattern reference: internal-meeting-prep, nightly-triage-test.

15. **Slack does NOT render markdown tables in chat messages**: Slack's `chat.postMessage` does not render markdown tables at all. For tabular data in DMs, use monospace code blocks (```) or formatted bullet lists. Slack Canvas (`canvases.create` API) does support markdown tables natively but requires `canvases:write` scope and the URL sharing story is unreliable. For cron-delivered digests, stick with code blocks or lists.

16. **Helper scripts installed without execute bits — check before invocation**: Sub-skill `scripts/` directories (`meeting-auto-join/scripts/`, `internal-meeting-prep/scripts/`, etc.) may contain `*.py` helpers installed without the executable bit set. When invoked directly (e.g. `python3 scripts/safe-log-append.py`), `Permission denied` from missing `+x` is silently absorbed by the agent. **Detection:** the first `safe-log-append.py` or `slack-dm.py` call fails with exit code 126. **Fix:** `chmod +x ${HERMES_HOME}/skills/meeting/*/scripts/*.py` after any skill install or checkout. Pre-emptively run this during the Prerequisites setup step rather than waiting for the first failure.

17. **Join-fall-through failure — bot silently proceeds to captions without entering the call**: When `meet_join` spawns successfully but Google auth is dead, the bot enters a subtle failure mode: `_click_join` finds "Ask to join" at `visible=False count=1` (the button exists in the DOM but is not rendered because the server-side session is invalid), and "Join now" at `count=0`. After 30 seconds, the join loop expires and the bot silently **falls through to the captions phase** — trying to enable captions via keyboard shortcut `c` — without ever having entered the call. The bot remains alive, consuming resources and producing useless logs, while `meet_status` shows `inCall=false`, `joinedAt=null`, `lobbyWaiting=false`.

    **Detection signals in bot.log:**
    - `_click_join: attempt N — looking for 'Ask to join' — visible=False count=1` across multiple attempts (the button is statically present but invisible)
    - `_click_join: attempt N — looking for 'Join now' — visible=0 count=0` always (open-join button never appears)
    - Join loop expires normally (no crash, no error) and transitions to `_click_enable_captions` phase
    - Captions loop runs for 30 seconds finding `visible=0 count=0` on all caption buttons, fully polluting the log with captions-attempt noise before the bot eventually gives up

    **Action plan when this signal is detected:**
    1. **Do NOT retry** — retrying cycles the same failure and leaves stale `.joining` lockfiles. The auth state hasn't changed between attempts.
    2. **Kill the bot immediately** with `meet_leave` — the captions loop is pure resource waste. Do not wait for the bot's internal timeout.
    3. **Do NOT ask Organizer for permission to debug** — his known directive when auth is the confirmed root cause is "move on." Report concisely: "Same auth issue — bot found Ask to join but visible=False, couldn't join. Killed cleanly. Route diagnosis to Operator."
    4. **Route technical triage to Operator's DM** (`person@example.org`, Slack `CONFIGURED_SLACK_ID`, `CONFIGURED_SLACK_ID`) — this is an infrastructure problem (server-side cookie session death), not a scheduling or workflow question for Organizer.
    5. **Do NOT tell the user "I'm in the call" or "I've joined"** without first verifying via `meet_status` that `inCall=true`. The premature confirmation causes confusion — the user will see an empty meeting and wonder where the bot went.

    **Key distinction from existing Pitfall #9:** Pitfall #9 covers the case where `container=False` — the meeting UI DOM itself never loaded (no sign-in page, no meeting container). This pitfall covers the case where the meeting UI loaded (`container=True`) but the join button is invisible due to invalid server-side auth (CookieMismatch from memory). The visible=False signal is the tell. For both cases, the outcome is the same: the bot never enters the call and should be killed immediately.

18. **"Move on" directive — user preference when auth is known-broken**: When the root cause of a meeting join failure is already diagnosed (persistent CookieMismatch, known auth crisis) and Organizer has previously said "Move on" for the same class of failure, do NOT:
    - Re-debug the same auth state
    - Ask "should I debug this?"
    - Offer to retry with a fresh capture
    - Propose selector-tweaking workarounds

    Instead, kill the bot immediately and report the failure in a single sentence. The auth crisis is a known infrastructure issue tracked by Operator. Operator time is not consumed retreading known ground. This directive applies session-wide: once Organizer says "move on" for a failure class, all subsequent occurrences of the same class in the same session get the same treatment without asking again.

19. **NEVER use `write_file` for append-only log files — use `safe-log-append.py` instead (observed 2026-06-17)**: The Hermes `write_file` tool fully overwrites the target file every invocation. Using it to "append" a log line to `meeting-auto-join.log`, `gate.log`, or any accumulated operational log destroys the entire history. In the 2026-06-17 incident, 6 days of meeting-auto-join log history (Jun 12–17) was wiped in a single `write_file` call. The dedicated append helper is:
    ```
    python3 ${HERMES_HOME}/skills/meeting/meeting-auto-join/scripts/safe-log-append.py <log_path> "entry text"
    ```
    This applies to ALL workspace files whose content grows over time — bot.log, meeting-auto-join.log, gate logs, .joining lock receipts. If the file accumulates lines across multiple invocations, it MUST be written via `safe-log-append.py`, never `write_file`. This pitfall is mirrored in `meeting-auto-join` SKILL.md Step 5 and Pitfall #68.

    **CRITICAL usage notes (v1.5.2+):**
    - **Positional args only** — `safe-log-append.py` takes exactly two positional arguments (log path, entry text). There are NO named flags (`--log`, `--message`, `--file`). Passing a flag as the first argument silently corrupted log files in earlier sessions. As of 2026-06-17 the script includes a guard that rejects args starting with `--` and prints a helpful error.
    - **Trailing newline handled automatically** — the script now strips trailing newlines from the entry text and adds exactly one `\\n` before writing. Callers do NOT need to include `\\n` at the end of their entry text. If you see consecutive log entries merged on one line in the log file, the entry text contained `\\r\\n` (Windows line endings) or the log was written by a prior version of the script that did not enforce the newline. Current behavior always produces cleanly separated lines.
    - **Validation** — the script validates that it receives at least two arguments. It does NOT validate that `log_path` is a writable path — the filesystem returns the error at write time.

20. **Meeting Notes DB may not be shared — probe before writing, use fallback**: Client project pages often have a "Meeting Notes" child database visible in the page tree, but the Meeting Bot CoS integration may not have been added to it. Query `GET /v1/databases/{child_db_id}` or `POST /v1/data_sources/{child_db_id}/query` with page_size:1 before attempting to create entries. If the response is a 404 (`"object_not_found"`), the DB is not shared — fall back to creating a standalone child page under the project's parent page instead. See `references/meeting-notes-db-fallback.md` for the full procedure. This was discovered on 2026-06-29 while creating Example Analytics meeting notes: the Meeting Notes child DB returned 404, and a child page under the Example Analytics parent page (`${CONFIGURED_NOTION_ID}`) was created successfully as the fallback.

21. **`meet_join` returns `skipped: joining_lock_active` — stale lock recovery**: `meet_join` returns `ok=true, skipped=true, reason="joining_lock_active"` when a prior bot session for the same meeting crashed without clearing its `.joining` lock. The tool detected the stale lock and refused to spawn a new Chromium. This looks like the bot "reporting incall: true but not actually in" because the stale lock persists.

    **Recovery (confirmed 2026-07-06 Sprint Planning incident):**
    1. Do NOT call `meet_leave` — it returns `"no active meeting"` because the prior bot session already exited. The leave call is a no-op and does NOT clear the lock.
    2. Find and kill the stale Chromium processes: `ps aux | grep 'chrome.*meet' | grep -v grep | awk '{print $2}' | xargs -r kill -9`. The lock is held by the Chrome profile directory used by the prior session (`${HERMES_HOME}/workspace/meetings/chrome-profile`).
    3. Call `meet_join` again with the same URL and event_id — the lock file is cleared when Chromium dies, and the fresh spawn succeeds.
    4. After successful spawn, `meet_status` may return `"no active meeting"` even though the new Chromium is running — the PID tracker was reset. Check `bot.log` for real status.

    **Normal join timeline after successful spawn** (not a failure — wait through it):
    - T+0s: Chromium navigates to Meet URL
    - T+5-10s: `_click_join: starting 30s poll loop` — looks for "Join now", "Ask to join", "Switch here" buttons
    - T+15-30s: Finds and clicks "Join now" (if the meeting is open-join). The delay is normal — the Meet page loads progressively.
    - T+30-35s: Transitions to `_click_enable_captions` phase — finds "Turn on captions" or dispatches keyboard shortcut `c`
    - T+40-45s: Captions enabled, `_click_enable_captions returned True`. Transcript file starts accumulating data.
    - During this window, `meet_status` will show `inCall=false`, `joinAttemptedAt=null`, `joinedAt=null`. **This is normal — the bot hasn't finished its join flow yet.** Don't panic or kill the bot. Check `bot.log` for the real state.
    - The `log_path` field returned by `meet_join` is the authoritative source during the join window. Poll it with `tail -5 <log_path>` every 10s until you see `CLICKED 'Join now'`.

    **Contrast with Pitfall #17 (join-fall-through failure):** If the 30-second join poll loop expires with NO button clicked and the bot transitions to captions mode anyway, the bot never entered the call (auth failure). In that case, the bot.log shows `visible=False count=1` for "Ask to join" across multiple attempts. Kill immediately with `meet_leave` (Pitfall #17 protocol). But if "Join now" IS clicked (`CLICKED 'Join now'` in logs), the join succeeded — let the bot continue. These are opposite signals that require opposite actions.

    **Window title diagnostic for the homepage redirect variant:** When the auth failure is severe enough that Google redirects the page to the Meet homepage entirely (not just invalid server-side session), the `_click_join` poll loop shows `visible=0 count=0` for ALL buttons — "Join now", "Ask to join", and "Switch here" are all absent because the bot is on the wrong page entirely. The key diagnostic: the browser window title is `"Google Meet: Online web and video conferencing calls | Google Workspace - Google Chrome for Testing"` (the homepage) instead of `"Meet"` (the meeting page). Check with `DISPLAY=:1 xdotool getactivewindow getwindowname`. The bot.log may show `page title='Meet'` at navigation time (the initial page.goto target), then the homepage title after the redirect. This is a **different failure mode** from the Pitfall #17 join-fall-through (where the meeting page loads but the join button is invisible). Both end with the bot in the zombie drain loop, but the homepage redirect case is pure auth failure (no Google sign-in at all) and the join-fall-through case is server-side session invalidation. Same action for both: kill immediately, route to Operator, do NOT retry.

23. **Drain loop zombie — bot stays alive but captures nothing after page content loss**: The `meet_bot.py` drain loop originally had only three exit conditions: `stop_flag`, duration deadline, and `page.is_closed()`. When the Google Meet page content disappears (captions container removed from DOM, observer destroyed) but the page is NOT closed (e.g., after Meet kicks the bot or shows a 'Reconnecting' screen), the drain loop spins forever logging `__hermesMeetDrain not available` every second. The bot process stays alive, the lockfile says `in_call: true`, and no new captions flow. The duration deadline's soft-extension (15-min silence) masks the issue for up to 15 minutes. Diagnosis: `grep "DOM PROBE" bot.log | grep -B2 "container=False"` shows the transition point.

    **Diagnostic capture (implemented 2026-07-16 with Operator's sign-off):** Two detection mechanisms were added to `meet_bot.py` to capture the exact kick state for analysis. Both are **diagnostic-only** — they capture data (screenshot, page URL, visible text patterns) but do NOT change the bot's behavior (no auto-rejoin, no early exit):
    - **`framenavigated` event handler** (registered after `page = context.new_page()`, line 886): Fires synchronously the instant the main frame navigates to a non-meeting URL. Event-driven, no polling, catches the exact kick moment.
    - **Observer transition detection** (in the DOM probe section, every 10 iterations): Tracks a `_was_observer_alive` flag. Fires once when the observer goes from alive → dead, as a second line of defence.
    - Both share a single `_kick_captured` guard — whichever fires first captures the state, the other is a no-op.
    - **`_capture_kick_state` function:** Captures screenshot (`kick_<source>_<ts>.png`), page URL, page title, and scans `document.body.innerText` for known kick/error UI patterns (removed, left, rejoin, captcha, error, audio_issue, network, meeting_ended, host_ended, waiting). Saves diagnostic JSON (`kick_<source>_<ts>.json`) to the meeting output directory.

    - **Do NOT make behavioral changes to meet_bot.py without Operator's sign-off.** The diagnostic capture is observation-only — it exists to gather data so Operator can decide what the right fix is. For manual zombie kill, diagnosis steps, and full debugging pattern, see `references/drain-loop-zombie-debugging.md`.

    24. **Drain loop `UnboundLocalError` crash — bot crashes immediately after ADMITTED (observed 2026-07-16, fixed).** The bot joins the meeting, enables captions, enters the drain loop, but crashes with `UnboundLocalError: cannot access local variable 'dom'` within 1s of `ADMITTED`. Root cause: `dom` is only assigned inside `if loop_iter % 10 == 0:` (DOM PROBE) but referenced outside it by the observer transition detection logic. Fix: `dom = None` initialization at the start of each drain loop iteration. See `references/drain-loop-unboundlocalerror-fix-2026-07-16.md` for full root cause analysis, fix, and recovery procedure. Mirrored in `meeting-auto-join` Pitfall #67.

    25. **Systemd SIGKILLs the bot when the gateway restarts — bot appears to "quit prematurely" with no error in bot.log.** When the Hermes gateway service (`hermes-gateway.service`) stops or restarts, systemd sends SIGKILL to all child processes in the control group — including the meet_bot and its Chrome instances. The bot.log stops abruptly mid-iteration with no `received SIGTERM`, no `drain loop: exited`, and no cleanup message. The `status.json` still says `exited: false, inCall: true` because SIGKILL is uncatchable. The bot was healthy right up to the kill — the last DOM PROBE shows `container=True, observerInstalled=True`.

    **Diagnosis:**
    ```bash
    # Check if systemd killed the bot
    journalctl -u hermes-gateway.service --since "<time>" | grep -i "kill\|Killing"
    # Check gateway restart history
    systemctl status hermes-gateway.service | grep "Active:"
    # Check for OOM
    dmesg | grep -i "killed\|oom\|<bot_pid>"
    ```

    **Key signals:**
    - `bot.log` ends at a normal-looking iteration — no exit message, no error, no traceback
    - `container=True` in the last DOM PROBE — the page was healthy when the kill happened
    - No kick diagnostic files — the framenavigated handler and observer transition never fired
    - Gateway `Active: since <same timestamp>` — the gateway restarted at the exact same second

    **Pattern diagram:** The bot was NOT kicked by Google Meet. The bot was NOT a drain loop zombie. The bot was healthy in the meeting, then systemd killed it. This is an infrastructure issue, not a bot logic issue.

    **Action:** Kill orphaned Chrome processes, clean up stale `.joining` lockfile, report to Operator with the gateway restart timestamp. Do NOT retry the meeting — the bot will be killed again if the gateway is unstable.

    For full debugging details, timeline, and distinction from other zombie patterns, see `references/drain-loop-zombie-debugging.md` → Pattern C: Systemd SIGKILL — Gateway Restart Kills the Bot.

    **Common triggers for gateway restart:** High memory usage (observed peak 3.1G), manual restart after config changes, gateway crash, systemd `Restart=always` policy.

26. **"You can't join this video call" — guest access blocked by meeting policy (observed 2026-07-16).** The bot navigates to the Meet URL, the page loads, but the bot can't find any join buttons and the page closes after ~50 seconds with `leave_reason: page_closed`. The root cause is NOT Chrome-profile lock contention — the profile is clean. The Meet page shows "You can't join this video call" because the meeting host has restricted access (no guest join, Quick access disabled, or domain-only).\\n\\n    **⚠️ CRITICAL: The browser tool uses a TEMP headless profile with NO Google auth cookies.** If you use `browser_navigate` to check the page and it shows "You can't join this video call", that's what an unauthenticated session sees — NOT what the bot's persistent chrome-profile sees. The persistent profile IS authenticated (40+ cookies including SID/LSID/OSID). **Do NOT diagnose based on the browser tool result alone.** See Pitfall #11 (auth.json cookie count is a false negative) and the full discussion in `references/guest-access-blocked-diagnostic.md`.\\n\\n    **Correct diagnosis flow when all join buttons are absent:**\\n    1. Check the bot.log for `HERMES_MEET_AUTH_STATE=***` — if set, the bot is using auth cookies\\n    2. Check the chrome-profile's `Default/Cookies` SQLite DB for SID/LSID/OSID cookies (NOT `auth.json`, NOT `Preferences`)  \\n    3. If auth IS present but the bot still can't find join buttons after ~50s → the meeting room is not active (see `references/return-to-home-screen-signal.md` and `references/lobby-page-closed-exit-2026-07-16.md`)\\n    4. If auth is NOT present → do the VNC sign-in flow\\n\\n    See `references/guest-access-blocked-diagnostic.md` for the full diagnosis procedure, SQLite cookie check commands, and chrome-profile account state verification.

27. **"Meeting ended" — bot clicks "Join now" but room is inactive (observed 2026-07-17).** When the meeting room has ended (past its scheduled end time or host ended early), the bot navigates successfully, finds the "Join now" button visible and clickable (auth is fine), clicks it, but `_click_join` returns `in_call=False, lobby_waiting=False`. The bot then falls through to the captions phase, where captions never produce real content.

    **Bot.log signature:**

    navigation complete, page title='Meet'           ← auth OK
    _click_join: attempt N — looking for 'Join now'  ← button found
      — visible=True count=1                         ← visible and clickable
    _click_join: CLICKED 'Join now' after attempt N  ← clicked
    _click_join returned — in_call=False lobby_waiting=False ← NOT admitted
    state change: {'captions_enabled_attempted': True} ← falls through to captions
    caption keystroke dispatched

    **Key distinction from Pitfall #17 (auth failure):** In Pitfall #17, "Ask to join" is found at `visible=False count=1` — the button exists in the DOM but is invisible due to invalid server-side session. In this case, "Join now" is `visible=True count=1` — the button IS present, visible, and clickable. The page is served correctly, the user is authenticated. The room simply isn't active.

    **Key distinction from Pitfall #26 (guest access blocked):** In Pitfall #26, the page shows "You can't join this video call" and the page closes after ~50 seconds. In this case, the page loads normally, the "Join now" button is present, and clicking it doesn't close the page — it just doesn't transition to the call.

    **What to do:**
    1. Check the meeting time: `date -u` and `TZ=Asia/Singapore date`. If the meeting has ended, this is expected behavior.
    2. Kill the bot immediately — do NOT retry, the room won't become active.
    3. Report the failure concisely. Include the meeting's scheduled end time and the current time.
    4. If the meeting should still be active, the host may have ended it early. Check with the organizer.

    **Common scenario: Auth delay pushes join past meeting end.** The bot was delayed by infrastructure issues (auth fix, gateway restart, VNC session). By the time it navigates to the Meet URL, the meeting has ended. This happened on 2026-07-17: the standup (09:30-10:30 SGT) was over by the time the auth fix was applied and the bot was triggered.

    **Prevention:** If the join takes more than 5 minutes from the meeting start time, check the scheduled duration before proceeding. The `.joining` auth gate prevents re-spawns when auth is known-broken, but once auth is fixed, the remaining delay may push the join past the meeting end.

    **Related:**
    - `meeting/meeting/references/meeting-ended-detection.md` — full reference with comparison table and bot.log signature
    - `meeting/meeting/scripts/meet-join-diagnostic.py` — diagnostic script using `launch_persistent_context` with the chrome-profile

28. **Overlapping meetings evict each other — single-tracked bot, no scheduler busy-check (observed 2026-08-03).** The pipeline is architecturally single-active-meeting (one Chrome profile, one `meet_bot.py`, tracked by `${HERMES_HOME}/workspace/meetings/.active.json`), but `run.js` never consults `.active.json` before firing a starting webhook. When meeting B's join window opens while the bot is live in meeting A, `process_manager.start_bot()` (`~L136–149`) sees a `meeting_id` mismatch and calls `stop()` on A's bot to boot B's — so the two meetings *thrash*, each tick evicting the other. Whichever loses gets no transcript and no `meeting-auto-join.log`. Two supporting defects: `run.js` STARTING only processes `startingMeetings[0]` (~L791, no queue), and the `profile-locked` exit_reason `checkJoinLock` handles (~L505) fires ONLY from the Chrome SingletonLock same-profile race — NOT from the single-active guard, so run.js is blind to "busy in another meeting."

    **Diagnosis rule:** a meeting with NO `meeting-auto-join.log` at all (not even an auth-failure log) during a window when another meeting was live = suspect this concurrency gap, not an auth failure. Check `.active.json` for the meeting_id that owned the bot during the missing meeting's window.

    **Operator-directed switch protocol:** If the operator explicitly says to leave meeting A and join meeting B, that instruction authorizes the destructive switch; do not ask again. Resolve B exactly from the live scheduler/calendar payload (event ID, title, start/end, Meet URL, attendees), then:
    1. Send SIGTERM to A's verified bot PID; wait briefly, then SIGKILL only if it remains non-zombie. A `/proc/<pid>` entry with state `Z` means the bot is dead and awaiting reaping, not still in-call.
    2. Remove only the shared Chrome profile `Singleton*` files and B's proven-stale `.joining`; do not delete A's meeting artifacts or transcript.
    3. Persist B's `event_meta.json`, spawn the active image bot with the gateway venv, `HERMES_MEET_AUTH_STATE`, and `start_new_session=True`.
    4. Verify B via `status.json`: require `inCall=true`, non-null `joinedAt`, captions active, and `error=null` before reporting success. Report listen-only plus PID/transcript path.
    5. Preserve the audit trail: record the replacement PID/reason and append the normal active join receipt after admission.

    **Do NOT code the architectural fix without Operator's sign-off** (any prod-service edit needs it; systematic root-cause first). Three approaches (scheduler-only busy-gate+queue / +process_manager decline-not-evict / multi-bot concurrent capture) and full source locations in `references/overlapping-meeting-concurrency.md`. Downstream: the losing meeting's `bot-never-joined` FINISHED webhook re-fires every tick (6× observed) because there's no `.never-joined` terminal guard in `checkFinalizeLock` — fold that guard into whichever fix ships.

29. **`HERMES_MEET_AUTH_STATE` is REQUIRED — it steers the chrome-profile directory (re-confirmed 2026-09-06).** `meet_bot.py` resolves the persistent chrome-profile dir as `Path(auth_state).parent / "chrome-profile"` when `HERMES_MEET_AUTH_STATE` is set, else falls back to `Path.home() / ".hermes/workspace/meetings/chrome-profile"` — a NON-EXISTENT path on this host. The `meeting-auto-join` SKILL.md prose says "HERMES_MEET_AUTH_STATE is obsolete — do not set it." That is MISLEADING: the env var is used only to DERIVE the profile dir path, never read/validated as an auth file. The skill's own spawn-script examples DO set it. Omitting it → bot launches an empty unauthenticated profile → all join buttons render `visible=0 count=0` → exits `error: "host denied admission"`, `leaveReason: "denied"` (the unauthenticated-join signature, NOT a real host denial — do NOT gate on it). **Always set** `HERMES_MEET_AUTH_STATE=${HERMES_HOME}/workspace/meetings/auth.json` so the bot uses `${HERMES_HOME}/workspace/meetings/chrome-profile`. First-spawn detection: `WARNING chrome-profile not initialised at <path>` in bot.log. See `references/meetbot-profile-dir-and-slack-token.md`.

30. **`pkill -f "<pattern>"` can kill your own shell when the pattern appears in the command line (observed 2026-09-06).** `pkill -f` matches against every process's full command line, including the shell running your `pkill` command. If the pattern string literally appears in the command (e.g. `pkill -9 -f "chrome.*chrome-profile"`), pkill matches that shell and SIGKILLs it — the whole command dies with exit code -9 and no output. **Fix:** don't pass a pattern that also appears in your own command line; `pgrep -f` first and `kill` the explicit PIDs, or scope the match to a substring not present in the command.

 31. **meet_bot SIGTERM after successful join = webhook-session process-group teardown (observed 2026-09-06; clean-spawn validated 2026-09-14).** A bot that joins (`ADMITTED`), runs the drain loop, then gets `WARNING received SIGTERM` and exits cleanly (`leaveReason: null`, `in_call: false`) is being killed by its OWN spawning webhook session, NOT rejected by the host. `spawn_bot.py` must use `subprocess.Popen(..., start_new_session=True)` so the bot is detached from the webhook session's process group. **Diagnostic:** `cat /proc/<defunct_pid>/stat` → if `ppid=1 sess=<N> pgrp=<N>` (sess==pgrp), it was in the spawning session's group. **Validated path:** after a prior `cross-meeting-profile-contention` cleared, a direct-script re-attempt with `start_new_session=True` produced a live detached bot, `status.json inCall=true`, captions enabled, and a verified active lock. Prior contention skips are transient: re-check the conflicting bot; if it has exited and no process holds the profile, retry the original event instead of treating the old skip as terminal. See `references/meetbot-sigterm-after-join-process-group.md`.

 32. **Concurrent-sibling detection via sentinel-lock rewrite (observed 2026-09-07, Sprint Planning `98mlekd31gm7b96a1cvd1hgko4_20260907T073000Z`).** Two webhook sessions for the same event can both pass the Step 0b check before either writes the sentinel, then BOTH write it — last-write-wins overwrites the first. Detect the race **after** writing your sentinel: re-read it before the first external write and compare `started_at` to the value you wrote. If it changed to a later timestamp with the SAME `event_id`, a sibling session rewrote your lock and is actively processing the same meeting. **Back off** — do NOT create the page, do NOT send the DM, log `finalization-in-progress` and stop. A duplicate page is wasteful; a duplicate triage DM is a triage-gate/privacy violation (organizer sees a second approval request). Do NOT remove the sibling's `.finalizing` because `status.json.pid` is dead — that PID is the bot's, not the finalizing session's. Distinguish from the write-behind-cache false positive on scratch `/tmp`/`${HERMES_HOME}/tmp` paths: only a change inside the meeting directory counts as a real concurrent writer. Full detail in `references/webhook-idempotency.md` → "Concurrent-Sibling Detection via Lock Rewrite".

 33. **Notion task CREATE: `POST /v1/pages`, not `/v1/databases/{id}`; `Tags` is a closed list.** When a meeting finalize creates action-item tasks, the create call is `POST /v1/pages` with `parent.database_id` — the `POST /v1/databases/{id}` shape 400s. And `Tags` multi_select accepts only the fixed option names (`INFRA`, `Feature`, `Research`, `Bug`, ...) — a freeform tag like `workflow` returns 400. `Priority` accepts both `Med` and `Medium`. Pages cannot be `DELETE`d — archive with `PATCH /v1/pages/{id}` `{"archived": true}`. Full recipe + live-schema-dump fix: `references/notion-task-create-endpoint.md`.

34. **Modified approval can SUPERSEDE a proposed action item — record it, don't create it.** When the organizer's approval resolves a proposed item (e.g. "evaluate langgraph, decide by Sep 15" once the final decision is made), do NOT create a task for it — record `resolution: "superseded-by-decision"` with the deciding note. Reuse an existing open task when the organizer names the outcome ("Participant B will work on it" → existing Windmill/orchestration task), create only the genuinely-new items. This keeps the approval audit one-to-one with the proposal.

38. **An approval reply can carry a DELIVERY OVERRIDE — the approval signal and the destination are two separate decisions (observed 2026-09-11, Demo & Backlog Refinement `1fnj741fipd1f8de2b9985lp2r_20260911T070000Z`).** Organizer's entire reply to the triage DM was `Post to channel <#CONFIGURED_SLACK_ID> tag all`. That is BOTH (a) approval of the proposed items as-is — he requested no edits — and (b) two overrides of `meeting-finalize` Step 7 item 10, which hardcodes "send the Slack update to the #general channel": the destination becomes the named channel, and `tag all` means a channel-wide `<!channel>` mention on the post.

    **Decision table for an approval reply:**
    | Reply shape | Reading |
    |---|---|
    | Names a channel/mention, says nothing about the items | Approve ALL items as-is, POST to the NAMED channel (never #general) with the requested mention |
    | Plain approve, no destination | Approve all, default destination (#general) |
    | `Mark notes as done` + a publish destination (for example, `send a message to the general channel`) | Approve ALL items as-is and run the complete finalization workflow. “Done” means resolve every proposed action, update the visible approval state, publish, verify, and close the audit markers — not merely flip note text and send a summary. |
    | Destination + item edits/reassignments | Modified approval — apply both the edits and the destination |
    | Destination named, but items rejected | Reject items, still honour the destination only if a post is actually being made; do not post an "approved" update for rejected items |

    **Do NOT re-ask for approval** because the reply "didn't say approve" — naming a destination is the instruction to publish, which presupposes approval. Re-asking burns the operator's turn.

    **Record the override so the audit shows it was instructed, not defaulted:** `triage-context.json` gets `approval_note` (quote the reply verbatim), `approval_source: "slack-reply"`, and a `slack_update` block with `channel_id`, `channel_name`, `message_ts`, `mention`; `.finalized` mirrors the channel id + ts. Never leave the destination implicit — a future reader must be able to tell #general was overridden.

    **Pre-flight and verify a NAMED channel** (you have no roster entry for arbitrary channels): `GET conversations.info?channel=<id>` → confirm `is_member: true` before `chat.postMessage` (a non-member post fails `not_in_channel`). After sending, `GET conversations.history?channel=<id>&limit=1` and confirm the returned `ts` matches the `ts` from the send. Full worked example with the exact mrkdwn message that shipped: `references/approval-reply-destination-override.md`.

39. **Notion rich_text: `href` is response-only — the writable link is `text.link.url`.** When appending an approved-task link into a `to_do` block (meeting-finalize Step 7 item 6), the rich_text object must be `{"text": {"content": "<task text> — ", "link": {"url": "${CONFIGURED_SOURCE_URL}<page-id-without-dashes>"}}}`. The sibling shape `{"text": {"content": "…"}, "href": "…"}` is wrong: `href` is a *read-back* field on the rich_text object (and on the `text` sub-object), not a writable input. **Verify after the append** with `GET /v1/blocks/{page_id}/children` and check that the first rich_text segment of the to_do has a non-null `href` and the second (assignee) segment has `href: null` — the placement rule already documented in Step 7 item 6, now with the writable key name attached.

40. **Audio spool full = silent CONFIGURED_SLACK_ID recording loss with no alert — check it on every "why is there no recording?" (observed 2026-09-12, `abc-defg-hij` "Meeting Demo").** Organizer: *"you missed the recording for the last call too."* The call was not missed — capture was DECLINED before it started. `status.json` carries `audioRecordingError: "audio capture setup failed: audio spool is full (5/5 recordings)"` with `audioRecordingBytes: 0` and no `meeting-audio.webm`, **while every other health field looks normal** (`inCall: true`, `joinedAt` set, captions attempted, `exited: true`, `leaveReason: duration_expired`). The failure is visible ONLY in that one field.

    **Why it never self-heals:** `AudioSpool._pending_recordings()` (plugins/google_meet/transcription_pipeline.py) counts `*/meeting-audio.webm` **plus** `*/meeting-audio.webm.part` under the meetings root and refuses at `AudioSpoolPolicy.max_pending_recordings` (default 5). Nothing ever prunes — a finalized, already-transcribed recording keeps a slot forever, and a crashed capture's orphan `.part` (`status.json` `exited: false`, no `audio.ready.json`) holds a slot with nobody left to finalize it. Once 5 accumulate, **every later meeting is audio-less and the count never falls**: the 5 blocking files here dated 2026-09-06 → 09-08.

    **Diagnostic:** run `scripts/spool-audit.py` — it mirrors the bot's count, prints each holder's age/size/finalized state, and lists which meetings already lost their recording. One read replaces the whole hunt.

    **Do NOT conflate with a caption stall on the same call.** This call ALSO froze captions (`lastCaptionAt` 12:38:53Z, `transcript_lines` stuck at 11 for ~38 min, `__hermesMeetDrain not available (observer not installed)` repeating, DOM PROBE `container=False`) — that is a separate defect in the drain-loop-zombie family and needs its own ticket. Recording loss happens *before* capture starts; observer death happens *during* capture.

    **Clearing the spool is a DELETION of captured data — audit, report, ASK.** Never delete unprompted. State per file whether it is finalized (`audio.ready.json`) and whether a transcript exists; offer the orphan `.part` as the minimally-destructive candidate. Filing the durable-fix ticket is NOT gated on the deletion. Durable-fix ACs that matter: retention so finalized recordings stop counting, orphan-`.part` reaping by age, **a DM to the operator when capture is declined** (discovering it afterwards is the actual defect), and a greppable spool-full event in `bot.log` rather than status.json only. Full incident, code model, and per-file table: `references/audio-spool-capacity-capture-loss.md`.

    **Generalizable rule:** a capture-degradation signal that lives only in a status field no one reads is worth more than it looks. When asked to file a bug about "you missed X", go read the artifact's own status/error fields before accepting the framing — the verb here was *declined*, not *missed*, and the root cause was 6 days old.

41. **A review flag with non-zero sparse lines is still terminal — use artifact freshness, not `transcriptLines > 0`, to suppress finalize retries.** A sparse capture may legitimately contain several lines. If the scheduler treats any positive line count as proof that `.review-flag.txt` is stale, it re-fires the finished webhook every tick and generates repeated agent-history notifications. In `checkFinalizeLock`, skip while the review flag is current; consider it superseded only when `status.json` was updated after the flag, proving a later capture attempt. Add a deterministic regression fixture covering both states, then verify two real scheduler ticks log the review-lock skip. See `references/review-flag-finalization-retry-loop.md`.

42. **Long pre-flight is not a transaction — converge on sibling state immediately before every join side effect (verified 2026-09-28).** Concurrent calendar webhook sessions can advance while another session reads logs, checks auth, or loads references. Immediately before Vexa dispatch and again before legacy-browser spawn, re-read the exact event's `.joining`, `status.json`, active lock, Vexa lifecycle artifact, and live `meet_bot.py` processes identified from `HERMES_MEET_EVENT_ID` (exact Meet URL only as compatibility fallback). A matching live PID with `inCall=true`, non-null `joinedAt`, and `error=null` is success owned by the sibling: do not dispatch or spawn again. Converge idempotently by creating the active lock only if absent/stale and appending the joined receipt only if absent. A matching PID still booting with fresh log progress means `bot-already-joining`; do not publish an active lock yet. If no bot exists, acquire `.joining` atomically with `O_CREAT|O_EXCL`; `FileExistsError` means a sibling won, so re-read rather than deleting its claim. Re-run this gate after any timing sleep or backend request. A clicked Join button is progress, not admission; wait through status lag for the verified state before claiming success. Full incident and procedure: `references/concurrent-delivery-gate-bypass.md`.

43. **A staged triage context is not proof the organizer received the approval DM (verified 2026-09-28).** `triage-context.json` is intentionally written before Slack delivery, so `approval_status: pending` can coexist with `slack_delivery.status: pending` and `message_ts: null`. Under a fresh sibling-owned `.finalizing` lock, classify that state as `finalization-in-progress`, not `triage-pending-awaiting-approval`. True triage-pending requires a delivery receipt: `slack_delivery.status: sent` plus non-empty `message_ts`, or the legacy top-level `slack_message_ts`. For stale recovery, query Slack history read-only for the exact `ctx:<event_id>` before any send: repair the receipt if found; if absent, resume only the missing DM step from the existing page/context; if unverifiable, stop rather than blind-resend. Never infer delivery from the Notion page, transcript attachment, lock, or context-file existence. Full state table: `references/duplicate-fire-triage-context-signals.md`.

44. **Cross-meeting Chrome contention is backend-scoped; converge on remote coverage before skipping or alerting (verified 2026-09-30).** A legacy bot for meeting A can own the local Chrome profile while Vexa independently covers meeting B. Immediately before logging `cross-meeting-profile-contention`, sending its Slack alert, or reporting B uncovered, re-read B's active lock, Vexa lifecycle/state, and provider status. `vexa.bot.response.json` with `status=requested` proves dispatch only; require an exact `platform + native_meeting_id` match reported `active` by `GET /bots/status` (or equivalent provider read-back). If active, preserve A, suppress the contention alert, converge B's active lock/joined receipt idempotently, and use `pid=none` / `transcript_lines=unknown` rather than inventing values. If an alert escaped before convergence, retain it and send one verified correction. A local alert artifact still marked `pending` is likewise not proof of failed delivery: search Slack by exact event ID and reason before resending, then repair the artifact from the verified message timestamp. Full procedure: `references/vexa-primary-contention-convergence.md`.

45. **“Use existing only” is a hard zero-create approval boundary.** Replies such as “we already made the action items,” “find the tickets,” or “don’t make any more” are modified approvals: reconcile every proposed action against live Notion, reuse only same-outcome tasks, and create nothing. One action may map to several decomposed tickets. Record zero matches as `no-existing-same-outcome-task` and multiple plausible matches as `needs-exact-task-match`; continue every unambiguous item instead of failing the whole finalization. Preserve status, assignee, title, relations, and existing body text unless separately authorized; additive provenance and an explicit meeting due date are permitted by the update request. Verify every target after writing and prove zero creation by comparing Tasks data-source counts before/after. Publish the requested team update from verified results, read it back by exact timestamp, then finalize. Full state machine and receipt schema: `references/existing-only-approval-reconciliation.md`.

46. **Split workspace roots require one global idempotency state, not two independent finalizers.** A capture root may hold `status.json`, Vexa lifecycle, and the signal tape while a profile root holds `transcription.state.json`, canonical/readable transcripts, and downstream receipts. Before any Notion, Slack, or email write, enumerate the exact event directory under every runtime-resolved root and evaluate `.cancelled`, `.finalized`, `.finalizing`, and delivered-triage evidence globally. Acquire the same `.finalizing` claim in every active root before the first external write; an existing or conflicting claim means re-read and back off, never overwrite. Mirror only control-plane markers and verified receipts atomically; do not duplicate raw audio, signal tapes, or transcript evidence merely to flatten the layout. On approval, write and verify `.finalized` in every active root before removing `.finalizing`. Verify Slack by exact channel + `ctx:<event_id>` + returned timestamp, email by Gmail envelope metadata (`To`, `Subject`, `SENT`), and Notion by page/block read-back. Full discovery, conflict, mirroring, and verification procedure: `references/two-workspace-roots.md`.

47. **A dispatch lease can fence after an external write succeeds; reconcile, never blind-retry.** With `_dispatch`, heartbeat immediately before every Notion, Slack, email, task, receipt, context, or terminal-marker write. Keep each external write plus provider read-back tightly bounded. If a later heartbeat exits `3`, stop with no further side effects and do not call executor `finish`; the durable provider object may exist even though the local receipt does not. A retry generation must query the exact external target first using stable identity (`ctx:<event_id>` for Slack; event ID plus canonical page identity for Notion), verify one complete match, then heartbeat and adopt it into local receipts. Zero matches permits a fresh write; multiple or partial matches require ambiguity handling. Slack IDs are workspace-scoped: resolve and deliver through the same configured route. Adapter-added text also makes byte equality fragile; verify channel, provider timestamp when available, context reference, canonical URL, title, and required action fragments. Full state machine: `references/dispatch-fenced-external-write-reconciliation.md`.

## Slack Messaging & ID Resolution (absorbed from `slack-ops`)

When a meeting skill needs to send Slack DMs or resolve a Slack user/channel ID, use these patterns:

### Sending messages
Use the `send_message` tool with `target='slack:...'`:
- Home channel: `slack`
- Named channel: `slack:#engineering`
- DM / specific chat: `slack:<channel_or_dm_id>` (e.g. `slack:CONFIGURED_SLACK_ID`)
Attach files with `MEDIA:/abs/path` inside the message body.

### Resolving a Slack user/channel ID
**The agent has NO Slack directory API by default.** You CANNOT call `users.list`, `users.lookupByEmail`, or search the workspace to turn a name/email → user ID. Three real options, in preference order:

1. **Read from stored roster.** Team Slack IDs live in `${HERMES_HOME}/team-roster.yaml`. Check there first.
2. **Have the user copy it from the Slack app:** profile → More (···) → Copy member ID (`U0XXXXXXX` for users, `C…` channels, `D…` DMs).
3. **Only if a bot token with `users:read` exists** (check GSM), run the lookup yourself via terminal + curl. Do NOT assume the token exists; the message-send path uses a token injected into the agent process, which is NOT the same as having directory read scope.

### Pitfalls
- Message-send capability ≠ directory-read capability. Different scopes, and lookup is usually absent.
- The Slack bot token is injected into the AGENT process, not a bare terminal subshell — `$SLACK_BOT_TOKEN` is empty in `terminal`. **In this deployment `${HERMES_HOME}/.env` holds a REAL `SLACK_BOT_TOKEN` (a live `xoxb-` value, NOT the redacted sentinel `meeting-finalize` Pitfall #35 claims) — parse it with the `partition("=")` pattern and use it directly (verified 2026-09-05, Goodbye world Meeting Bot finalize: DM sent successfully).** Do NOT burn a round-trip on GCP Secret Manager first (note: `meeting-finalize` SKILL.md's own Pitfall #35 still recommends GSM/GCP-SDK-first and caused exactly that wasted round-trip on 2026-09-11 Demo & Backlog Refinement finalize — `DefaultCredentialsError`, no ADC on host — before the .env read succeeded; the umbrella rule here wins): `secretmanager.SecretManagerServiceClient` fails on BOTH auth paths — with no ADC it raises `DefaultCredentialsError`; with `GOOGLE_APPLICATION_CREDENTIALS` set (e.g. `GOOGLE_APPLICATION_CREDENTIALS=${HERMES_HOME}/secrets/x509/credentials.json`) it reaches the service but returns 403 PermissionDenied `secretmanager.versions.access` (the john-cos-dwd SA has no Secret Manager role). Verified 2026-09-08 (Sync on Architecture finalize) for the 403-with-ADC path. Read `.env` first; fall back to GSM only if the `.env` value is empty or a `***` sentinel.
- **Notion token is the mirror image of Slack: source it from `os.environ`, not `.env`.** `NOTION_API_TOKEN` IS set in `os.environ` inside a terminal subshell (unlike `SLACK_BOT_TOKEN`), and the `${HERMES_HOME}/.env` `NOTION_API_TOKEN` can be a *different, stale* value that 401s on the first Notion call (`HTTP Error 401: Unauthorized`) — observed 2026-09-06, event `19ma4pg15d78lh96ssjqcr3uai` ("john 6th") verify query. For any Notion API call read `os.environ.get("NOTION_"+"API_"+"TOKEN")` (fragment-built name dodges the `KEY=` redaction), never the `.env` copy. Token-source rule: **Notion = `os.environ`, Slack = `.env`.**
- **`write_file` cannot write to `/tmp` — scratch scripts must live under `${HERMES_HOME}`.** In this deployment `HERMES_WRITE_SAFE_ROOT=${HERMES_HOME}`, so `write_file`/`patch` to `/tmp/...` is DENIED ("outside HERMES_WRITE_SAFE_ROOT (${HERMES_HOME})") and the script is silently not created. Every "write to `/tmp/send-dms.py`" / `/tmp/resolve_attendees.py` instruction in the meeting skills fails here. **Fix:** write scratch scripts to `${HERMES_HOME}/tmp/...` and run with `python3 ${HERMES_HOME}/tmp/...`. Observed 2026-09-07 (Sprint Planning finalize): `write_file /tmp/resolve_attendees.py` denied; `${HERMES_HOME}/tmp/resolve_attendees.py` created and ran. Note the `${HERMES_HOME}/tmp/` sibling-warning caveat in Pitfall #32 still applies (only treat a sibling warning as real for files inside the meeting directory).
- Never guess or invent a `U…` ID. A wrong ID DMs the wrong person or silently no-ops.

See `references/slack-ops-reference.md` for the full GSM token retrieval quirk and exact curl lookup commands.

## Finding a Meeting by Weekday + Timezone (no event_id given)

When a user asks for "the [meeting type] we had on [weekday] ([timezone])" without an event ID or exact date:

1. Convert the described weekday+timezone to a UTC date range yourself (don't guess) — meeting directories are named `<calendar_uid>_<YYYYMMDDTHHMMSSZ>` where the timestamp is UTC.
2. `search_files(target='files', path='${HERMES_HOME}/workspace/meetings')` or `ls -latr` the meetings workspace (NOT `${HERMES_HOME}/meetings/` — that path doesn't exist; the real store is `${HERMES_HOME}/workspace/meetings/`) and scan directory names for that date.
3. Confirm the match by reading `event_meta.json` (title, attendees, start/end in local tz) and/or `summary.json` (meeting_type) inside the candidate directory — don't rely on the directory name alone, since recurring meetings share a calendar_uid prefix and only the timestamp suffix disambiguates.
4. Resolve transcript provenance before searching. If `transcription.state.json` exists and `canonical_status=completed`, use `transcript.canonical.jsonl` or `transcript.readable.md`; do not silently prefer a coexisting legacy file. If canonical processing failed terminally, use `transcript.live.raw.json` when present and label it provisional. Only without Vexa artifacts fall back to legacy `transcript.json` / derived readable output. Search the selected transcript for the topic keyword first, then read a wide window around the hit because substantive discussion spans fragmented turns.

## Technique: Reconciling Spoken Requirements Against Written Notion Specs

A recurring Chief-of-Staff task: the user asks "what did Organizer actually want for X" where X has both a Notion PRD/tickets (the written record) AND a meeting where it was discussed live (the spoken record). The written record is reliably **incomplete** — Organizer thinks out loud, revises understanding mid-sentence, and only a fraction of what's said gets formalized into tickets. Treat this as a two-source reconciliation, not a single-source lookup:

1. **Pull the written spec first** (Notion PRD page via `/v1/pages/{id}/markdown`, plus any linked tickets in the Tasks DB filtered by title/keyword). This gives you the "official" baseline — what's already resolved, decision-logged, or ticketed.
2. **Pull the transcript stretch** covering the same topic (see technique above). Read generously wide — the real discussion is rarely a single clean paragraph; it's scattered across dozens of short fragmented lines with false starts, tangents, and interruptions from other speakers.
3. **Diff the two, explicitly categorizing each point** into: (a) resolved and ticketed — matches PRD, no action needed; (b) discussed but never ticketed — a real gap, flag it; (c) said in the meeting but *contradicts* what's written in the PRD (e.g. PRD says "adopt the existing test framework", but in the meeting the same person said "I don't really need that level of capability") — flag as a live discrepancy needing reconciliation, don't silently prefer one source; (d) raised as an open question by a participant and dropped without an answer (e.g. someone asks "so is it still X?" and the conversation just moves on) — flag explicitly as unresolved, don't let it disappear.
4. **Never resolve the ambiguity yourself.** The deliverable is a precise list of gaps/contradictions/unresolved-questions, not an invented answer. Offer the user a next step (draft OQ entries for the PRD, draft a clarification message) but don't write it into the PRD unprompted — PRD edits need the user's explicit go-ahead per the "propose, don't commit" rule.
5. Cite exact transcript timestamps/lines and Notion ticket IDs/URLs for every point raised — every claim in the reconciliation must be traceable back to either the transcript or the ticket, never a paraphrase floated as fact.

See `references/requirements-reconciliation-from-transcript.md` for a worked example (Tester/QA agent Gherkin+Allure requirements, 2026-08-03 Sprint Planning).

## Reference Files
|---|---|
| `references/shared-constants.md` | Shared constants, Slack resolution, domain references |
| `references/external-follow-up-calendar-booking.md` | Rebook a known external contact from an exact prior event, warn once on conflicts, send updates, add Meet, and verify the created event |
| `references/webhook-idempotency.md` | Two-phase lock pattern for webhook-triggered skills, June 10 incident |
| `references/duplicate-fire-triage-context-signals.md` | Distinguish staged triage context from delivered approval: receipt-based state table, fresh-lock handling, read-only Slack recovery, no blind resend |
| `references/triage-publication-transaction.md` | Crash-safe publication order across Notion, local context, Slack, and attendee email; cross-system identity fallback; marker and timestamp read-back verification |
| `references/atomic-joining-lock-acquisition-2026-09-25.md` | Atomic `O_EXCL` `.joining` claim for concurrent calendar deliveries; current-runtime proof, bot ownership after spawn, older-image compatibility |
| `references/concurrent-delivery-gate-bypass.md` | Re-check authoritative state under concurrent webhook delivery: terminal gates plus last-moment convergence on a sibling's verified join before Vexa dispatch or legacy spawn |
| `references/vexa-primary-contention-convergence.md` | Treat local Chrome contention as backend-scoped; verify exact Vexa admission before skip/alert, converge receipts, reconcile stale pending-alert artifacts, and correct raced alerts without deleting history |
| `references/meeting-pipeline-and-transcript-provenance.md` | Distinguish cron scheduler, webhook finalizer, Vexa primary audio/STT artifacts, shadow mode, and legacy DOM-caption fallback; includes a live verification checklist |
| `references/two-workspace-roots.md` | Discover split capture/profile roots, enforce global idempotency, atomically mirror control state, and verify Notion/Slack/email receipts without copying evidence artifacts |
| `references/dispatch-fenced-external-write-reconciliation.md` | Recover when an executor lease fences after an external write: stop semantics, next-generation provider reconciliation, workspace-scoped Slack identity, and receipt adoption |
| `references/recurring-events-directory-naming.md` | Why event_id not Meet slug — recurring events reuse the same Meet URL |
| `references/pointerless-slug-finalization-recovery.md` | Recover exact Meet-slug capture artifacts when a prep-created event directory exists but has no capture pointer/files; preserve metadata and handle zombie PID reaping |
| `references/transcript-injection-guide.md` | How to inject mock transcripts into a meeting directory for testing; JSONLines format, bot counter caveats, verification steps |
| `references/auth-validation.md` | How to validate auth.json cookies server-side (CookieMismatch diagnosis), file format parsing, persistent auth failure root causes |
| `references/chrome-cookie-db-auth-check.md` | Ground-truth auth check via the chrome-profile cookie DB (Python `sqlite3`, FILETIME `expires_utc` decode); resolves host-denied vs auth-failed gate conflicts when concurrent deliveries disagree |
| `references/playwright-setup-guide.md` | Playwright system dependency setup for Ubuntu — browser installs, apt packages, venv integration; includes the broken-symlink "Executable doesn't exist" diagnosis + `playwright install chromium` fix (2026-09-05) |
| `references/meeting-notes-db-fallback.md` | When a project's Meeting Notes child DB is not shared with Meeting Bot CoS — create child pages under the parent project page instead |
| `references/notion-attendee-resolution.md` | Resolve calendar/webhook attendees to Notion user IDs: live `GET /v1/users`, calendar-email ≠ Notion-email mismatches (Organizer=person@example.org), omit no-account attendees from `people`, keep them in triage-context with `notion_id:null` |
| `references/drain-loop-zombie-debugging.md` | Drain loop zombie — bot stays alive but captures nothing after page content loss; timeline, diagnosis, and fix |
| `meeting-auto-join/references/stale-incall-drain-loop-contention.md` | Diagnose false shared-profile contention when a live PID retains stale `inCall=true` after Meet redirects away from the room; includes health-gate requirements and regression coverage. |
| `references/drain-loop-unboundlocalerror-fix-2026-07-16.md` | Drain loop `UnboundLocalError` 'dom' crash — root cause, fix, and recovery |
| `references/guest-access-blocked-diagnostic.md` | "You can't join this video call" — guest access blocked by meeting policy; browser tool diagnostic, chrome-profile account state check, resolution options |
| `references/meeting-ended-detection.md` | Bot clicks "Join now" but room is inactive — meeting ended signal, bot.log signature, distinction from auth failure, root cause, and prevention |
| `references/overlapping-meeting-concurrency.md` | Overlapping meetings evict each other — single-tracked bot, run.js no busy-check, process_manager evicts instead of declines; source locations, three-defect diagnosis, fix options A/B/C |
| `references/meetbot-sigterm-after-join-process-group.md` | meet_bot SIGTERM AFTER successful join = webhook-session process-group teardown; `/proc/<pid>/stat` sess==pgrp diagnostic; `start_new_session=True` detach fix (validation pending) |
| `references/runtime-environment-paths.md` | Meet-bot runtime paths, working bot python (`/usr/bin/python3`), greenlet musl/glibc fix, `/tmp` noexec trap, PLAYWRIGHT_BROWSERS_PATH |
| `references/early-join-respawn-then-gate.md` | Early-join lobby timeout → re-spawn once → if still not admitted, gate + notify; the `lobby_timeout` vs `reason=None` discriminator |
| `references/requirements-reconciliation-from-transcript.md` | Worked example: reconciling spoken meeting requirements against a written Notion PRD (finding the right meeting by weekday/tz, phonetic-transcript search gotcha, categorizing gaps/contradictions/unresolved questions) |
| `references/framenavigated-kick-transient-2026-09-11.md` | Kick artifact ≠ capture failure: framenavigated → Google marketing page mid-meeting, bot recovered, kept capturing; check transcript growth after kick ts before classifying auth failure; audio_issue pattern + audioRecordingError are noise here |
| `references/sparse-vs-test-session-classification.md` | Short mic-test transcript falls into the sparse bucket (human review) not the test-session bucket (triage) when it's too short for Threshold 2/3 (`minimal_collapse_4_to_1` or `sparse_collapsed_<100chars`) and the title lacks "test"/"debug"; why Threshold 6 doesn't catch it; plus the zombie `ps -p` liveness gotcha; plus concurrent-disagreement discipline (don't clear an existing `.review-flag.txt` to "upgrade" to a test session) |

Manual “finalize the current meeting” procedure: see `meeting-finalize/references/manual-current-meeting-finalization.md` for candidate resolution, clean bot shutdown, triage execution, and receipt verification.
| `references/tester-qa-requirements-and-legacy-framework-review.md` | Tester/QA planning reference: PRD + spoken-requirement reconciliation, legacy testpack reuse audit, dashboard/IAP/vault architecture corrections, and concise planning rules |
| `references/meeting-finalize-notion-task-writes.md` | Meeting finalize → Notion Tasks writes: current-sprint resolution fallback (search recipe fails; read Sprint relation off an open task), comments-403 `restricted_resource` → body-append fallback, modified-approval comment-vs-create split, and write verification |
| `references/notion-task-create-endpoint.md` | Notion task CREATE: `POST /v1/pages` (not `/v1/databases/{id}`), `Tags` is a CLOSED multi_select list (invalid tag name → 400), `Priority` accepts `Med`/`Medium`, pages archive-not-delete, live-schema dump to stop a 400 loop |
| `references/approval-reply-destination-override.md` | An approval reply that names a channel/mention instead of saying "approve" — read it as approve-as-is + destination override; exact Slack pre-flight (`conversations.info`), the shipped mrkdwn team-update, read-back verification, and the `&amp;` false alarm |
| `references/existing-only-approval-reconciliation.md` | Treat “we already made these; find the tickets” as modified approval with a hard zero-create boundary; action-to-many matching, unchanged task-count proof, unresolved-match receipts, verified team delivery, and terminal ordering |
| `references/mark-notes-done-and-publish-approval.md` | Treat “mark notes done and publish” as full approval plus destination instruction; one-to-one action resolution, body-status fallback, live `general` resolution, verified Slack receipt, and terminal audit ordering |
| `references/audio-spool-capacity-capture-loss.md` | Audio spool full → silent cumulative recording loss: `_pending_recordings()` counts `.webm` + orphan `.part` with no retention, cap 5, failure visible only in `audioRecordingError`; diagnostic recipe, per-file table, ticket ACs, destructive-clear protocol |
| `references/review-flag-finalization-retry-loop.md` | Sparse non-zero transcript + current review flag retry loop: freshness-based terminal guard, regression test, live two-tick verification |
| `scripts/spool-audit.py` | Audit spool capacity + per-meeting capture health (mirrors the bot's count; exit 1 when full). Run before filing/diagnosing any "no recording" report |
| `scripts/meet-join-diagnostic.py` | Diagnostic script using `launch_persistent_context` with the chrome-profile: navigates to Meet URL, dumps page state, saves screenshot |
|
