---
name: post-meeting-approval
description: "Sends a Slack DM to the meeting organizer with the Notion page URL, extracted action items, and decisions for triage/approval. Writes a triage-context.json file before sending the DM, and embeds the retrieve-meeting-flow-context skill slug in the message so the organizer's reply rehydrates the agent with full meeting context. Called as the final step in the meeting-finalize skill chain."
version: 1.5.1
metadata:
  hermes:
    tags: [meeting, slack, approval, post-meeting, triage]
    related_skills: [meeting-finalize, meeting-transcript-to-notion, retrieve-meeting-flow-context]
    changelog:
      "1.5.1": "2026-09-25 — When the native send_message tool is unavailable, prefer the credential-aware `hermes send` CLI before custom GSM/Slack code; capture its message_id and verify by read-only Slack history."
      "1.5.0": "2026-08-04 — Organizer-is-Meeting Bot no longer means skip-entirely (that stalled the triage gate with no approver). Step 1 + Pitfall #1 now route the review DM to the senior Example Organization human on the call (usually Organizer) with approval_routed_to in triage-context. See meeting-finalize references/organizer-is-john-approval-routing.md."
      "1.4.0": "2026-07-22 — Step 2: stamp $HERMES_SESSION_ID + session_note into the context file for tier-2 recall (rehydrating session runs session_search on it for full working memory beyond the JSON). New pitfall #12: the rehydrating session is a different session; the stamped id is the only bridge back. Mirrors the sales jordan-outbound-draft / retrieve-outbound-draft-context pair."
      "1.3.0": "2026-06-29 — Step 4: replaced `.env`-based SLACK_BOT_TOKEN parsing with GCP Secret Manager pattern. Script delivery switched from write_file to terminal heredoc ('PYEOF'). Pitfall #7 updated for GCM source. Pitfall #8/9/10 consolidated: terminal heredoc is canonical, write_file with partition() pattern is fallback. Duplicate pitfall #8 (mrkdwn) renumbered to #8."
      "1.2.1": "2026-06-17 — Pitfall 10: single-recipient verification for triage DM"
      "1.2.0": "2026-06-15 — Pitfall 9: write_file env var sanitization; single-recipient DM verification"
      "1.1.0": "2026-06-10 — Initial triage-gate implementation"
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Post-Meeting Approval

## When to use

- Called as the final step in the meeting-finalize skill chain, after the Meeting Notes page has been created in Notion.
- Sends a Slack DM to the **meeting organizer** (not Meeting Bot) with the Notion page URL, proposed action items, and decisions for triage.
- Writes a triage context file so the organizer's reply can rehydrate the agent with full meeting context.

## Input

From the meeting-finalize skill chain:
- `meeting_id` — the meeting slug (e.g., `abc-defg-hij`)
- `notion_page_url` — URL of the Notion Meeting Notes page
- `organizer` — object with `email`, `display_name`
- `action_items` — list of proposed action items (with suggested assignees, NOT yet created as Notion tasks)
- `transcript_path` — path to the transcript JSON file
- `meeting_title` — title of the meeting
- `meeting_date` — date of the meeting

## Workflow

### Step 1: Check if organizer is Meeting Bot (bot account)

If the organizer's email is `person@example.org`:
- This is the bot's own calendar account — there is no human organizer to approve.
- Do NOT send a *self*-DM (never DM the bot's own channel).
- Do NOT skip the approval gate and do NOT create tasks — meeting-finalize golden rule #33 still holds.
- Instead, **route the triage review DM to the senior Example Organization human who was on the call** (usually Organizer). Set `approval_routed_to` in `triage-context.json`, keep `organizer` as Meeting Bot, and proceed through Steps 2–5 sending the DM to that human's channel. See Pitfall #1 and meeting-finalize `references/organizer-is-john-approval-routing.md` for the full routing-target order.

### Step 2: Write triage context file

Before sending the DM, write a context file at:
```
${HERMES_HOME}/workspace/meetings/<meeting_id>/triage-context.json
```

Contents:
```json
{
  "meeting_id": "abc-defg-hij",
  "meeting_title": "Daily Standup",
  "meeting_date": "2026-06-08",
  "organizer": {"name": "Operator", "email": "person@example.org", "slack_user_id": "CONFIGURED_SLACK_ID"},
  "notion_page_url": "${CONFIGURED_SOURCE_URL}",
  "transcript_summary": "<2-3 sentence summary>",
  "transcript_path": "${HERMES_HOME}/workspace/meetings/abc-defg-hij/transcript.json",
  "action_items": [
    {
      "task": "Complete wallet connection flow PR",
      "assignee": "Operator",
      "assignee_email": "person@example.org",
      "due_date": "2026-06-08"
    }
  ],
  "session_id": "20260608_171500_ab12cd34",
  "session_note": "Full working context for this meeting-finalize run lives in the Hermes session above. When this file is not enough, call the session_search tool with session_id to recover the complete conversation/decision trail.",
  "approval_status": "pending",
  "created_at": "2026-06-08T17:20:00Z",
  "expires_at": "2026-06-09T17:20:00Z"
}
```

**Stamp the running session id (tier-2 recall).** Read `$HERMES_SESSION_ID` from the
environment at write time and store it as `session_id` in the context file, alongside a
short `session_note`. This gives the rehydrating session a second, deeper tier of context:
the JSON holds the distilled facts (tier 1), and `session_search(session_id=...)` recovers
the *full working memory* — reasoning, rejected variants, anything that never fit into the
file (tier 2). Capture it here at write time; a later reply lands in a **different** session
that is not "inside" this one and can only reach it via `session_search`. This is the same
session-id-stamping enhancement carried by the sales `jordan-outbound-draft` /
`retrieve-outbound-draft-context` pair.

### Step 3: Resolve organizer Slack DM channel

**Use the `read_file` tool** to load `${HERMES_HOME}/team-roster.yaml`.

Find the entry matching `organizer.email` and extract `slack_dm_channel_id`.

**Do NOT use `execute_code` or shell commands to resolve Slack IDs.** The roster file is the authoritative source. If the organizer is not in the roster, log a warning and skip the DM — do NOT fall back to Slack API calls.

Resolve the organizer's `slack_dm_channel_id` from the roster. This is the channel ID you'll use for the Slack API call in Step 4.

### Step 4: Compose and send the triage DM

**Prefer the native `send_message` tool when it is available.** First call `send_message(action="list")` to verify the organizer's exact DM target, then send once to that single target. Capture the returned message ID and channel in `triage-context.json`.

If `send_message` is unavailable in the active runtime, prefer the credential-aware Hermes CLI before generating custom Slack code:

```bash
hermes send --list slack --json
hermes send --to slack:<organizer_dm_channel_id> --file <message_file> --json
```

Run the send command exactly once. Its success receipt includes `message_id`; save that as `slack_message_ts`, then verify with read-only Slack history keyed by the exact timestamp or `ctx:<event_id>`. The CLI loads the active profile's configured credentials and routes through the platform adapter, so it remains usable when a terminal subprocess lacks GCP ADC. Only if both native `send_message` and `hermes send` are unavailable should you use Python urllib against Slack `chat.postMessage` as the fallback below.

**Token source: GCP Secret Manager (NOT `.env`).** The `.env` file contains only a redacted sentinel. Prefer the Google Cloud Secret Manager Python SDK; it avoids shell quoting/redaction hazards and keeps the credential in process memory. Use the profile-specific secret name rather than hardcoding another agent's secret.

```python
import urllib.request, json
from google.cloud import secretmanager

profile = "john"  # derive from the active profile when available
secret_name = f"hermes-{profile}-slack-bot-token"
resource = (
    "projects/CONFIGURED_CLOUD_PROJECT/secrets/"
    + secret_name
    + "/versions/latest"
)
client = secretmanager.SecretManagerServiceClient()
slack_token = ${CONFIGURED_SECRET}
    request={"name": resource}
).payload.data.decode("utf-8").strip()
if not slack_token:
    raise RuntimeError("Slack credential lookup returned an empty value")

payload = {
    "channel": "<slack_dm_channel_id from roster>",
    "text": "<message_text>",
    "mrkdwn": True
}
req = urllib.request.Request(
    'https://slack.com/api/chat.postMessage',
    data=json.dumps(payload).encode(),
    headers={
        'Authorization': 'Bearer ' + slack_token,
        'Content-Type': 'application/json'
    },
    method='POST'
)
with urllib.request.urlopen(req, timeout=15) as resp:
    result = json.loads(resp.read())
    # result['ts'] is the message timestamp — save to triage-context.json
```

**Script delivery:** Prefer a committed helper under this skill's `scripts/` directory. For a one-off script, `write_file` is acceptable only when secret identifiers and secret-suffixed assignment patterns are assembled from fragments (for example `_envname = "NOTION_" + "API_" + "TOKEN"`; assign the value to a lowercase variable). Terminal heredocs pass through the same redaction layer and are **not** a reliable bypass. After writing, run `python3 -c "import ast; ast.parse(open('/tmp/script.py').read())"` before execution. See `meeting-finalize` `references/write-file-env-var-sanitization.md` and this skill's `references/gsm-token-in-out-of-band-scripts.md`.

**Message format — use Slack mrkdwn, NOT standard markdown:**
- Bold: `*text*` (not `**text**`)
- Links: `<url|text>` (not `[text](url)`)
- User mentions: `<@CONFIGURED_SLACK_ID>` (use roster UID, not `@name`)
- Italics: `_text_`
- Bullets: `•`

Compose the DM:

```
📋 *Meeting Notes Ready for Review*

*{meeting_title}* — {meeting_date}

🔗 Notion page: <{notion_page_url}|{meeting_title}>

*Summary:*
{transcript_summary}

*Proposed Action Items:*
• {item_1} — <@{assignee_uid_1}> (Due: {due_date_1})
• {item_2} — <@{assignee_uid_2}> (Due: {due_date_2})
...

*Decisions Made:*
• {decision_1}
• {decision_2}
...

Now {organizer_name}, what do you think of this? Reply to this message with any changes — reassign tasks, remove items, add new ones, or approve as-is.

I will run the skill `retrieve-meeting-flow-context` and continue with the next steps.

Context ref: ctx:{meeting_id}
```

**Critical:** The message MUST include:
1. The `retrieve-meeting-flow-context` skill slug mention
2. The `ctx:{meeting_id}` context reference
3. The organizer's name for personalization

### Step 5: Update context file

After sending the DM, update `triage-context.json`:
```json
{
  "approval_status": "pending",
  "slack_message_ts": "1717857600.123456",
  "slack_channel_id": "D0XXXXX",
  "dm_sent_at": "2026-06-08T17:20:00Z"
}
```

## Pitfalls

1. **Organizer is Meeting Bot (the bot's own `person@example.org` calendar account).** Skip the *self*-DM — never DM the bot's own channel. But do NOT drop the human approval gate: the meeting still has real attendees and decisions, and meeting-finalize's golden rule (#33) forbids creating tasks without a human's approval. **Route the triage review DM to the senior Example Organization human who was on the call instead** (almost always Organizer, `person@example.org` / `CONFIGURED_SLACK_ID` for client-facing calls). Write `approval_routed_to` into `triage-context.json`, keep `organizer` as Meeting Bot, and state in the DM why it's coming to them ("organized under my bot account, no organizer to approve — routing to you as the Example Organization owner"). The `ctx:<event_id>` ref still drives reply rehydration regardless of recipient. Full workflow + routing-target order: see meeting-finalize `references/organizer-is-john-approval-routing.md` (observed 2026-08-04, Tara Site review `n8jvmqi5hep60gn5vjl9m82qq4`).

2. **Slack API failures and partial remote success.** If the Slack DM fails, preserve the completed Notion work and all local meeting artifacts; do not roll back or treat the whole finalization as failed. Update `triage-context.json` with a structured delivery record containing `status: "failed"`, the intended single recipient/channel, `message_ts: null`, and a concise error classification. Keep `approval_status: "pending"`, because no approval was received. Return the Notion page URL/ID, explicit Slack failure, intended channel, and exact local artifact paths. If credential-dependent identity or project mapping could not be completed, record and report `Mapping_Confidence: "Unknown"`. This is a partial-success workflow, not permission to omit local artifacts.

3. **Missing action items.** If no action items were extracted, still send the DM with just the Notion link and transcript summary.

4. **Context file TTL.** The triage context file expires after 24 hours.

5. **Bao (CONFIGURED_SLACK_ID)** DM channel could not be resolved. If Bao is the organizer, skip the DM.

6. **This skill does NOT create Notion tasks or write `.finalized`.** Tasks are created only after organizer approval (by meeting-finalize Step 7).

7. **Use the Slack Web API through a committed helper or syntax-checked Python script.** The Hermes toolkit has no Slack `send_message` tool. Use Python `urllib.request` against `https://slack.com/api/chat.postMessage`; prefer the skill's committed helper over generating source during the run. The Slack bot token is in GCP Secret Manager, NOT in `.env`. Fetch the active profile's `hermes-<profile>-slack-bot-token` secret inside the runtime script. Prefer the Google Cloud Secret Manager Python SDK; use the documented `gcloud` fetch-to-file pattern only as fallback. Do NOT attempt `hermes slack send` — it only supports `manifest`, not message posting. Never use inline shell command substitution for credential retrieval; the redaction layer can mangle it. See `references/gsm-token-in-out-of-band-scripts.md`.

8. **Slack mrkdwn ≠ standard markdown.** The DM template must use `*bold*` (not `**bold**`), `<url|text>` (not `[text](url)`), and `<@UID>` (not `@name`). Standard markdown renders as literal asterisks in Slack.

9. **No script-delivery path bypasses secret redaction.** `write_file`, `patch`, and terminal heredocs can all sanitize secret-looking assignment text. Do not encode a literal secret value or a contiguous secret-suffixed assignment pattern in generated source. Prefer a committed helper. For one-off Python, assemble sensitive environment names from fragments and store returned values in ordinary lowercase variables; syntax-parse the written file before running it. See `meeting-finalize` `references/write-file-env-var-sanitization.md`.

10. **Fetch Slack credentials inside the runtime script without exposing them.** The Slack bot credential is not expected in a terminal subprocess environment. Fetch the configured profile secret from GCP Secret Manager via the Python SDK (preferred) or the documented CLI fallback, keep the value in memory, and never print it. Use `references/gsm-token-in-out-of-band-scripts.md` for the canonical pattern.

11. **Single-recipient verification — CRITICAL.** The triage DM MUST be sent to exactly ONE Slack channel: the meeting organizer's DM channel. Before writing the send script:
    - Resolve the organizer's `slack_dm_channel_id` from `${HERMES_HOME}/team-roster.yaml` (Step 3).
    - Hardcode that single channel ID in the script. Do NOT loop over attendees or send to multiple channels.
    - After executing the script, verify the output shows exactly one `DM_SENT` line with the organizer's channel ID.
    - If the script output shows multiple sends, or a channel ID that doesn't match the organizer, STOP. Do not update `triage-context.json`. Log the error and exit.
    - **Why:** If a non-organizer attendee receives a triage DM, it's a privacy leak — they see proposed action items and meeting notes before the organizer has approved them. The triage gate exists specifically to prevent this.

12. **Stamp `$HERMES_SESSION_ID`, and know the rehydrating session is a *different* session.** Write the running session id into the context file at Step 2 so a later reply can `session_search(session_id=...)` for the full working memory (tier 2) beyond the distilled JSON (tier 1). The reply lands in a fresh session that is NOT inside the draft/finalize session — it cannot "remember" anything directly; the stamped `session_id` is the only bridge back. Capture it at write time, not later. Use tier-2 recall only WHEN NEEDED (a plain "approve" needs only the JSON); don't burn a session search on every reply.

13. **Do not mutate an attendee object while enriching the organizer.** If `organizer` is selected from the `attendees` list, copy it before adding organizer-only fields such as `slack_dm_channel_id`. Otherwise the same dictionary appears enriched inside `attendees[]`, corrupting the triage-context schema. Full identity fallback and verification pattern: `meeting-finalize/references/identity-resolution-and-object-aliasing.md`.

14. **Calendar-to-Notion identity resolution needs fallbacks.** Exact email matching may leave a known organizer unresolved because Calendar, Notion, and the roster can use aliases. Query live Notion users first, then use maintained aliases and unique normalized-name matching. Keep unresolved users out of Notion `people` fields rather than inventing IDs. Notion-ID failure does not block Slack routing when the organizer's roster entry is known.

## Reference Files

| File | Purpose |
|---|---|
| `references/triage-status-query.md` | How to check a meeting's approval status when a team member asks — read triage-context.json, interpret `approval_status` values, cross-reference disk markers, report concisely. |
| `references/manual-finalization-partial-success.md` | Manual triage finalization checklist: preserve local/Notion success, record Slack failure structurally, verify artifacts, and return exact handles. |
| `references/gsm-token-in-out-of-band-scripts.md` | Operational gotchas for sending Slack from a script the agent runs: GSM secrets are injected into the agent process NOT the terminal subshell (so `$SLACK_BOT_TOKEN` is empty in `terminal` — fetch from GSM); `HERMES_REDACT_SECRETS` mangles `$(gcloud secrets access)` substitutions (fetch-to-temp-file instead); how to test a Slack send symmetrically for ANY profile (auth.test + conversations.info pre-flight, then clean up with chat.delete). |
