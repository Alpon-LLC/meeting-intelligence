---
name: meeting-finalize
description: "Webhook-driven skill that finalizes completed meetings. Receives the meeting ID from the webhook payload, reads the transcript from the google_meet plugin artifacts, and pushes it through the downstream skill chain (meeting-transcript-to-notion, decision-log-extraction, contact-sync, post-meeting-approval)."
version: 2.28.0
metadata:
  hermes:
    tags: [meeting, transcript, google-meet, notion, slack, finalize]
    related_skills: [meeting-auto-join, meeting-transcript-to-notion, save-contact-to-notion, post-meeting-approval, retrieve-meeting-flow-context]
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Meeting Finalize

## When to use

- **Webhook-triggered** — fires when a meeting-end webhook POST arrives at `/webhooks/meeting-finished`. NOT cron-driven.
- Picks up where `meeting-auto-join` + `google_meet` plugin leave off: takes the completed transcript and pushes it through the downstream skill chain (`meeting-transcript-to-notion`, `decision-log-extraction`, `contact-sync`, `post-meeting-approval`).
- Not normally invoked manually. To finalize a specific meeting manually, ask Meeting Bot: `"Finalize the meeting at ~/.hermes/workspace/meetings/<meeting_id>/"`.

## Trigger

The webhook subscription `meeting-finished` receives meeting-end POSTs. On each event:
1. The webhook adapter validates the HMAC signature
2. The agent prompt instructs the meeting-finalize skill workflow
3. The skill reads the meeting's `status.json` and `transcript.json`, normalizes the transcript, and invokes the downstream skill chain

Webhook subscription (managed via `hermes webhook subscribe`):
```
URL:    /webhooks/meeting-finished
Secret: (configured at registration time)
```

## Prerequisites

- `google_meet` plugin enabled and writing artifacts to `~/.hermes/workspace/meetings/<meeting_id>/`.
- Downstream skills available: `meeting-transcript-to-notion`, `decision-log-extraction`, `contact-sync`, `post-meeting-approval`.
- Notion and Slack delivery configured (same prerequisites as `meeting-transcript-to-notion`).
- **Sparse transcript detection**: See `references/sparse-transcript-detection.md` for thresholds and flagging logic.

## Environment

- `MEETING_FINALIZE_DRY_RUN` — when `1`, lists meetings that would be finalized and the transcript that would be passed downstream, but does not invoke `meeting-transcript-to-notion` or any other writer.
- `TRANSCRIPT_EDITOR_API_URL` — OpenAI-compatible `/v1/chat/completions` endpoint for the
  evidence-preserving readability pass after canonical Vexa transcription.
- `TRANSCRIPT_EDITOR_API_KEY_FILE` — mode-0600 file containing the editor endpoint key.
- `TRANSCRIPT_EDITOR_MODEL` — defaults to `gemini/gemini-3.5-flash-lite` for fast batched cleanup.
- `TRANSCRIPT_VERIFIER_MODEL` — defaults to a second `gemini/gemini-3.5-flash-lite` pass. Semantic
  verification is combined with deterministic number, URL/email, negation, glossary, token-order,
  and contraction guards.
- `VEXA_CAPTURE_ROOT` — read-only mounted Vexa signal directory; production shadow default is
  `${HERMES_HOME}/vexa-captured-signal`.
- `NINE_ROUTER_URL` — private context-aware router origin, without `/v1/audio/transcriptions`.
- `NINE_ROUTER_API_KEY_FILE` — mode-0600 file used by batch processing when the token is not in the
  process environment.

## Missing Template Files
This skill relies on specific template files located in the `templates/` and `scripts/` subdirectories for certain fallback and testing scenarios. If these files are missing, some functionalities, particularly those related to transcript injection or default page creation for test sessions, may not operate correctly.

The following files are expected:
- `templates/daily-standup-transcript.json`
- `templates/caption-test-transcript.json`
- `templates/test-session-notion-page.json`
- `scripts/inject-transcript.py`

If any of these are absent, it could lead to a `bot-never-joined` skip-reason during processing, as observed in scenarios where transcript recovery or mock injection is needed. Ensure these files are present and accessible within the skill's directory structure.

A dedicated reference file, `references/missing-template-files-guide.md`, has been created to detail the purpose and expected location of these critical files.


## Workflow

### High-fidelity Vexa transcription gate

When `transcription.state.json` exists in the meeting workspace, it is authoritative for transcript
readiness. If batch/canonical status is pending at meeting end, run
`scripts/finalize_vexa_transcription.py` first. Then, before publishing or invoking downstream
skills, run:

```bash
python3 scripts/update_transcription_state.py \
  --state <meeting-dir>/transcription.state.json \
  --check-ready
```

- Exit `0`: `live_status`, `batch_status`, and `canonical_status` are all terminal; use
  `transcript.canonical.jsonl` only when `canonical_status=completed`.
- Exit `3`: batch/canonical processing is still pending or running. Stop and retry finalization later.
- If batch is `failed_nonretryable` or `retry_exhausted`, preserve the artifacts and route the live
  transcript through the existing failure/approval path. Never label it canonical.
- Never overwrite `transcript.batch.normalized.jsonl`; canonical corrections belong only in
  `transcript.canonical.jsonl` and every change must appear in `transcript.corrections.json`.
- When canonical completed, set the downstream transcript source to `transcript.canonical.jsonl`
  and attach `transcript-readable.md`. Do not continue using legacy `transcript.json` merely because
  it also exists.
- Exception: if legacy `transcript.json` begins before the first canonical Vexa timestamp, preserve
  only that non-overlapping opening with `scripts/merge_transcript_fallback.py`. Publish the merged
  JSONL/Markdown with explicit source provenance; never retain overlapping legacy rows after Vexa's
  canonical cutover.
- When batch/canonical failed terminally, use `transcript.live.raw.json` (or legacy
  `transcript.json` only if no Vexa live artifact exists) through the existing failure/approval path,
  and label the source provisional rather than canonical.

At meeting end, invoke `scripts/finalize_vexa_transcription.py` once with the signal tape, context,
workspace state and output directory. It runs the idempotent batch and canonical stages, records all
artifact hashes, and always moves failed stages to terminal fallback states without logging provider
stderr or transcript text. A repeated successful invocation returns `already_completed` after hash
verification and performs no provider calls.

For authenticated primary mode, the signed completion bridge materializes the tape and sidecars
directly in the matching calendar event directory; use that directory as the capture root. For the
shadow profile, locate the tape under `${VEXA_CAPTURE_ROOT}`. In both cases select by native Meet
code plus event start/end window, then run the finalizer with the meeting directory itself as
`--output-dir`, its `transcription.context.json`, `transcription.state.json`, optional
`vexa.bot.response.json`, and the located live transcript. The configured `NINE_ROUTER_URL` and
`NINE_ROUTER_API_KEY_FILE` are authoritative; never put either key on a command line. This writes
`transcript.canonical.jsonl` and `transcript-readable.md` exactly where the downstream Notion flow
already expects them.

When the capture filename is not yet known, run `scripts/locate_vexa_signal_tape.py` against the
scoped capture root with the native Meet code and event time window. It reads only tape headers,
selects the newest exact match, and returns sibling live/STT/log paths without modifying any source.
Always provide the event window for recurring Meet URLs so a later occurrence cannot be selected.

Raw signal and STT tape retention is enforced by `scripts/prune-vexa-transcription-artifacts.py`.
It defaults to dry-run, rejects every `replay-baselines` path, and requires a matching canonical
hash plus `backup_verified_at` and `backup_manifest_sha256` in state before `--apply` can remove an
artifact older than 14 days.

Set those backup fields only through `scripts/verify_transcription_backup.py`. The verifier requires
the external `transcription-backup-manifest.v1` to contain every canonical, batch-manifest, raw-signal
and STT-tape hash recorded in state; incomplete manifests leave state byte-identical.

For controlled and representative meetings, run `scripts/score_transcription_quality.py` against a
human-timed JSONL reference. It reports WER, substitutions/omissions/insertions, keyterm and
participant-name recall, number/date accuracy, non-overlap speaker attribution, and optional
meeting-end-to-canonical latency.

Create the reviewer worksheet with
`scripts/prepare_transcription_review.py export --transcript <canonical.jsonl> --audio-origin <signal-origin-seconds> --output <human-review.tsv>`.
After the reviewer edits `human_text`, `human_speaker`, `overlap` and optional `reviewer_notes`, import
it with `scripts/prepare_transcription_review.py import --review <human-review.tsv> --output <human-reference.jsonl>`
before running the scorer.

When no ordinary meeting recording exists, create a private synchronized review track from the
read-only signal tape with `scripts/render_signal_tape_review_audio.py --signal-tape <signal.jsonl> --output <review-audio.opus>`.
The renderer writes a SHA-256 sidecar and mixes participant channels without changing the tape.

### Step 0a: Directory existence gate

**Before Step 0's idempotency checks**, verify the meeting directory exists at `~/.hermes/workspace/meetings/<event_id>/`. If it does not, the idempotency checks in Step 0 cannot run (there are no markers to check). Follow this decision tree:

1. Check `~/.hermes/workspace/meetings/active/<event_id>.lock` — if it exists, the bot may still be joining (lobby, auth delay). Check the lock's age and the bot's status. If `exited: false` and the meeting is still within its scheduled window, log `skip-reason: join-in-progress event_id=<id>` and stop. Re-check on next webhook fire. If the meeting is past and the bot is stuck, see Pitfall #13.

1b. Check for `.joining` file in the meeting directory — if it exists, the bot was dispatched but may not have reached the `status.json`-writing phase. **Before branching on `exit_reason`, check whether `status.json` also exists.** If `status.json` shows `exited: true` with valid transcript, the `.joining` file is stale metadata — see `references/joining-file-contract.md`. Log `skip-reason: stale-joining-ignored event_id=<id>` and proceed to Step 0b.

    Only branch on `.joining` `status` (and `exit_reason` for "exited") when `status.json` does NOT exist (the bot never got far enough to write it) — **or when `status.json` exists but is a bootstrap artifact** (see `references/status-json-bootstrap-edge-case.md`). A bootstrap artifact has `exited: false`, `inCall: false`, `transcriptLines: 0`, `joinAttemptedAt: null`, `joinedAt: null`. In this case the `.joining` file's `exit_reason` is still authoritative because the bot never actually joined the call.
        - `status: "booting"` — bot is still attempting to join. If the meeting is past its scheduled end and the lock is > 5 minutes old, the bot likely crashed during join. Log `skip-reason: join-crashed event_id=<id>` and stop.
        - `status: "admitted"` — bot successfully joined the call (reached in-call state, started captioning) but `status.json` never got written (the bot stalled or crashed after admission). This is distinct from `"booting"` (never reached in-call) and `"exited"` (left before writing status). **When `status.json` is ABSENT**, log `skip-reason: join-failed:admitted-no-status event_id=<id>` and stop. Do NOT infer auth-failure or drain loop — check `bot.log` for the stall path (likely caption engine crash, OOM, or browser disconnect after admission). See `references/joining-file-contract.md` for the full `"admitted"` contract and observed format.
        - `status: "exited"` with `exit_reason: "auth-failed-sign-in-redirect"` — bot hit Google's sign-in redirect and could not authenticate. **`status.json` MAY exist** with bootstrap state (`exited: false`, `joinedAt: null`, `transcriptLines: 0`) — see `references/status-json-bootstrap-edge-case.md`. The `.joining` exit_reason is still authoritative. No `transcript.json` exists. Log `skip-reason: auth-failure:auth-failed-sign-in-redirect event_id=<id>` and stop. Do NOT write `.finalized` or `.finalizing`. The meeting directory contains `AUTH_ALERT.txt` with recovery instructions. A `.auth_alerted` sidecar may also exist (bot dispatcher confirmation) — see `references/joining-file-contract.md`.

    **Before notifying, check for prior notifications.** Search `~/.hermes/cron/output/meeting-finalize.log` for `auth-failure-notified` with this event_id within the last 10 min. If found, log `already_notified=true` and stop. See `references/persistent-auth-failure-dedup.md`.

    **If no prior notification found:** Notify the tech maintainer (Operator for Example Organization org) via Slack DM with recovery steps from `AUTH_ALERT.txt`.
    - `status: "exited"` with any other `exit_reason` — bot exited during join for a non-auth reason. Log `skip-reason: join-failed:<reason> event_id=<id>` and stop.
    - `status: "exited"` with `exit_reason: null` — generic join failure. Log `skip-reason: join-failed:unknown event_id=<id>` and stop.
    - `status: "exited"` without any `exit_reason` field (key absent entirely) — the `.joining` writer version doesn't include `exit_reason`. This is the "tombstone" state when `status.json` also exists, but when `status.json` is ABSENT it means the tombstone was written before a successful bot lifecycle was established. Log `skip-reason: join-failed:tombstone-no-exit-reason event_id=<id>` and stop. Do NOT infer auth-failure or any specific cause — check `bot.log` for the actual exit path. See `references/joining-file-contract.md` "tombstone state" section.

1c. **Orphaned AUTH_ALERT.txt fallback** — if `.joining` does NOT exist but `AUTH_ALERT.txt` exists AND `status.json` does NOT exist, the bot was dispatched but authentication failed before the `.joining` contract was fully established (bot crashed before writing `.joining`, or `.joining` was cleaned up by a prior crash handler). This is the same auth-failure mode as case 1b but without the `.joining` sidecar. Log `skip-reason: auth-failure:orphaned-auth-alert event_id=<id>` and stop. Do NOT write `.finalized` or `.finalizing`. Read `AUTH_ALERT.txt` for recovery instructions — it contains VNC tunnel steps. Notify the tech maintainer (Operator for Example Organization org) via Slack DM; this blocks all bot attendance until auth is refreshed.

1d. **Covered-by-existing-bot recovery** — `event_meta.json` may have `"status": "covered-by-existing-bot"` with `existing_bot_dir` and `existing_bot_pid`. This means the bot was already in the same Google Meet URL from a prior session and reused its existing browser process. The bot's artifacts (`status.json`, `transcript.json`) live in the `existing_bot_dir` directory, NOT in the event-specific directory. The event-specific directory only has `event_meta.json`.

    **Check this path BEFORE falling through to Step 1's `status.json` missing gate.** Run this check when:
    - The event directory exists (Step 0a passed)
    - No `.cancelled` marker (Step 0a-1 passed)
    - No `.finalized`/`.finalizing` markers (Step 0b passed)
    - `status.json` is ABSENT from the event directory BUT `event_meta.json` exists
    - Read `event_meta.json` — if `status == "covered-by-existing-bot"` and `existing_bot_dir` is set:
      1. Verify the existing bot directory exists at `~/.hermes/workspace/meetings/<existing_bot_dir>/`
      2. Verify it has `status.json` with `exited: true` (bot finished capturing)
      3. Verify it has `transcript.json` with valid content
      4. Copy both files to the event-specific directory: `cp <bot_dir>/status.json <event_dir>/status.json` and `cp <bot_dir>/transcript.json <event_dir>/transcript.json`
      5. Log `covered-by-existing-bot-recovered event_id=<id> existing_bot_dir=<dir> transcriptLines=<N>`
      6. Proceed to Step 0b (normal pre-flight check with the now-populated directory)
    - If the existing bot directory is missing or `status.json` shows `exited: false` with no valid transcript, treat as `bot-never-joined` — the bot was dispatched but never successfully captured for this meeting.

    **Why this is not a failure mode:** The `covered-by-existing-bot` status is a legitimate dispatch outcome. The bot was already in the room. It captured the meeting's transcript. The artifacts just need to be located in the right directory before finalization. Without this check, the flow falls through to Step 1 where missing `status.json` produces `meeting-not-complete` — a misclassification that implies the bot was dispatched but didn't capture, when in fact it captured successfully.

    **Observed 2026-07-13:** Meeting `2uimskdjr0d3ec2qfeg3isk8ku_20260713T100000Z` (Example OrganizationXExampleAnalytics_Weekly Sync). The bot was already in meet URL `abc-defg-hij` from a prior meeting. `event_meta.json` had `"status": "covered-by-existing-bot"`, `"existing_bot_dir": "abc-defg-hij"`, `"existing_bot_pid": 177570`. The bot directory had `status.json` (exited: true, 39 lines) and `transcript.json` (valid conversation between Manni, Participant A, Organizer). Artifacts were copied and finalization proceeded normally.

2. Check `meeting-auto-join.log` — if the event ID appears with a `skip-reason` (e.g., `bot-already-joining`, `no-accepted-response`, `outside-auto-join-scope`), that's the cause. Log `skip-reason: auto-join-skipped:<reason> event_id=<id>` and stop.
3. **All checks empty** (no directory, no lock, no log entry) — the bot was never dispatched for this meeting. Log `skip-reason: bot-never-joined event_id=<id>` and stop. Do NOT write `.finalized` or `.finalizing`. Do NOT attempt a retroactive join. This is terminal — there is no transcript to recover. If this meeting should have been joined, escalate to `meeting-auto-join` configuration.

Only proceed to Step 0b when the directory exists.

### Step 0a-1: `.cancelled` marker check

After confirming the meeting directory exists (Step 0a passed), check for a `.cancelled` marker at `~/.hermes/workspace/meetings/<event_id>/.cancelled`. If present:

- Read `.cancelled` content (JSON with `cancelled_at`, `by`, `reason`, `bot_pid`). Log the reason.
- Kill the bot if still alive — read `status.json.pid`, send `SIGTERM` to the PID, wait 1-2s for graceful shutdown. If bot already dead (PID not found), skip kill step.
- Log `skip-reason: meeting-cancelled event_id=<id> by=<by> reason=<reason>` and stop.
- Do NOT write `.finalizing` or `.finalized` — the `.cancelled` marker IS the terminal state.
- Do NOT trigger any downstream skills (no Notion page, no triage DM, no status update).

**Why this runs BEFORE Step 0b:** A cancelled meeting should never enter the idempotency or finalization pipeline. The marker is checked at the earliest possible point after confirming the directory exists — before `.joining` checks, before `.finalizing`/`.finalized` gates, before drain-loop recovery. This is deliberate: oral cancellation overrides all other meeting state.

### Step 0a-2: Recurring event artifact recovery from slug directory

**Check for `existing_bot_dir` in `event_meta.json`.** When `meeting-auto-join` dispatches a bot to a recurring meeting URL (shared Meet URL across occurrences), it may write bot artifacts (`status.json`, `transcript.json`, `bot.log`, `.joining`) under the Meet slug directory (e.g., `abc-defg-hij/`) rather than the event-ID directory. The event-ID directory contains only `event_meta.json` with `existing_bot_dir: "<slug>"` and `status: "covered-by-existing-bot"`.

**Recovery:**
1. Read `event_meta.json` in the event-ID directory. Check for `existing_bot_dir`.
2. If `existing_bot_dir` is present: look for bot artifacts at `~/.hermes/workspace/meetings/<existing_bot_dir>/`.
3. If the slug directory exists AND contains `status.json` and `transcript.json`, copy them to the event-ID directory:
   ```
   cp ~/.hermes/workspace/meetings/<slug>/status.json ~/.hermes/workspace/meetings/<event_id>/status.json
   cp ~/.hermes/workspace/meetings/<slug>/transcript.json ~/.hermes/workspace/meetings/<event_id>/transcript.json
   ```
4. If the slug directory has a `.joining` file that is a tombstone from a prior occurrence, log `stale-joining-ignored` and ignore it (same logic as Step 0a check 1b).
5. If the slug directory does NOT exist or has no bot artifacts, log a note (`existing-bot-dir-not-found event_id=<id> slug=<slug>`) but continue — the drain-loop or Step 1's missing-status check will handle the failure correctly.

**Verification:** The slug directory's `status.json` will have `eventId: null` and `meetingId: "<slug>"`. The PID in `status.json.pid` matches the bot process. File contents are valid — the bot captured the meeting under the shared Meet URL.

**When to skip:** If `event_meta.json` has NO `existing_bot_dir` field, skip this step. The bot artifacts are where the skill expects them (event-ID directory).

See `references/recurring-event-slug-artifact-recovery.md` for the full pattern, observed examples, and troubleshooting.

### Step 0b: Pre-flight idempotency check

**Before doing ANY downstream work**, check these markers in the meeting directory (`~/.hermes/workspace/meetings/<event_id>/`):

1. **`.finalized` exists** → meeting already finalized. Log `skip-reason: already-finalized` and stop. Do NOT re-process.

2. **`.finalizing` exists and is non-empty** → meeting is currently being processed (or was processed and is in triage state).
   - Read the `.finalizing` JSON. Check `started_at`.
   - **Check `triage-context.json` first.** If it exists → the meeting is in triage-pending state (organizer hasn't approved yet). Log `skip-reason: triage-pending-awaiting-approval` and stop. Do NOT re-send the DM, do NOT re-create the Notion page. The first session already handled everything up to the approval gate.
    - **If `triage-context.json` does NOT exist** (the prior session crashed before Step 5):
      - If `notion-page.json` exists, the page is already authoritative. Resume that exact page: verify/attach missing transcript files and continue to the organizer handoff. Do not remove the lock and do not create/search for another page.
     - Check whether the lock's associated session is still alive. Read `status.json.pid` and run `ps -p <pid>`. If exits non-zero, the process is dead.
     - **If the PID is dead:** the session crashed before completing triage setup. The lock is orphaned regardless of age. Remove it and proceed. Log `skip-reason: orphaned-lock-pid-dead event_id=<id> started_at=...` for traceability.
     - **If the PID is still alive** (bot still running or another agent session is genuinely active): if < 30 minutes old, log `skip-reason: finalization-in-progress` and stop. If > 30 minutes old, the lock is stale despite a live process — log `skip-reason: finalization-in-progress` and stop; the live process likely exited but the PID was reused by an unrelated system process. Do NOT remove a < 30 min lock when the PID appears alive, even if triage-context is absent — the session may still be in progress between Step 3b and Step 5.
   - **Old threshold (fallback — when PID check is impossible):** If the `.finalizing` JSON contains `started_at` and the file is < 30 minutes old AND you cannot check the PID, log `skip-reason: finalization-in-progress` and stop. If > 30 minutes old AND no `triage-context.json`, the lock is stale — remove it and proceed.
   - **Why this matters:** A session can write `.finalizing` and crash before writing `triage-context.json` (crashes between Step 3b and Step 5). The < 30 min age alone is not sufficient evidence that the session is still running — always verify the PID. This was observed 2026-07-08 for Example Organization Daily Standup `33kpfbnc7p7aapl9mjdaa94nhk_20260708T023000Z`: the `.finalizing` lock was 2 minutes old but the PID was dead and no triage-context existed. The orphaned lock prevented the next webhook fire from finalizing the meeting until the stale state was recognized.

3. **`.finalizing` exists and is empty (0 bytes)** → stale lock from a crashed session (per Pitfall #10). Remove it and proceed.

**Why this matters:** Each webhook trigger creates a BRAND-NEW agent session with NO memory of prior sessions. Without this check, every webhook fire re-runs the entire finalization pipeline: re-creates Notion pages, re-sends Slack DMs, re-writes triage context. The `.finalizing` lock is useless if nobody checks it first.

**This step MUST run before Step 1.** Do not extract meeting details, do not read transcripts, do not call any downstream skills until this check passes.

**Re-verification guard (filesystem caching):** `ls` and `read_file` can return stale directory views on GCP persistent disks and other networked filesystems — files written by a prior session may not appear in `ls` output until the write-behind cache flushes. Observed 2026-06-23 for meeting `SYNTHETIC_EVENT_ID`: a prior session's `.finalizing` (written at 10:11, 119 bytes) and `triage-context.json` (written at 10:11, 3184 bytes) were invisible to `ls` at 10:30, then appeared moments later. If you proceed past Step 0b (no markers found) and later discover markers exist (via `read_file` returning unexpected content, or a second `ls` showing files the first missed), stop immediately. Re-run the Step 0b check. If `.finalizing` AND `triage-context.json` are both present, log `skip-reason: triage-pending-awaiting-approval` and stop — even if that means abandoning partially-completed downstream work. The prior session's state is authoritative; overwriting markers or re-creating pages is worse than a wasted session.

**Double-check before Step 3b write:** Before writing the `.finalizing` lock in Step 3b, do ONE more `read_file`-style directory scan to re-verify Step 0b's negative result. This catches the filesystem-cache delay case. See Pitfall #34.

### Step 0c: Drain-loop recovery check (before Step 1's exit gate)

**Check for drain-loop condition BEFORE Step 1's hard `exited != true` gate.** The `status.json.exited == false` + bot-still-alive pattern can be a legitimate drain loop (Pitfall #13) where the bot's browser-side meeting-end detection failed but the transcript was captured successfully. Do not skip straight to `meeting-not-complete` — attempt recovery first.

**Prerequisites:** The meeting directory exists (Step 0a passed) and no `.finalized`/`.finalizing` markers are present (Step 0b passed). The webhook has fired, meaning the calendar service considers the meeting ended.

**Check these three conditions:**
1. **Meeting is past its scheduled end** — The webhook's `end` timestamp is in the past. The meeting ended.
2. **Bot process is still alive** — `ps -p <pid>` succeeds for the PID in `status.json.pid`.
3. **Transcript has valid content** — `transcript.json` exists, has > 0 lines, and passes the sparse-transcript quality gate (Step 3). **Do NOT check `status.json.transcriptLines`** — it lags behind the actual file. Read the file directly.

If ALL THREE conditions are true → the bot is in a drain loop. **Recover:**

1. Verify `transcript.json` passes sparse-transcript detection (Step 3's quality gate). Read file directly — ignore `status.json.transcriptLines` (known-lagging counter per Pitfall #13).
2. Send SIGTERM to the bot: `kill -TERM <pid>` (from `status.json.pid`)
3. Wait 1-2 seconds for graceful shutdown
4. Verify `status.json.exited` is now `true` and `ps -p <pid>` fails
5. Proceed to Step 1 — the `exited` gate will now pass

**Natural-resolution edge case — two variants:**

   **Variant A (existing): `kill` returns `No such process`.** Between Step 0c's state check (condition 2: `ps -p <pid>` succeeds) and the SIGTERM in recovery step 2, the bot's own drain-loop detection kicks in and the bot exits on its own. `kill -TERM <pid>` returns `No such process` — the bot already exited. This is NOT a recovery failure. Proceed directly to recovery step 4: re-read `status.json` and verify `exited: true`. If the re-check shows `exited: true` and `ps -p <pid>` now fails, proceed to Step 1. Observed 2026-07-02 for Example Organization Daily Standup `33kpfbnc7p7aapl9mjdaa94nhk_20260702T043000Z`: resolved in the ~1-second gap between the initial `ps` and the `kill` command.

   **Variant B (observed 2026-07-08): `kill` succeeds (exit 0, no error), bot lingers, then resolves after the first re-check.** `kill -TERM <pid>` returns exit 0 with no error, but the subsequent `sleep 2; ps -p <pid>` still shows the process alive and `status.json.exited == false`. The bot hasn't exited yet — it is processing the SIGTERM (writing a final `_flush()`, closing the browser, etc.) but hasn't completed. The agent may have already logged `drain-loop-recovery-failed` and written the skip-reason. **Wait and re-check once more** — the bot may exit naturally within a few more seconds (observed: process died and `status.json.exited` changed to `true` ~3 seconds after the first re-check). If the second re-check succeeds (`ps -p <pid>` fails AND `status.json.exited == true`), proceed to Step 1. If it still fails (bot unresponsive to SIGTERM > 5 seconds total), then finalize the `skip-reason: drain-loop-recovery-failed` and stop. Do NOT write the skip-reason on the first failing re-check — give the bot its ~3-second window to finish processing SIGTERM. Observed 2026-07-08 for Example Organization Daily Standup `33kpfbnc7p7aapl9mjdaa94nhk_20260708T023000Z`: the first re-check showed the bot still alive, and by the second re-check (~3s later) it had exited cleanly.

   **Why Variant B is distinct:** In Variant A the bot pre-empts the agent's SIGTERM entirely — the signal never reaches a live process. In Variant B the bot receives the signal, acknowledges it (SIGTERM returns 0), but takes several seconds to complete its shutdown sequence (browser close, file flush). The agent must distinguish between "bot is permanently stuck" (true drain-loop-failure) and "bot is processing SIGTERM" (pending resolution). The distinguishing signal is a single re-check after ~3s: if the bot has exited, it was processing; if still alive after that, it is stuck.

**If recovery fails** (bot doesn't exit after SIGTERM, or `transcript.json` is empty/sparse after SIGTERM, or `kill -TERM` returns `No such process` AND re-check shows `status.json.exited` is still `false`), log `skip-reason: drain-loop-recovery-failed event_id=<id>` and stop. Do NOT fall through to Step 1's `meeting-not-complete` — that path implies the bot never successfully captured, which is a different failure mode.

**If conditions 1-2 are met (bot alive, meeting past end) but condition 3 is NOT met (transcript is sparse/empty):** The bot is still running but won't produce useful output — it's a zombie, not a drain loop. **Always SIGTERM the bot as a cleanup action** (`kill -TERM <pid>` from `status.json.pid`), wait 1-2 seconds for graceful shutdown, then check the exit state:
  - See `references/zombie-bot-pid-reaping.md` for the exit-check decision tree. The key insight: `status.json.exited` is the authoritative signal; `ps -p <pid>` is secondary (the PID may linger as a zombie after the bot has already written `exited: true`).

Do NOT fall through to Step 1's `exited != true` gate after this cleanup — the bot's exit state is now clean (`exited: true`), and Step 3's quality gate is the correct handler for sparse content. The `meeting-not-complete` path is only for bots that have genuinely not finished capturing, not for bots that were cleanly terminated post-meeting.

**If conditions 1-2 are NOT met** (bot already dead or meeting still within its window), proceed to Step 1's normal `exited != true` gate.

### Step 1: Extract meeting details from webhook payload

The webhook POST body contains the meeting end event. Extract:
- `meet_url` — the full Meet URL (e.g., `https://meet.google.com/abc-defg-hij`)
- `event_id` — the calendar event ID from the webhook `id` field (e.g., `5j6dcepnuedkc6ujq3qua0eb9q`). **Use this as the directory name.** This is the same `id` that `meeting-auto-join` used when creating the directory. The Meet slug extracted from `meet_url` is NOT unique for recurring events — every occurrence of a recurring meeting shares the same Meet URL.
- `status_path` — path to `status.json` (defaults to `~/.hermes/workspace/meetings/<event_id>/status.json`)
- `transcript_path` — path to `transcript.json` (defaults to `~/.hermes/workspace/meetings/<event_id>/transcript.json`)

**Important:** The `id` field in the webhook payload is the calendar event ID (e.g., `5j6dcepnuedkc6ujq3qua0eb9q`), NOT the Meet slug. The meeting-auto-join skill creates directories using this event ID. Always derive the directory name from the webhook `id` field, never from the Meet slug in `meet_url`.

**Recurring event ID format:** For recurring calendar events, Google Calendar appends a date-suffix to the base event ID: `{base_id}_{YYYYMMDD}T{HHMMSS}Z`. For example, a recurring Sprint Planning with base ID `0cpfrgfe0gdosv0g4hhqpiaqr8` produces occurrence IDs like `0cpfrgfe0gdosv0g4hhqpiaqr8_20260629T073000Z`. The meeting-auto-join skill creates directories using the full occurrence ID (base + suffix), so the directory name always matches the webhook `id` field. Do NOT strip the suffix — use the full webhook `id` as-is for directory lookups. See `references/recurring-event-id-pattern.md` for examples and the pitfall about base-ID-only lookups failing.

If `status.json` is missing or `exited != true` — BUT ONLY after Step 0c's drain-loop recovery was attempted and either didn't apply or failed — log `skip-reason: meeting-not-complete` and do not finalize.

### Step 2: Normalize the input

Build the input object for `meeting-transcript-to-notion`:

- `title` — from `event_meta.json` sidecar (written by `meeting-auto-join`). If missing, fall back to `Meeting on <date>`.
- `date` — derive from `status.json.joinedAt` (unix ts), formatted in `Asia/Bangkok`.
- `attendees` — from `event_meta.json`. Plugin doesn't capture attendees.
- **Email ≠ Notion email** (CRITICAL): A person's email in the webhook/calendar may differ from their Notion account email. Example: Organizer is `person@example.org` in calendar/webhook but `person@example.org` in Notion. Always resolve attendees via `GET /v1/users` and match `person.email` — never assume the calendar email matches the Notion email. Use `write_file` + `terminal` (write a Python script to `/tmp/`, execute with `python3 /tmp/script.py`) — do NOT attempt `execute_code`, it is not a Hermes tool in this environment. See Pitfall #20 for the safe script-writing pattern, and `references/attendee-resolution-pattern.md` for the full attendee resolution workflow.
- `transcript_text` — follow the high-fidelity precedence above. When canonical completed, read
  `transcript.canonical.jsonl`; otherwise use the explicitly approved provisional fallback. Extract
  `ts`, `speaker`, `text` from each line. **Use resilient parsing** (see
  `references/transcript-corruption-recovery.md`) — some legacy transcript lines may have trailing
  garbage characters or missing newline separators (Pitfall #40). No progressive caption collapse
  needed — each line is already a distinct speaker turn. Build transcript text as
  `[ts] Speaker: text` per line.
- `meet_url` — from `status.json.url`.

`finalize_vexa_transcription.py` must finish the readability stage before setting
`finalization_ready=true`. When all three `TRANSCRIPT_EDITOR_*` settings are configured it invokes
`edit_transcript_with_ai.py` automatically; do not rely on the finalizing agent to remember a
separate optional command. It writes:

- `transcript.edited.v1.jsonl` — edited speaker turns linked to source segment IDs and hashes;
- `transcript.edited.md` — human-facing transcript;
- `transcript.edit-ledger.jsonl` — per-turn validation and fallback evidence;
- `transcript.editor.manifest.json` — model, source digest and accepted/fallback counts.
- `transcript-readable.md` — the stable human-facing attachment, formatted as compact turns:
  `[5:19] Operator Nguyen: Hi`.

This is a **third editorial stage after the second Deepgram pass**, not a replacement for canonical
evidence. The model may remove verbal fillers, stutters, accidental repetitions and abandoned false
starts and may repair punctuation. It must not summarize, paraphrase, add facts, change names,
technical terms, numbers, URLs, emails or negation. The script rejects unsafe edits and falls back to
the original turn. Always retain and attach the canonical verbatim transcript as the audit source.

If the editor is unconfigured or fails, finalization deterministically writes the same compact
`transcript-readable.md` from canonical turns and records `readability_status=canonical_fallback`.
Never overwrite this file later in the agent workflow, and never render one heading or one bold
timestamp/speaker row per raw STT segment into Notion.

### Step 3: Validate transcript quality

**Quality gate**: Validate transcript is not sparse/garbage (see `references/sparse-transcript-detection.md`). The detection algorithm now pre-filters bot-self "You" speaker entries (null-sink mic noise) before evaluating thresholds, avoiding inflated short-utterance density from join/leave fragments. If flagged:
  - Write `.review-flag.txt` marker
  - Log `skip-reason: sparse-transcript event_id=<id> transcriptLines=<N> reason=<threshold_reason> totalChars=<N>`
    Enrich the log line with the specific threshold that fired (e.g. `short_utterance_dense`, `minimal_collapse_4_to_1`), the raw line count, and the total character count. This distinguishes re-triggers with stale data from new transcript states.
  - **Do not write `.finalized`** — next webhook trigger re-evaluates
  - Stop processing this meeting

### Step 3b: Write `.finalizing` lock

Before invoking any downstream skills, write the `.finalizing` lock file to the meeting directory. This is the **acquire** — the check was in Step 0, the write is here. Content:

```json
{"started_at": "<ISO timestamp now>", "event_id": "<from webhook payload>", "organizer": "<from webhook payload>"}
```

This lock must exist BEFORE the first Notion API call or Slack message. If the agent crashes between Step 0 and Step 3b, the lock won't exist, and the next webhook fire will correctly re-run from scratch. If the agent crashes AFTER Step 3b, the lock exists and the next fire will see it in Step 0 and skip.

### Step 3c: Invoke the downstream skill chain

For the meeting, in order, **synchronously**:

**Invocation pattern:** The skill chain below can be invoked via `delegate_task` (recommended for non-time-critical flows) or via direct implementation (recommended for webhook-driven synchronous flows that must complete quickly). For webhook-driven finalization:
- **Webhook context (this skill):** Use direct implementation to avoid delegation latency. Normalize the input data (Step 2 output) and call each step's logic inline, or invoke them as stateless functions.
- **Manual finalization flow:** Use `delegate_task` to delegate to each downstream skill separately.

**Branching logic — ALL organizers go through triage. No fast path. No task creation without approval.**

- **Every organizer (including Organizer, including Meeting Bot):** Go triage route (Steps 4–5, no task creation). Inline-implement page creation in Step 4 (do NOT invoke full `meeting-transcript-to-notion`; it creates tasks automatically, which breaks the triage gate). Nothing gets created without the organizer's explicit approval.

**Invoke downstream skills inline (no delegation — run directly in this session):**

### Step 4: Create Notion Meeting Notes page (NO tasks yet — triage flow)

**Output-language contract:** All derived communication is English regardless of transcript
language. This includes the Notion Summary property and page notes, discussion topics, decisions,
blockers, proposed action descriptions, the `#general` summary, attendee email, and organizer
approval DM. Preserve names, product terminology, ticket IDs, URLs, numbers, and commitments.
Readable and canonical transcript attachments remain in the language actually spoken; never
translate or overwrite those evidence artifacts.

**For triage workflows (always — no fast path):** Inline-implement this step using Python urllib (see `meeting-transcript-to-notion` skill for API pattern). Do NOT invoke the full `meeting-transcript-to-notion` skill — it creates tasks immediately, which breaks the triage gate.

Immediately after a successful `POST /v1/pages`, atomically write `notion-page.json` with `event_id`,
`page_id`, `page_url`, and `status=created` **before uploading an attachment or making another API
call**. On retry, this receipt wins; verify and resume the same page. If the API response was lost,
run `scripts/meeting-trigger/vexa-publication-recovery.py` to reconcile by exact meeting date/title.

**CRITICAL — use the Meeting Notes DB, not the Tasks DB.** The Meeting Notes database is `${CONFIGURED_NOTION_ID}`, NOT the Tasks DB (`${CONFIGURED_NOTION_ID}`). Using the Tasks DB will return `400 validation_error` because its properties (Task name, Assignee, Status, Priority) don't match the Meeting Notes schema.

Meeting Notes DB properties for `POST /v1/pages`:

| Property | Type | Example |
|---|---|---|
| `Meeting name` | title | `[standup] Example Organization Daily — Caption Test` |
| `Date` | date | `{"start": "2026-06-17"}` |
| `Category` | multi_select | `[{"name": "Daily Standup"}]` |
| `Attendees` | people | `[{"id": "<notion-user-id>"}]` — resolved Notion IDs only |
| `Summary` | rich_text | Brief 1-2 sentence description |

See `meeting-transcript-to-notion` skill's "Database Quick Reference" section for the full schema and additional context.

**No fast-path workflows.** Every organizer — Meeting Bot, Organizer, anyone — goes through triage. Do NOT invoke `meeting-transcript-to-notion` here. The triage DM goes out in Step 5; tasks are created in Step 7 only after the organizer approves.

**Page should contain:**
- Title, date, attendees, transcript summary
- A "Proposed Action Items" section in the page body — one bullet per item, one line each. Brief inline description if needed (same line), priority inline. No child paragraphs. Do NOT paste transcript caption snippets in the bullet text — the transcript is already attached as a separate file. See `references/action-item-extraction.md` Step 6 for format rules and examples.
- Status: "Pending Organizer Approval"

**⚠️ CRITICAL — Proposed Action Items must be real, not a placeholder.** The Proposed Action Items section in the Notion page body must contain the actual proposed items with assignees — NOT a placeholder like "Action items will be proposed by Meeting Bot after review" or "See Slack DM for details." The organizer opens the Notion page to review and approve; the page is the primary artifact, the DM summary is secondary. A placeholder creates a broken page that requires the organizer to cross-reference the DM, defeating the purpose of a single source of truth. This was observed 2026-07-06 for Sprint Planning `0cpfrgfe0gdosv0g4hhqpiaqr8_20260706T073000Z`: the agent created the page with a placeholder and only put the real items in the DM. The page is incomplete until the next agent session patches it. See `references/action-item-extraction.md` for the canonical extraction pattern.

**⚠️ FORMAT — ONE bullet per item, NO descriptions, NO transcript quotes.** Each proposed action item must be a single `bulleted_list_item` block — nothing else. Do NOT add child paragraphs with descriptions, transcript refs, or explanation text below each bullet. The page must be scannable at a glance. Format: `[Assignee] Task description`. Examples:

```
[Participant C] Fix UI issues and clarify animation requirements
[Operator] Fix Example Organization Strapi issues for frontend integration
[Participant A] Test Example Analytics platform
```

The detailed context (priority, transcript source lines, descriptions) belongs in `triage-context.json` and the Slack DM — NOT in the Notion page body. This was enforced 2026-07-07 after Participant A reported the page was too verbose to read ("h cứ viết dài dài ko muốn đọc luôn").

**Do NOT create Notion tasks.** Tasks will be created only after the organizer approves them (Step 7, triage flow only).

**Preferred deterministic completion:** once `notion-page.json` is durable and the page body contains
Summary, Decisions, and Proposed Action Items, run:

```bash
python3 ${HERMES_HOME}/scripts/meeting-trigger/vexa-publication-recovery.py --meeting-dir <meeting-dir>
```

When it returns `completed`, it has idempotently attached both transcript artifacts, published the
summary/link once to `#general`, sent the organizer approval DM once, and written
`triage-context.json`. Skip the manual attachment, contact-sync, and triage-DM work below and continue
at Step 5b. `needs_review` is a real terminal review condition; never create a second page to work
around it.

**⚠️ Transcript attachment:** After creating the page, always upload `transcript-readable.md` as the
primary transcript. `transcription.state.json` proves whether the AI editor ran and how many turns
were edited or fell back. The organizer must see one compact line per speaker turn. Attach the
canonical segment-level file second and label it **Verbatim canonical transcript — audit source**.
Use the `attach-transcript-to-notion.py` helper from
`meeting-transcript-to-notion` (see that skill's Step 5b for full documentation):

```python
import subprocess, os
script = os.path.expanduser(
    "${HERMES_HOME}/skills/meeting/meeting-transcript-to-notion/scripts/attach-transcript-to-notion.py"
)
result = subprocess.run(
    [sys.executable, script, transcript_path, page_id,
     "--receipt", os.path.join(meeting_dir, ".notion-attachment-readable.json"),
     "--title", meeting_title,
     "--roster", os.path.expanduser("~/.hermes/team-roster.yaml")],
    capture_output=True, text=True, timeout=30
)
if result.returncode == 0:
    # Success — log it
else:
    # Non-blocking: log the error but continue with triage flow
```

The helper accepts the already-rendered `.md` and canonical `.jsonl` files directly and checks existing
file blocks before uploading, so retries are idempotent. Attach the readable and canonical files with
separate receipts. **If the transcript is empty (0 lines) or an attachment fails, post a visible yellow
callout warning block on the page instead** — log the degraded attachment and proceed to Step 5. An
attachment failure alone must never produce a retryable `notion-attachment-failed` terminal receipt or
block the organizer triage flow.

### Step 4: Sync contacts (optional)

**`save-contact-to-notion`** — sync any new contacts from the attendee list. Skip if skill not installed.

### Step 5: Send triage DM to organizer

**`post-meeting-approval`** — send a Slack DM to the meeting **organizer** (not Meeting Bot) with:
- Notion page URL
- Transcript summary
- Proposed action items (with suggested assignees)
- Request for approval/modification/rejection
- **`ctx:<event_id>` reference** — embed `ctx:<event_id>` (where event_id is the webhook `id` field) in the DM text so the `retrieve-meeting-flow-context` skill can rehydrate the agent when the organizer replies. Place it at the end of the DM body, after the approval request. Without this reference, the organizer's reply won't automatically trigger context retrieval — the agent will have no way to connect the reply back to this meeting's `triage-context.json`.

This step also writes `triage-context.json` with the proposed (not yet created) action items. See `references/triage-context-schema.md` for the full JSON schema — use the documented field names and types, including `test_session` (boolean, `true` for Pitfall #21 test sessions), `created_at` (ISO 8601 UTC), `ttl` (created_at + 24h), and `approval_status` (`"pending"` on creation).

**Every organizer gets the triage DM — no exceptions.** No tasks are created without the organizer's explicit approval. Proceed to Step 6 (always Case B — triage pending).

### Step 5a: Publish summary and transcript link to #general

Post the concise summary and Notion page link to `#general` immediately. State that action items remain
pending organizer approval. Include `Meeting ID: <event_id>` in the message and use
`<meeting-dir>/.slack-general.json` as the mode-0600 receipt. Before sending, inspect recent channel
history for either that event marker or the exact Notion URL. Never wait for task approval to share the
meeting summary, never send the same meeting twice, and never publish proposed action details in
`#general` before approval.

### Step 5b: Email the draft notes to internal attendees

After `triage-context.json` exists, run `scripts/send-transcript-email.py` with `event_meta.json`,
`triage-context.json`, and `<meeting-dir>/transcript-email.json` as the receipt. This user-approved
policy sends the Notion link and concise summary to named `@example.org` and `@example.net` attendees
only. It excludes declined attendees, Meeting Bot bot accounts and every external address. It never attaches
the raw transcript, identifies Vexa as the source, and says that action items remain pending organizer
approval. The mode-0600 receipt makes retries idempotent. Test sessions and no-internal-recipient
meetings are skipped. The sender also scans the meeting directory for any prior sent email receipt;
different publishers, receipt filenames or Notion URLs must not generate a second automatic attendee
email. A send failure must be logged and reported to the organizer but must not delete the Notion page
or block the Slack triage flow.

### Step 6: Write .finalized — single case (triage)

**Every meeting goes through triage.** Tasks are never created automatically:
→ Do NOT write `.finalized` yet. Meeting is in `triage` state.
→ The `.finalizing` lock stays in place.
→ Wait for organizer reply via Slack (triggers `retrieve-meeting-flow-context`).
→ Only after approval → create/update tasks → write `.finalized` → optionally post the approved-action update to #general. The summary/link was already published in Step 5a.

### Step 7: Post-approval task creation (Case B only)

Two paths into this step:

**Path A — Inline reply (agent in same thread):** The agent session that sent the triage DM receives the organizer's approval reply directly as a new conversation turn (no webhook). The agent is already in the right context and should proceed immediately through Step 7. This is the most common path when the organizer replies to the agent's DM thread in Slack.

**Path B — Webhook re-trigger (`retrieve-meeting-flow-context`):** A new agent session picks up the reply via webhook, reads the `triage-context.json`, and processes the approval. Requires the `ctx:<meeting_id>` reference to be present in the triage DM.

Both paths converge on the same steps:

1. **Interpret approval signal:**
   - `"Approve"`, `"Approved"`, `"LGTM"`, `"Looks good"`, `"Yes"`, or an emoji-like sentiment → approve all proposed items
   - Specific modifications (reassign, reword, remove specific items) → apply changes selectively
   - `"Reject"`, `"No"`, `"Cancel"` → reject all items, skip task creation, write `.finalized` with `approval_status: rejected`

2. **Load `triage-context.json`** with proposed action items and resolved Notion user IDs.

3. **Create approved tasks in Notion** using the Tasks DB (`database_id: ${CONFIGURED_NOTION_ID}`):
   - Use `properties` with correct field names: `"Task name"` (title), `"Assignee"` (people → Notion user ID), `"Priority"` (select), `"Status"` (status → `{"status": {"name": "Not Started"}}`)
   - Always resolve assignee emails to Notion user IDs via `triage-context.json.attendees[n].notion_id` — stored from Step 2's attendee resolution
   - **Before creating, check for existing tasks** — Query the Tasks DB by Task name (title filter) for each proposed action item. Use `POST /v1/databases/{db_id}/query` with a filter on the `Task name` title property containing key words from the item. If a match already exists (from a prior session, manual logging, or a recurring issue), skip creation and log `skipped-existing: <task_name> -> <existing_url>`. Do NOT create duplicates. This applies particularly to recurring/multi-session issues like the Trip Tips Google Maps fix — always search first.
   - Rate-limit: 300ms between POST calls (~3 req/s)
   - Capture returned page IDs for later block appending

4. **Update the page body status** — Find the paragraph block containing "Status: Pending Organizer Approval" in the meeting notes page body. PATCH it to `"Status: Approved — tasks created by Meeting Bot"` (or the appropriate agent name). Use `PATCH /v1/blocks/{block_id}` with the paragraph block's full ID. **Do NOT skip this step** — the status text is the only triage-completion signal visible on the Notion page when the user opens it, and omitting it was the #1 user complaint (observed 2026-07-03 Demo & Backlog Refinement).

5. **Apply any name/scope corrections from the approval signal** — If the organizer said "Rapid should be Example Analytics" or similar name/scope corrections, PATCH the original bulleted_list_item blocks in the "Proposed Action Items" section of the page body. Update the text inline via `PATCH /v1/blocks/{block_id}` with the corrected rich_text. Do NOT leave stale names in the proposed-items section.

6. **Append task links to the Meeting Notes page** (`PATCH /v1/blocks/{page_id}/children`):
   - Add a `heading_2` block (`"✅ Approved Action Items (Approve with changes)"` or `"✅ Approved Action Items"` as appropriate)
   - Then one `to_do` block per task with unchecked status and a link to the task's Notion page URL
   - ⚠️ **CRITICAL — link placement:** The hyperlink MUST be on the **task description rich_text segment (first segment)**, NOT on the assignee name (second segment). Wrong: `[{"content": "task text — ", "link": null}, {"content": "Assignee", "link": {"url": "..."}}]`. Correct: `[{"content": "task text — ", "link": {"url": "..."}}, {"content": "Assignee", "link": null}]`. If the link is on the assignee name, the user sees no clickable hyperlink on the action item text itself.
   - Task URL format: `${CONFIGURED_SOURCE_URL}` (e.g., `${CONFIGURED_NOTION_ID}`)
   - If a `"✅ Approved Action Items"` section already exists (from a prior partial append attempt), replace it by appending AFTER the existing divider and using `PATCH` on the existing to_do blocks to fix links, rather than duplicating the section. Prefer in-place PATCH over delete+re-append.

6b. **Verify page state before finalizing triage context** — After appending task links and applying name/scope corrections, verify the page blocks via `GET /v1/blocks/{page_id}/children`. Confirm all three:
   - (a) The status paragraph block says "Status: Approved — tasks created by Meeting Bot" (or the appropriate agent name)
   - (b) The to_do blocks exist under the ✅ Approved Action Items heading with hyperlinks on the first (task description) segment
   - (c) Any name/scope corrections from item 5 were applied to the bulleted_list_item blocks in the Proposed Action Items section
   If any check fails, retry the failed item (4, 5, or 6) before proceeding to item 7. If all pass, proceed to finalize the triage context. **Do NOT skip this verification** — writing `.finalized` with a broken page state is worse than never writing it, because `.finalized` prevents all future sessions from touching the meeting.

7. **Update `triage-context.json`** with `approval_status: approved`, `approved_at` timestamp, `approval_source: slack-reply`, and a `tasks_created` array mapping task IDs to task names and URLs.

8. **Write `.finalized`** with approval details (approval_status, approved_at, event_id, title, notion_page_url, tasks_created count).

9. **Remove `.finalizing` lock** — meeting is fully finalized.

10. **Send Slack update to #general channel** with Notion page URL and final task list.

**If no reply within 24h (TTL expires):**
→ Write `.finalized` with `approval_status: expired`
→ Do NOT create tasks (organizer didn't approve)
→ Remove `.finalizing` lock

**`decision-log-extraction`:** Skipped — action items proposed in triage DM, created as tasks only after approval.

### Step 8: Dry-run mode

When `MEETING_FINALIZE_DRY_RUN=1`, perform Steps 1 and 2, log the would-be-finalized list, but do not invoke downstream skills and do not write `.finalized`.

## Pitfalls

1. **The plugin's completion signal.** The exact marker is: `status.json.exited == true` AND the bot process is no longer running (no PID match for `status.json.pid`).

2. **Partial finalization.** If `meeting-transcript-to-notion` succeeds but `decision-log-extraction` fails, the Notion page exists and the next webhook trigger will re-create it (since `.finalized` is absent). `meeting-transcript-to-notion` handles duplicate-page suppression via Notion search.

3. **Plugin failures.** If the plugin marks a meeting `failed`, do not finalize it. Surface to a human.

4. **Long meetings near midnight.** Use absolute UTC timestamps everywhere; do not key off date strings.

5. **Sparse/empty transcript.** Two failure modes — empty (0 lines) or sparse/garbage (<2 meaningful utterances after collapse). Route to human review with `.review-flag.txt` marker.

6. **Event metadata sidecar.** If `event_meta.json` is missing: log `skip-reason: missing-event-meta`, do NOT fall back to auto-generated titles. Surface as `meeting-auto-join` failure.

6b. **Organizer conflict: webhook payload is authoritative.** The `event_meta.json` sidecar (written by `meeting-auto-join`) may list a different organizer than the webhook payload — `meeting-auto-join` defaults to `person@example.org` when it can't determine the real organizer. The webhook payload's `organizer` field comes directly from the calendar event and is always correct. Always use the webhook payload organizer for `post-meeting-approval` triage routing, never the `event_meta.json` organizer.

   **Beyond the organizer field:** `event_meta.json.status` and `event_meta.json.skip_reason` are also unreliable — they reflect the auto-join dispatch decision BEFORE the bot joined, and are never updated after success. A meeting can have `"status": "joining"` and `"skip_reason": "external-organizer-unapproved"` in `event_meta.json` while the bot captured 108 transcript lines. See `references/event-meta-stale-state.md` for the full pattern and verification approach.

7. **Linear is deprecated.** Do not create Linear tickets. If `post-meeting-approval` or other downstream skills reference Linear, they should be updated to use Notion Tasks only.

8. **Stale `.review-flag.txt` from previous failed runs.** A `.review-flag.txt` may exist from a prior bot session that failed (e.g., 0 transcript lines captured). If `status.json` shows a successful second session (`exited: true`, `transcriptLines > 0`, bot process not running), remove the stale `.review-flag.txt` and proceed with finalization — the new transcript supersedes the old failure.

   **Also check for algorithm-update stale flags.** If the sparse-transcript detection algorithm was patched since the `.review-flag.txt` was written (e.g., a new pre-filter or threshold gate was added), re-run the detection with the updated algorithm. If it now passes, remove the stale `.review-flag.txt` and proceed. The flag was correct at the time but the algorithm has improved — the old data may now be valid. This was observed 2026-06-18 for meeting `25t01u3mt3tjdcvrrdktsqsmgq`, where a bot-self utterance pre-filter was added after the original flag was written, and the re-run passed clean.

   **Update the flag when root cause is better understood.** If the review flag's `REASON` field says `empty_transcript` (the symptom) but the current session identifies a specific root cause (e.g., `auth-failure:auth-failed-sign-in-redirect`), update the flag's reason and assessment to reflect the correct classification. The substantive condition (empty transcript) is still valid — the flag should not be removed — but the root cause attribution should be more precise for the human reviewer. Do NOT leave a misleading `REASON` field that implies a generic capture failure when the root cause is known. This was observed 2026-07-17 for Example Organization Daily Standup `33kpfbnc7p7aapl9mjdaa94nhk_20260717T023000Z`: the review flag was written by a prior session that hit the `meeting-not-complete` fallthrough, and the `REASON` field said `empty_transcript` while the assessment correctly noted `auth-failed-sign-in-redirect` in the narrative. The root cause was documented in the assessment text but not in the structured `REASON` field.

9. **Transcript format changed.** Empirically verified June 2026: the plugin now writes `transcript.json` (JSON lines format: one JSON object per line with `ts`, `speaker`, `text` fields) instead of the old `transcript.txt` (`[HH:MM:SS] Speaker: text`). The skill must handle the JSON lines format. No progressive mutation collapse is needed when each line is a distinct speaker turn.

10. **Empty `.finalizing` lock file.** A previous webhook trigger may have written `.finalizing` and then crashed before populating it (leaving it at 0 bytes). An empty lock is not evidence of active processing — treat it as stale regardless of mtime, remove it, and proceed. Do NOT skip finalization just because a 0-byte lock file exists.

10b. **Notion task due dates may not be set correctly.** When creating tasks via `meeting-transcript-to-notion`, the agent must explicitly set the `Due date` property using the Notion API date format: `{"date": {"start": "2026-06-08"}}`. Do NOT rely on the ntn CLI to infer due dates from natural language. Always pass the date explicitly in the API call.

11. **Triage context pattern for human-in-the-loop approval (REQUIRED: embed ctx reference in DM).** When the organizer is NOT Meeting Bot and `post-meeting-approval` runs:
   - The skill writes `triage-context.json` containing: meeting metadata, transcript summary, action items with transcript source lines, task URLs, organizer info, 24h TTL
   - **The Slack DM MUST embed a `ctx:<event_id>` reference** (e.g., `ctx:65g7teic5n0oos64u6pdm4bhp8`) in the message body so the `retrieve-meeting-flow-context` skill can rehydrate the agent from the organizer's reply. Place it as the last line of the DM: `Reference: ctx:<event_id>`. Without this, a new agent session processing the reply has no way to find the corresponding `triage-context.json` file — the reply is orphaned.
   - When the organizer replies, the `retrieve-meeting-flow-context` skill rehydrates the agent with full context from the file
   - `.finalized` is NOT written until organizer approval is received (or 24h TTL expires)
   - The `.finalizing` lock stays in place during the wait

12. **`save-contact-to-notion` skill path.** The skill is registered as `save-contact-to-notion` (not `meeting/save-contact-to-notion`). If Step 4 (contact sync) is needed, use the bare name `save-contact-to-notion` when calling skill_view. If the skill is not installed, skip gracefully — contact sync is optional.

13. **Bot stuck in drain loop after meeting end.** The bot's drain loop monitors for the meeting's end condition on the browser side (host removes bot, meeting dissolved, browser disconnect, etc.). Sometimes this detection fails or lags, leaving the bot running indefinitely even though the meeting ended and transcripts were successfully captured.

**Canonical entry point: Step 0c.** The drain-loop recovery is now a formal workflow step (Step 0c) that runs BEFORE Step 1's `exited != true` gate. Follow Step 0c for the full recovery procedure. The details below are the reference for what Step 0c does internally.

**Symptoms:**
- Webhook fired (meeting end detected by calendar service, correct timestamps in webhook)
- `status.json` shows `exited: false` and bot process still running (`ps -p <pid>` succeeds)
- But `transcriptLines > 0` and transcript JSON is valid — content WAS captured successfully

**Recovery:**
1. Verify transcript is non-empty and passes sparse-transcript checks (Step 2 quality gate)
2. Send SIGTERM to the bot: `kill -TERM <pid>` (from `status.json.pid`)
3. Wait 1–2 seconds for graceful shutdown
4. Verify `status.json.exited` now reads `true` and process is gone: `ps -p <pid>` fails
5. Proceed with finalization (Steps 2+)

The bot handles SIGTERM gracefully — it will write `exited: true` and exit cleanly, allowing the transcript to be finalized immediately. Do NOT use SIGKILL (`-9`) — it won't update status.json and will leave the meeting in an incomplete state.

**After SIGTERM: Check transcript.json, not status.transcriptLines.** The drain loop's line counter (`status.json.transcriptLines`) can lag behind the actual file when the bot exits abruptly. Example: `status.transcriptLines=0` but `transcript.json` contains 5 valid lines. **Always verify transcript.json directly** for true line count. If the file has valid content and passes the sparse-transcript quality gate (Step 3), proceed with finalization regardless of what the status counter says.

**Prevention:** This is a plugin browser-detection issue (caption selector stale, DOM mutation observer lag), not recoverable in this skill. If it occurs frequently, escalate to the `google_meet` plugin maintainer with bot.log timestamps.

14. **Duplicate finalization from webhook re-fires (CRITICAL).** Each webhook trigger creates a BRAND-NEW agent session. The agent has NO memory of prior sessions. Without Step 0b's pre-flight check, every webhook fire re-runs the entire pipeline: re-creates Notion pages, re-sends Slack DMs, re-writes triage context. The `.finalizing` lock is useless if the agent doesn't CHECK it before doing work. **Always run Step 0a then Step 0b before Step 1.** This was the root cause of the June 9 duplicate-finalization bug where `abc-defg-hij` got 3+ Notion pages and multiple Slack DMs.

15. **`.finalizing` write timing.** The lock must be written in Step 3b (BEFORE the first downstream API call), NOT after. If the lock is written after Notion/Slack work, a concurrent webhook fire can race past Step 0 and duplicate the work before the lock exists. The sequence is: Step 0 check → Step 3b write lock → Step 3c+ downstream work → Step 6 write `.finalized`.

17. **Webhook subscriptions without `enabled_toolsets` launch agents with NO tools (CRITICAL — observed 2026-06-10).** When the `meeting-finished` webhook subscription was created without specifying toolsets, every agent session reported "I don't have filesystem tools (terminal, read_file, write_file)" — the agent could load the skill document but could not execute any of its steps. The entire 03:30–03:37 window of 8+ webhook fires produced zero output. Fix: add `enabled_toolsets: ["terminal", "file", "skills"]` to the webhook subscription config. See `webhook-subscriptions` skill Pitfall #12.

16. **Ad-hoc joins with unknown organizer.**

17. **`write_file` tool sanitizes env var `KEY=VALUE` patterns.** When writing Python scripts for Notion/Slack API calls via the `write_file` tool, any `KEY=` pattern (e.g., `NOTION_API_TOKEN=***`) gets replaced with `***`, causing `SyntaxError`. **Workaround:** Use the `partition("=")` pattern for `.env` parsing instead of `line.startswith("KEY=")`. See `references/write-file-env-var-sanitization.md` for full details and the canonical safe pattern.

18. **Notion token is available via `os.environ.get("NOTION_API_TOKEN")` in the agent runtime.** The gateway exports this env var at startup (confirmed 2026-07-08: `ntn_4930...` is in `os.environ`). **Use it directly in Python scripts** — no need to parse `.env` or fetch from GCP Secret Manager. Just `tkn = os.environ.get("NOTION_API_TOKEN")` and use it for the `Authorization` header.

    **Important — the env var name is `NOTION_API_TOKEN`, NOT `NOTION_API_KEY`.** The latter is NOT in env. Always use the token form.

    **Slack token is DIFFERENT — must be fetched from GCP Secret Manager.** `SLACK_BOT_TOKEN` is NOT in `os.environ` and NOT in `.env` (the `.env` file contains a redacted sentinel `***`). See Pitfall #35 for the canonical gcloud-based access pattern. Attempting `os.environ.get("SLACK_BOT_TOKEN")` will return `None`.

35. **GCP Secret Manager Slack token access from Python scripts.** The Slack bot token (`CONFIGURED_SLACK_SECRET`) is stored in GCP Secret Manager and is NOT available as a shell env var or in `.env`. Python scripts running in the `terminal` tool must fetch it via `subprocess` + `gcloud`, OR via the Python SDK if `gcloud` is unavailable (see `references/gcp-python-sdk-slack-token.md`):

    **Method A — google-cloud-secret-manager Python library (preferred, use when `gcloud` CLI is absent):**

    ```python
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = "projects/CONFIGURED_CLOUD_PROJECT/secrets/CONFIGURED_SLACK_SECRET/versions/latest"
    response = client.access_secret_version(request={"name": name})
    SLACK_TOKEN = response.payload.data.decode("utf-8").strip()
    ```

    Requires `google-cloud-secret-manager` to be installed (`pip install google-cloud-secret-manager`). GCP metadata server or application-default credentials handle auth automatically when running on GCP infrastructure. Observed working 2026-07-17 when `gcloud` CLI binary was absent but the Python package was available.

    35. **GCP Secret Manager Slack token access** — two approaches documented in `references/slack-token-access-pattern.md`:
        - **Preferred:** `google.cloud.secretmanager` Python library (no PATH dependency)
        - **Fallback:** `subprocess` + `gcloud` (at `/snap/bin/gcloud` if not on PATH)
        **Use configured runtime environment/key-file rather than historical cloud-secret assumptions; original note: `os.environ.get("SLACK_BOT_TOKEN")` — it returns `None`. The gateway never exports this to the agent subprocess. **Do NOT** parse `.env` — it contains `***` (redacted). Secret: `CONFIGURED_SLACK_SECRET`, project: `CONFIGURED_CLOUD_PROJECT` (stable, hardcode).

19b. **Concurrent same-slot meetings cause bot-never-joined.** When two meetings share the same time slot (e.g., "Tomo Web Daily Standup" and "Example Organization Daily Standup" both at 03:00 UTC), `meeting-auto-join` will dispatch a bot for one and skip the other with `skip-reason=bot-already-joining`. The `meeting-finished` webhook fires for BOTH events when they end. The skipped event will hit `bot-never-joined` in Step 0a — this is expected, not an error. No action needed beyond logging the skip reason. If concurrent capture becomes important, the solution is to increase the bot concurrency limit in `meeting-auto-join`, not to modify finalization.

20. **Python scripts for API calls: use `write_file` tool, NOT terminal heredoc.** When creating Python scripts for Notion/Slack API calls (attendee resolution, page creation, DM sending), use the `write_file` tool with `path="/tmp/script.py"`, then execute with `terminal` (`python3 /tmp/script.py`). Do NOT use `cat > /tmp/file.py << 'EOF'` in terminal — the `line.startswith("KEY=")` pattern breaks on shell quoting inside heredocs, producing `SyntaxError: unterminated string literal`. The `write_file` tool avoids all quoting issues. This applies to all downstream skills (`meeting-transcript-to-notion`, `post-meeting-approval`) that use Python urllib patterns.

21. **Test/caption sessions bypass normal meeting finalization.** When the sparse transcript detector flags `single_timestamp_burst` (Threshold 6 in `references/sparse-transcript-detection.md`), the meeting is a connectivity test, mic check, or caption system test — not a real discussion. **Do still create the Notion page and route through triage** (the organizer may want to confirm the test), but do NOT propose any action items.
   - **Notion page**: Use `templates/test-session-notion-page.json` for the page body `children` array. Clearly state "Caption system test session" in the page summary.
   - **`triage-context.json`**: Set `"test_session": true` and `"proposed_action_items": []`. See `references/triage-context-schema.md` for the full schema.
   - **Triage DM**: State "No action items — this was a caption test session" explicitly. Do not leave the action items section empty or ambiguous.
   - **Approval flow**: The triage approval from the organizer is lightweight — typically confirming the test worked or flagging quality issues. Do not create tasks even after approval.

22. **Mock transcript injection: status.json counter is volatile.** When injecting a mock transcript into a live meeting directory (for testing), the `status.json.transcriptLines` counter will be overwritten by the bot's next `_flush()` cycle if the bot is still running (`exited: false`). The bot maintains its own internal counter that only increments for captions it actually captured from the DOM. **The file contents are correct** — `meet_transcript` and the finalize workflow read `transcript.json` directly, not the status counter. But the status counter will mismatch until the bot exits. For testing, either: (a) inject after the bot has exited, (b) accept the counter will be wrong and verify the file directly, or (c) manually patch status.json after injection and accept it may be overwritten. See `references/mock-transcript-injection.md` for the full injection procedure.

23. **Triage DM must go to organizer ONLY.** When the organizer is NOT Meeting Bot and the triage route is active, the Slack DM in Step 5 (`post-meeting-approval`) MUST be sent to exactly one recipient: the meeting organizer's Slack DM channel. Never send to all attendees, never loop over the attendee list. Before executing the send script, verify the `DM_CHANNEL` variable in the script matches the organizer's `slack_dm_channel_id` from the roster. After execution, verify the output shows exactly one `DM_SENT` line. If anything doesn't match, stop — do not write `triage-context.json`. See `references/triage-dm-recipient-investigation.md` for the full investigation playbook if a non-organizer reports receiving a triage DM.

24. **Organizer email ≠ roster key for Slack DM resolution.** The webhook organizer email (`person@example.org`) may not match the email in `~/.hermes/team-roster.yaml` (`person@example.org`). A naive `roster.find(email=webhook_organizer_email)` returns nothing. Resolve in this order: (a) Look up the organizer email via `GET /v1/users` to get their Notion display name (same API call you already make in Step 2). (b) Match that name against roster entries using substring matching (e.g., "Organizer Example" contains "Organizer"). (c) Fall back to known email aliases. (d) Skip the DM if unresolvable — never send the triage DM to non-organizer channels. See `references/organizer-slack-resolution.md` for the full pattern.

25. **Notion API code block rich_text content limit is 2000 characters.** When creating Meeting Notes pages with a `code` block containing the transcript via `POST /v1/pages` (with `children` array), the Notion API enforces a 2000-char limit on `rich_text[0].text.content` for code blocks. The `PATCH /v1/blocks/{id}/children` endpoint is more tolerant, but the initial page creation endpoint is strict. **Always truncate transcript content in code blocks to ≤ 1900 chars** to leave margin for encoding. Append a note paragraph after the code block indicating truncation happened: `[Transcript truncated — N total chars. Full transcript available in meeting directory.]`. If the full transcript must be available in Notion, append the remainder as a second `code` block (or as a separate call via `PATCH /v1/blocks/{page_id}/children` which has a higher limit of 3000 chars per block).

26. **`.joining` auth-failure artifact bypasses `status.json` gates.** When the bot fails authentication (sign-in redirect), `meeting-auto-join` writes a `.joining` file with `status: "exited"` and `exit_reason: "auth-failed-sign-in-redirect"` but NEVER creates `status.json` or `transcript.json`. The meeting directory exists, there is no active lock, and the auto-join log may or may not have a matching entry. Without a dedicated check, the flow falls through all Step 0 checks to Step 1, where the missing `status.json` produces `meeting-not-complete` — a misleading classification that implies the bot was in the meeting but didn't exit cleanly. The correct classification is `auth-failure`, documented in Step 0a check 1b. If this repeats for the same recurring event across multiple days, the auth issue is persistent and requires VNC tunnel recovery (see `references/joining-file-contract.md`).

27. **Orphaned `AUTH_ALERT.txt` without `.joining` file.** The `.joining` contract (Pitfall #26, `references/joining-file-contract.md`) assumes the `.joining` lock is always written before the bot attempts auth. In practice, the bot may crash before writing `.joining`, or a prior crash-handler cleanup may have removed it, leaving `AUTH_ALERT.txt` as the only signal in the directory. Step 0a case 1c handles this as a fallback with `skip-reason: auth-failure:orphaned-auth-alert`. When this fires, do NOT escalate as a `.joining`-contract bug — it's the safety-net for crash-edge cases. The recovery action is identical to Pitfall #26 (VNC tunnel re-auth), and the tech maintainer (Operator for Example Organization) must be notified via Slack DM regardless of whether `.joining` was present, because the bot cannot attend any meetings until auth is refreshed. To reduce orphaned alerts in the future, ensure `meeting-auto-join` writes `.joining` before the auth attempt (before creating the browser process), not alongside or after. If orphaned `AUTH_ALERT.txt` files accumulate for old meetings, the cleanup cron job in `meeting-auto-join` should remove directories older than 7 days with no `status.json`.

29. **Log appending — NEVER use `write_file` from first bot coexisting with successful second bot's artifacts.** When `meeting-auto-join` retries after an auth failure, the meeting directory can contain artifacts from TWO bot processes: `AUTH_ALERT.txt` (PID A, failed auth) AND a valid `status.json` (PID B, successful join + transcript capture). This was observed 2026-06-16 for Example Organization Daily Standup: PID 546266 wrote `AUTH_ALERT.txt` at 02:59 UTC; PID 546979 was dispatched at ~03:01, joined successfully, and captured 63 transcript lines. The directory had both files with different PIDs.

   **The skill handles this correctly via Step 0a's check ordering** — check 1b (`.joining` sidecar, with `status.json` gate) runs BEFORE check 1c (orphaned `AUTH_ALERT.txt`). Since `status.json` exists with valid data, check 1c is never reached. Logs show `stale-joining-ignored`, not `auth-failure`.

   **What can mislead a human or agent:**
   - Looking at a directory that has both `AUTH_ALERT.txt` and `status.json` with `exited: true, transcriptLines > 0`
   - The `AUTH_ALERT.txt` was written by a DIFFERENT PID than the PID in `status.json`
   - The auth that failed was the FIRST bot; the SECOND bot's auth was successful (or used a different auth path)
   - Conclusion: `AUTH_ALERT.txt` is stale metadata from a prior failed attempt. Ignore it.

   **How to verify:**
   - Check `status.json.pid` — this is the bot that actually captured the transcript
   - Check `AUTH_ALERT.txt` for its reported PID (usually in the subject line on line 6: `Bot PID: <n>`)
   - If PIDs differ, the `AUTH_ALERT.txt` is from a previous attempt and should be ignored
   - Also check `.joining` if present — its `pid` field confirms which bot wrote the sidecar

   **When to escalate:** If `status.json` shows NO valid transcript (`transcriptLines == 0` or `exited: false` with no PID match) AND `AUTH_ALERT.txt` exists with the SAME PID as `status.json.pid`, treat as a real auth failure (the only bot attempt failed). Follow the Step 0c drain-loop recovery path if process is still alive, then Step 0a check 1b/1c otherwise.

31. **Terminal Script Execution Context Failure (Logging Fallback)**. The script `scripts/safe-log-append.py` fails with `ModuleNotFoundError: No module named 'default_api'` when executed via the `terminal` tool because the isolated command execution environment lacks the Hermes API bindings necessary for the script to function.

    **Preferred alternative:** Use `scripts/append-log.py` — a standalone log appender with zero Hermes dependencies. It takes the same two positional args (log path, entry text) and appends with a guaranteed trailing newline:

    ```
    python3 ${HOME}/.hermes/skills/meeting/meeting-finalize/scripts/append-log.py \
      ~/.hermes/cron/output/meeting-finalize.log \
      "2026-06-22T08:15:00Z triage-dm-sent event_id=... action_items=10"
    ```

    **Fallback when both scripts fail (read+write pattern):** Log persistence requires reading the target log file via `read_file`, appending the new entry inline, and overwriting the file via `write_file` (Pitfall #29 workaround). This is a last resort, as it destroys any log history that isn't successfully read first, but it ensures the final run status is recorded.

    **⚠️ CRITICAL — newline separator in read+write fallback:** When using the read+write pattern, you MUST insert a `\n` between the existing content and the new entry. Without it, the new entry concatenates directly onto the last line of the existing log, producing a corrupted entry like:
    ```
    ...event_id=0cpfrgfe0gdosv0g4hhqpiaqr82026-06-22T08:14:17Z triage-dm-sent...
    ```
    This was observed 2026-06-22 for meeting `0cpfrgfe0gdosv0g4hhqpiaqr8`. The correct pattern is:
    ```python
    existing = read_file(log_path)  # returns str with line content
    new_content = existing.rstrip('\n') + '\n' + new_entry + '\n'
    write_file(log_path, new_content)
    ```
    Always ensure the old content ends with `\n` and the new entry has its own trailing `\n`. If you skip this, two entries land on the same line and the log becomes unparseable.

    **Log corruption recovery:** If the log IS corrupted (entries concatenated without newline separators), recover by:
    1. `read_file` the log to get all content
    2. Identify the corrupted boundary (two timestamps on same line)
    3. Insert a newline at the corruption point
    4. `write_file` the cleaned content back
    See `references/log-corruption-recovery.md` for the full procedure and regex pattern.

31b. **`append-log.py` is the canonical logger, not `safe-log-append.py`.** The skill's Output section recommends `safe-log-append.py` from the `meeting-auto-join` skill directory, but that script always fails in this execution environment. The canonical logger is now `scripts/append-log.py` within this skill's own directory. The old pattern (`skill-backup/meeting-auto-join/scripts/safe-log-append.py`) is deprecated — do not use it. Invoke `append-log.py` directly as shown above.

32. **Concurrent webhook sessions with different skill versions produce conflicting outputs (CRITICAL — observed 2026-06-22).** Two webhook triggers within seconds create concurrent sessions that both pass Step 0b before either writes `.finalizing` in Step 3b. If one session was loaded before a skill patch and the other after, they follow different branching logic — one may go triage (old rules) while the other fast-paths (new rules). The result: a triage DM waiting for approval AND a finalization with tasks created, sent to #general, with `.finalized` written. The `.finalizing` lock is useless in this race because neither session had it at Step 0b.

   **Mitigation:** The branching logic MUST be stable within a webhook fire burst. Do NOT patch `meeting-finalize` SKILL.md during hours when meetings are active (Example Organization meeting hours: ~00:00–09:00 UTC). Instead, queue skill patches for the quiet window. If a patch is urgent, disable the webhook subscription first, patch, re-enable.

   **Second line of defense (Pitfall #15):** Step 3b's `.finalizing` lock must be written BEFORE any downstream work. The race window is only between Step 0b and Step 3b — every downstream call reduces the window. Do not add anything between Step 0b and Step 3b.

   **Recovery when it happens:** The `.finalized` page from the fast-path session wins. Archive the triage-created Notion page and any tasks created without approval. Update `triage-context.json` to `superseded`. Log the incident in the meeting-finalize log. See the Sprint Planning `0cpfrgfe0gdosv0g4hhqpiaqr8` incident for the full recovery playbook.

33. **Skill self-modification must not bypass human approval gates (GOLDEN RULE — observed 2026-06-22).** An agent session in this skill's webhook flow has the ability to patch SKILL.md (`skill_manage`). A session MUST NOT use this ability to create or modify branching-logic rules that bypass human approval gates (triage DM, organizer approval, etc.). This includes:
   - Adding a "fast path" or "skip triage" rule for any organizer
   - Rewriting "organizer is NOT Meeting Bot → triage" to "organizer IS in list [Organizer, Meeting Bot] → skip triage"
   - Adding a pitfall that overrides the branching logic
   - Any change that reduces the number of organizers who receive a triage DM

   If the branching logic needs to change, the change must come from a human operator, not from an agent session processing a webhook. The agent's job is to follow the rules and report failures — not to alter the rules to make the pipeline faster.

   **Why this matters:** This was the root cause of the Sprint Planning `0cpfrgfe0gdosv0g4hhqpiaqr8` incident. A prior session added Pitfall #30 ("Meeting Bot/Organizer fast path") via skill_manage, and a later webhook session followed the new rule, creating 12 tasks and a Slack update without any human approval. Organizer's explicit instruction: "There's no fucking fast path. Don't fucking create shits at all unless approved."

34. **Filesystem write-behind caching hides Step 0b markers (CRITICAL — observed 2026-06-23).** GCP persistent disks and similar networked filesystems use write-behind caching: a file written by process A may not be visible to process B's `ls` or `read_file` for up to several seconds (observed: ~19-minute-old files invisible at one `ls`, visible at the next). This defeats Step 0b's entire purpose — the session proceeds as if no markers exist, writes its own `.finalizing`, and only discovers the prior session's artifacts later. The prior session's work is then put at risk of overwrite or duplicate.

   **Prevention:**
   - Before Step 3b's `.finalizing` write, do ONE more directory read (`ls -la <dir>`) as a re-verification. If markers now appear, you caught the cache delay — re-run Step 0b and stop.
   - Keep the window between Step 0b and Step 3b as tight as possible (no API calls, no heavy processing in between).
   - The `.finalizing` lock + `triage-context.json` pair is the most reliable detection signal; a second check before writing catches the cache-delay case.

   **Recovery when it happens:** If you proceed past Step 0b, write `.finalizing`, and THEN discover that `.finalizing` + `triage-context.json` already exist:
   1. Stop ALL downstream work immediately (do NOT create pages, send DMs, or make API calls).
   2. Check the EXISTING `.finalizing` content — if `started_at` is < 30 min old and session was successful (triage-context exists), the prior session is authoritative. Your `.finalizing` write may have overwritten the prior lock — restore the original `started_at` value.
   3. Log `skip-reason: triage-pending-awaiting-approval event_id=<id>` and stop.
   4. Do NOT write `.finalized` — the prior session's triage gate is still active.

   Organizer's explicit instruction: "There's no fucking fast path. Don't fucking create shits at all unless approved."

   34. **`write_file` sibling subagent warning signals concurrent webhook race (observed 2026-06-23).** When `write_file` returns a warning like `"was modified by sibling subagent '<id>'"`, another concurrent agent session is actively working on the same meeting directory at the same instant. This is an empirical diagnostic for the Pitfall #32 race — the `.finalizing` lock was written by a sibling before Step 3b could isolate this session. Immediately re-check the directory state when this warning fires:

      - If `triage-context.json` now exists → sibling completed the triage flow. Log `skip-reason: triage-pending-awaiting-approval` and stop. Do NOT re-send the DM or re-create the Notion page.
      - If `.finalized` now exists → sibling completed everything. Log `skip-reason: already-finalized` and stop.
      - If only `.finalizing` exists (no `triage-context.json`, no `.finalized`) → sibling is still in the workflow between Step 3b and Step 5. Log `skip-reason: finalization-in-progress` and stop. The sibling will complete the triage setup.
      - Do NOT continue with downstream work — the sibling is handling it.

      **Why this race is benign here (unlike Pitfall #32's version-skew scenario):** When both sessions run the same skill version, the sibling worker follows the same branching logic. The worst outcome is a duplicate Notion page (handled by Notion search dedup in `meeting-transcript-to-notion`) or duplicate DM — neither is the destructive task-creation-without-approval issue that Pitfall #32's version-skew race causes.

      **What cannot trigger this:** Pitfall #19b (concurrent same-slot meetings) uses different event_id directories, so `write_file` warnings won't cross. The sibling warning is specific to two sessions targeting the SAME directory.

      **Empirical observation, 2026-06-23** — meeting `SYNTHETIC_EVENT_ID` (ExamplePartner X ExamplePartner - Partnership Call). Concurrent webhook sessions A and B both passed Step 0b within the same second. Session A completed Steps 3b–5 (Notion page, `triage-context.json`). Session B reached Step 3b and received `"was modified by sibling subagent '20260623_100641_43fb57f3'"` from `write_file`. Session B re-checked the directory, found `.finalizing` and `triage-context.json`, correctly identified `triage-pending-awaiting-approval`, and stopped. No duplicate Notion pages or DMs were created.

      **Empirical observation, 2026-07-03** — meeting `1fnj741fipd1f8de2b9985lp2r_20260703T070000Z` (Demo & Backlog Refinement). Concurrent webhook sessions A and B both passed Step 0b within the same second. **Session A wrote `.finalizing` first, then Session B's `write_file` call received the sibling warning on the `.finalizing` write itself** — not on a downstream file like the June 23 case. This is the earliest possible detection point (Step 3b, lock acquisition). Session B re-checked the directory, found only `.finalizing` (no `triage-context.json`, no `.finalized`), correctly identified `finalization-in-progress`, and stopped. Session A completed the triage setup without interference.

      **⚠️ `/tmp` file false positive caveat:** The `write_file` sibling warning can also fire on `/tmp/` scratch files written by stale sibling sessions from days ago (observed: warning on `/tmp/resolve_attendees.py` from sibling `20260701_051848`, 2 days old). This is a write-behind collision on a shared temp-file path, NOT evidence of a concurrent webhook session targeting this meeting. Only trust the sibling warning for files INSIDE the meeting directory (`~/.hermes/workspace/meetings/<event_id>/`). Warnings on `/tmp/`, `${HOME}/.hermes/tmp/`, or other scratch locations should be noted but ignored for concurrent-session detection — the actual pitfall requires checking the meeting directory, not the temp path.

      **⚠️ Sibling ID timestamp staleness heuristic (meeting-directory files):** The sibling subagent ID includes a session-start timestamp prefix (`20260713_064833` = 2026-07-13 06:48:33 UTC). If this timestamp is more than ~5 minutes before the current time, the collision is most likely a stale write-behind from a prior session on the same file path, NOT an active concurrent webhook session. In that case, verify the meeting directory state normally (check for `.finalizing`/`.finalized`/`triage-context.json`). If the directory is clean and your writes succeeded (confirmed by re-reading the file content), the sibling is stale — proceed with the workflow. No additional re-check or escalation needed. Observed 2026-07-13 for meeting `1fnj741fipd1f8de2b9985lp2r_20260710T070000Z` (Demo & Backlog Refinement): sibling `20260713_064833_121cdb35` (26 minutes old) fired warnings on both `/tmp/resolve_attendees.py` and `triage-context.json`, but no concurrent session existed — the directory was clean and all writes were intact.

      **⚠️ Sibling warning on triage-context.json OR meeting-dir files AFTER full completion (observed 2026-07-09):** In the worst-case race, TWO concurrent sessions both pass Step 0b, Step 3b, Step 4 (create Notion page), and Step 5 (write triage-context + send DM) BEFORE either detects the sibling — both `triage-dm-sent` log entries exist in the log. The sibling warning fires on the SECOND session's write to `triage-context.json` (or `.finalizing`, or another meeting-dir file), but by then BOTH sessions have already created full Notion pages and sent DMs. This is distinct from the June 23/July 3 cases where one session stopped before creating artifacts.

      **Recovery (second session — the one that received the sibling warning):**
      1. Check which Notion page was created first (compare `created_time` via `GET /v1/pages/{id}`). The older page is authoritative. If same creation time, pick the one with the transcript attached (more complete).
      2. Archive the duplicate page (`PATCH /v1/pages/{id}` with `{"archived": true}`).
      3. Verify `triage-context.json` on disk — if it points to the duplicate page, update it to point to the authoritative page. If it points to the authoritative page, leave it.
      4. Log the duplicate cleanup in the meeting-finalize log with `duplicate-cleaned=<archived_page_id>` appended to the triage-dm-sent log line.
      5. Do NOT change the `.finalizing` lock or `.finalized` state — the authoritative session's triage gate is still active.
      6. Do NOT re-send the DM — the organizer already received the sibling's DM. One DM is sufficient.

      **Prevention:** This race cannot be fully eliminated at the event level because webhook sessions are independent. Mitigations:
      - Keep the Step 0b→3b window minimal (no API calls, no heavy computation between marker check and lock write)
      - The sibling-warning-based detection (above) is the existing second line; the post-hoc cleanup (this pitfall) is the third line
      - Accept that concurrent webhook sessions at the identical millisecond may produce dual artifacts requiring cleanup — this is better than blocking on an unavailable distributed lock

      **Observations, 2026-07-09** — meeting `33kpfbnc7p7aapl9mjdaa94nhk_20260709T023000Z` (Example Organization Daily Standup): two sessions both completed Steps 3b–5 within the same second. Session A's page (d625, 11 action items) and Session B's page (b917a, 7 action items) both created at `2026-07-09T03:11:00.000Z`. Session A's page had the transcript attached; Session B's did not. Session A's triage-context.json was authoritative (survived write-after-write). Session B's page was archived. The log shows two `triage-dm-sent` entries for the same event_id, one with `duplicate-cleaned=<id>` appended.

36. **Incomplete post-approval file finalization (CRITICAL — observed 2026-07-04).** A prior session completed all Notion page updates in Step 7 (task creation, status, links, name corrections) but skipped items 7–9: updating `triage-context.json` to `approved`, writing `.finalized`, and removing `.finalizing`. The meeting directory was left in `triage-pending` state — `.finalizing` lock present, no `.finalized`, `triage-context.approval_status == "pending"` — despite the Notion page showing complete state. Symptoms:
   - Directory scan shows `.finalizing` + `triage-context.json` exists (normal triage state)
   - But the Notion page has already-completed tasks, status "Approved — tasks created by Meeting Bot", and to_do blocks with links
   - The `.finalizing` lock has been in place for > 30 minutes — not a concurrent session
   - The bot PID in status.json is dead — meeting ended normally

   **Root cause:** The Step 7 agent crashed or was interrupted between item 6 (append links) and item 7 (update triage-context + write `.finalized`). Notion API calls are durable (persist through crash); filesystem markers are not.

   **Recovery (manual session only — NOT for webhook sessions):** Only a user-triggered session (reply in Slack thread, manual finalization request) should attempt this recovery. A webhook session hitting Step 0b must correctly identify `triage-pending-awaiting-approval` and stop — it cannot distinguish a legitimate triage wait from a crashed post-approval session.
   1. Verify page state via `GET /v1/blocks/{page_id}/children` — confirm status paragraph, to_do blocks with correct links, name corrections applied
   2. If page is complete, finalize: update `triage-context.json` (set `approval_status: approved`, add `approved_at`, `approval_source: session-recovery`, `tasks_created`), write `.finalized`, remove `.finalizing`
   3. If page is NOT complete (missing tasks, stale status, broken links), re-run items 3–9 from Step 7
   4. Log: `recovered-stale-finalization event_id=<id> title="..." tasks_created=<count>`

   **Prevention:** Item 6b (verify page state) catches a crash between items 6 and 7 by ensuring page state is committed before the triage-context update. If this pattern repeats, investigate why Step 7 agents crash between items 6 and 7 — the fix is preventing the crash, not teaching the webhook flow to auto-recover.

37. **Verbose Proposed Action Items make the page unreadable (observed 2026-07-07).** Adding description paragraphs and transcript quotes below each proposed action item turns the page into a wall of text that nobody wants to read. Participant A's feedback: "ngày xưa nó viết có hệ thống và rõ ràng hơn" (used to write systematically and clearly) and "h cứ viết dài dài ko muốn đọc luôn" (now writes too long, doesn't want to read it at all).

38. **`.cancelled` marker takes precedence over all other meeting state (observed 2026-07-10).** When a `.cancelled` marker exists in the meeting directory, it overrides every other signal — `status.json.exited`, `transcriptLines`, `.joining` state, bot liveness. The marker means "a human decided this meeting is off." The bot MUST be killed and the session MUST skip. Do NOT:
    - Check whether the transcript is sparse (the meeting never happened, of course it's empty)
    - Attempt drain-loop recovery (the bot is sitting in an empty room; kill it, don't drain it)
    - Try to finalize anything (the meeting was cancelled, not attended)
    - Write `.finalizing` or `.finalized` (the `.cancelled` marker is the terminal state; writing additional markers creates confusion about what happened)

    **How to distinguish from sparse-transcript:** A sparse-transcript failure has NO `.cancelled` marker. The bot was present, the meeting happened, but captioning failed or nobody spoke. A `.cancelled` marker explicitly means "meeting was cancelled before it started" — the transcript would be empty regardless of caption quality. The two must produce different skip reasons (`sparse-transcript` vs `meeting-cancelled`) so the team can distinguish technical failures from organizational decisions.

    **When to write the marker:** Any agent session (webhook, cron, Slack reply) that receives a cancellation signal from a team member. Write `.cancelled` AND kill the bot immediately — do not wait for the meeting-end webhook. The marker survives across sessions and is checked by the end webhook's finalize step.

    **Recovery if `.cancelled` was written in error:** Remove the `.cancelled` file and re-trigger the meeting-end webhook. The meeting will proceed through normal finalization. There is no `.cancelled` → finalize-reversal path by design — the marker is a human decision that should be unwritten by a human.

   **Rule:** Each proposed action item = ONE `bulleted_list_item` block, format `[Assignee] Task description`. No child paragraphs. No transcript quotes inside the page body. No priority labels in the bullet text. The detail lives in `triage-context.json` and the Slack DM — never in the Notion page body.

   **Verification:** Before writing the `.finalizing` lock, verify your Python script's page body has exactly ONE block per action item (no description paragraphs, no transcript refs). If you find yourself adding `f\"  {item['description']}\"` or `f\"  📝 {item['transcript_ref']}\"` as child blocks, stop and remove them. The page is for scanning, not deep reading.

   **Correct example:**
   ```
   "children": [
     {"heading_2": "Proposed Action Items"},
     {"bulleted_list_item": {"rich_text": [{"text": {"content": "[Participant C] Fix UI issues and clarify animation requirements"}}]}},
     {"bulleted_list_item": {"rich_text": [{"text": {"content": "[Operator] Fix Example Organization Strapi issues for frontend integration"}}]}},
     ...
   ]
   ```

39. **Recurring event bot artifacts under Meet slug directory (observed 2026-07-13).** For recurring events where `meeting-auto-join` dispatches a bot to a shared Meet URL, bot artifacts live under the Meet slug directory, not the event-ID directory. The event-ID directory only has `event_meta.json` with `existing_bot_dir: "<slug>"`. Recovery: follow Step 0a-2 to copy artifacts. See `references/recurring-event-slug-artifact-recovery.md`.

40. **Corrupted transcript.json lines (observed 2026-07-16).** The last few lines of `transcript.json` may have trailing garbage after `}` (e.g., `}.'`) or missing newline separators between adjacent JSON objects (two objects concatenated on one line). This is likely caused by the bot's drain loop corrupting the final `_flush()`. Do NOT use `json.loads()` directly on each line. Use the resilient parsing pattern in `references/transcript-corruption-recovery.md`: try `json.loads()` first, then fall back to extracting between first `{` and last `}`. Lines that fail both are unrecoverable — skip them. Re-run the sparse-transcript gate on recovered entries. The transcript is also attached as a raw file to the Notion page, so no data is permanently lost.

40. **Malformed transcript.json (trailing dots, multi-line objects — observed 2026-07-16).** The bot sometimes writes `transcript.json` with two types of corruption: (a) trailing `.` after the closing `}` brace (e.g. `"project again"}.` instead of `"project again"}`), and (b) embedded newlines inside the `text` field value, making single JSON objects span multiple physical lines. Both issues cause `sparse-detect.py` to fail with `Extra data: line 1 column N (char N)` when `json.loads(line)` is called on a corrupted line.

   **Normalization procedure (inline in Step 2 before any quality check):**
   1. Read the raw file content as a string (not line-by-line JSON parse).
   2. Run `content.replace('}.', '}')` to strip trailing dots after closing braces.
   3. Split on `\n`, strip each line, then accumulate lines in a buffer until `json.loads(buffer)` succeeds. This reassembles multi-line objects.
   4. Write the clean entries back to a temp file (e.g. `/tmp/transcript_clean.json`) before running sparse detection.

   **Script-level fix:** `sparse-detect.py` should be updated to handle this internally (see `scripts/sparse-detect.py` — it now uses `json.loads(accumulated_lines)`). If the script is not yet patched, apply the normalization above before calling it.

   **Verification:** After normalization, the line count should be `N` entries (not `N` physical lines). The bot-self utterance pre-filter runs after normalization, so `bot_self_utterances` is counted from the normalized entries, not raw lines.

   **Why this happens:** The bot's `_flush()` writes `json.dumps(entry) + '.'` to the file (a trailing dot per line), and the `text` field from Meet captions can contain embedded newlines. The fix is in the bot's output writer, but the normalizer in this skill protects against regressions.

   See `references/transcript-json-normalization.md` for full implementation example with error modes.

41. **Step 0b→3b race window — Steps 2+3 widen the gap (observed 2026-07-16).** The `.finalizing` lock write (Step 3b) comes AFTER Steps 2–3 (heavy processing), creating a wide race window. Mitigation: write `.finalizing` immediately after Step 0c, before Step 1. If following the written order, do the re-verification check before the lock write. See `references/step-0b-3b-race-window.md`.

42. **Persistent auth-failure re-fires flood Operator with redundant DMs (observed 2026-07-17).** Every webhook fire creates a fresh agent — each sends its own DM. Always check the log for a recent `auth-failure-notified` entry before sending (Step 0a check 1b). Escalation threshold and full details in `references/persistent-auth-failure-dedup.md`.

## Output

Each invocation produces a single per-meeting log line appended to `~/.hermes/cron/output/meeting-finalize.log` with ISO-8601 timestamp. See `references/output-log-format.md` for the full list of log line formats and their fields.

All log lines are appended to `~/.hermes/cron/output/meeting-finalize.log` with ISO‑8601 timestamps. One line per invocation. **⚠️ Append only — NEVER use `write_file`.** The `write_file` tool overwrites the entire file, destroying previous log history. Use `scripts/append-log.py` (canonical, no Hermes API deps):

```
python3 ${HOME}/.hermes/skills/meeting/meeting-finalize/scripts/append-log.py \
  ~/.hermes/cron/output/meeting-finalize.log \
  "2026-06-22T08:15:00Z triage-dm-sent event_id=0cpfrgfe0gdosv0g4hhqpiaqr8 title=\"Sprint Planning\" organizer=person@example.org action_items=10"
```

The script takes two positional args (log path, entry text) and guarantees a trailing newline. Exit code 0 on success.

**Legacy (deprecated):** The old `safe-log-append.py` from `meeting-auto-join` always fails with `ModuleNotFoundError` in this execution environment. Do not use it. See Pitfall #31/31b.

**Last resort — read+write fallback with newline guard:** If `append-log.py` also fails, use `read_file` to get existing content, then `write_file` with the NEWLINE separator added between old and new content. See Pitfall #31 for the exact pattern and the corruption warning.

**Notion ID dependency on direct path (Step 5/4).** If attendee ID resolution (Step 2/3) fails or is unavailable, any attempt to create the Meeting Notes page with a non-empty `Attendees` field results in `HTTP 400 validation_error` because the API requires resolved Notion UUIDs (`id`). When this occurs, the workaround is to OMIT the `Attendees` field entirely during initial page creation to allow the process to continue to task creation (Step 7). This results in incomplete page metadata but allows finalization to proceed. Tasks created in Step 7 must also omit the 'Assignee' field if IDs are unknown.

- `references/action-item-extraction.md` — Extract proposed action items from meeting transcripts: collapse same-speaker turns, topic scanning, owner assignment, source-line references, priority heuristics. Written for Step 4's Proposed Action Items section in the Notion page body. (`status`, `skip_reason`) are written at dispatch time and never updated after successful join; verification pattern to distinguish stale metadata from real outcomes
- `references/sparse-transcript-detection.md` — Thresholds and algorithms for flagging minimal/empty transcripts
- `references/transcript-corruption-recovery.md` — Resilient JSON-line parsing for corrupted transcript files (extra trailing chars, missing newline separators). Observed 2026-07-16.
- `references/webhook-finalize-flow.md` — Complete webhook architecture and event payload shapes
- `references/drain-loop-recovery.md` — Bot drain-loop debugging and SIGTERM recovery procedure
- `references/notion-api-rich-text-gotchas.md` — Notion API validation edge cases (rich_text, boolean handling)
- `references/slack-triage-gate-dm-ordering.md` — Prevent #general leakage before organizer DM in triage-gate flows
- `references/duplicate-finalization-pattern.md` — June 9 incident, idempotency, diagnostic steps
- `references/vietnamese-caption-quality.md` — Meet caption failures with Vietnamese speech, short-utterance-density heuristic
- `references/write-file-env-var-sanitization.md` — write_file corrupts `KEY=` patterns in scripts; safe parsing patterns
- `references/triage-dm-recipient-investigation.md` — Playbook for investigating reports that a non-organizer attendee received a triage DM; how to trace the actual source using session artifacts
- `references/mock-transcript-injection.md` — Manual mock transcript injection for testing the finalize pipeline without real Meet calls
- `references/organizer-slack-resolution.md` — Resolve webhook organizer email to Slack DM channel when roster email doesn't match; Notion name-based matching and fallback chain
- `references/joining-file-contract.md` — The `.joining` sidecar lock contract: file format, states (booting, auth-failure, other exit reasons), relationship to status.json/AUTH_ALERT.txt, recovery procedures for each state
- `references/triage-context-schema.md` — Full JSON schema for `triage-context.json` (written in Step 5, read in Step 7 + retrieve-meeting-flow-context). Includes `test_session` flag, field lifecycle, and required/optional field matrix.
- `references/log-corruption-recovery.md` — Recovery procedure for the meeting-finalize log when entries get concatenated (newline missing in read+write fallback). Regex fix pattern and verification steps.
- `references/orphan-triage-context-crash-pattern.md` — Prior session crash after triage-context write but before .finalizing lock. How to detect, why the current re-run-from-scratch is correct, and cleanup guidance. Observed 2026-06-22 on Sprint Planning.
- `references/post-hoc-task-sync.md` — Post-meeting pattern: user created tasks manually and wants descriptions backfilled from the meeting triage context. Diff proposed items against Notion, update empty-body tasks, report gaps. Triggered by "check notion for diffs" style instructions.

## Templates and Scripts

| File | Purpose |
|---|---|
| `templates/daily-standup-transcript.json` | Generic Organizer/Participant A/Operator standup (9 lines). Inject with `scripts/inject-transcript.py`. |
| `templates/caption-test-transcript.json` | Meeting Bot+Operator caption pipeline test (13 lines). Covers observer, speaker labels, timestamps, triage race condition. |
| `templates/test-session-notion-page.json` | Notion page body `children` array for caption test sessions (Pitfall #21). No action items section. |
| `scripts/inject-transcript.py` | Inject transcript template into a meeting directory. Auto-reads event_meta.json for attendees. Patches status.json. Supports `--dry-read`, `--start`, `--no-patch-status`. |
| `scripts/append-log.py` | **Canonical log appender.** Zero Hermes API deps — standalone Python, takes (log_path, entry_text), appends with guaranteed trailing newline. Replaces broken `safe-log-append.py`. See Pitfall #31/31b. |
| `scripts/safe-log-append.py` | **Deprecated.** Always fails. Use `append-log.py`. |
| `references/host-denied-gate-artifact.md` | Stale guest-access denial marker `HOST_DENIED_GATE.txt`. |
| **`references/missing-template-files-guide.md`** | **Details the purpose and expected location of critical template/script files crucial for the skill's robustness and testing.** |

Example Organization LLC internal tool. Use within crew scope. No external sharing.
