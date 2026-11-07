---
name: meeting-transcript-to-notion
description: Full workflow to convert meeting transcripts to Notion notes with action items as Notion tasks, plus Slack updates. Use when the user says "save meeting to Notion", "transcript to Notion", "meeting notes to Notion and Slack", or wants to process a meeting transcript (transcript → Notion page → Notion tasks → Slack update).
version: 1.5.0
metadata:
  hermes:
    tags: [meeting, transcript, notion, slack]
    category: meeting
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Meeting Transcript to Notion

**Transcript → Notion Meeting Notes page → Notion Tasks for action items → Slack update.**

## When to use

- User says "save meeting to Notion", "transcript to Notion", "meeting notes to Notion and Slack"
- Meeting transcript in context (webhook payload, pasted text, uploaded file)
- Triggered by webhook/meeting-transcript or manual processing

## Database Quick Reference

See `references/notion-schema.md` for full details.

### Example Organization Meeting Notes DB
| Field | Value |
|---|---|
| database_id | `${CONFIGURED_NOTION_ID}` |
| data_source_id | `${CONFIGURED_NOTION_ID}` |

**Properties:** Meeting name (title), Date (date), Category (multi_select: Planning/Standup/Presentation/Retro/Customer call/Daily Standup), Attendees (people), Summary (rich_text)

### Notion Tasks DB
| Field | Value |
|---|---|
| data_source_id | `${CONFIGURED_NOTION_ID}` |
| database_id | `${CONFIGURED_NOTION_ID}` |

**Key properties:** Task name (title), Assignee (people — resolve via `/v1/users`), Status (status: Not Started/In Progress/Review/Done/Archived), Priority (select: Low/Med/High), Due (date), Project (relation → Projects DB), Tags (multi_select)

### Notion Projects DB
| Field | Value |
|---|---|
| data_source_id | `${CONFIGURED_NOTION_ID}` |
| database_id | `${CONFIGURED_NOTION_ID}` |

**Key properties:** Project name (title), Client (rich_text, free-text), Status (status), Priority (select), Owner (people), Dates (date)

## Prerequisites

1. **Notion integration**: `NOTION_API_TOKEN` in `~/.hermes/.env` (also available via `os.environ["NOTION_API_TOKEN"]` in the agent runtime), integration shared with Meeting Notes DB, Tasks DB, and Projects DB
2. **Slack delivery**: Hermes gateway connected to Slack, `send_message` tool available

## Transcript File Format

`transcript.json` uses **JSON Lines** format — one JSON object per line, NOT a JSON array.

```json
{"ts": "2026-06-11T04:35:12", "speaker": "Operator", "text": "Alright, let's get started."}
{"ts": "2026-06-11T04:35:18", "speaker": "Meeting Bot", "text": "Yeah, I'm here."}
```

**⚠️ CRITICAL — multi-line JSON objects:** The `transcript.json` file is JSON-lines-like but NOT guaranteed to be one-object-per-line. When a speaker's `text` field contains an embedded newline (e.g., a multi-sentence technical update that wraps across file lines), the JSON object spans multiple lines. Do NOT use `json.loads(line)` per line — it silently skips multi-line objects. Use **brace-depth tracking** instead:

```python
import json
with open(transcript_path) as f:
    raw = f.read()
lines = [l.strip() for l in raw.split('\n') if l.strip()]
entries = []
buf = ''
depth = 0
in_string = False
escaped = False
for line in lines:
    if not buf and not line.startswith('{'):
        continue
    buf += line
    for ch in line:
        if escaped:
            escaped = False; continue
        if ch == '\\' and in_string:
            escaped = True; continue
        if ch == '"' and not escaped:
            in_string = not in_string; continue
        if not in_string:
            if ch == '{': depth += 1
            elif ch == '}': depth -= 1
    if depth == 0 and buf.startswith('{') and buf.endswith('}'):
        entries.append(json.loads(buf))
        buf = ''
        depth = 0; in_string = False; escaped = False
```

The canonical brace-depth parser is in `meeting-finalize/scripts/sparse-detect.py`.

**Do NOT use `json.load()`** on the whole file — it will fail with `JSONDecodeError: Extra data` because the file is not a single JSON value.

**Caveat:** If the bot is still running, `status.json`'s `transcriptLines` field may not reflect injected/missing lines. Count file lines directly if accuracy matters.

## Workflow

**⚠️ CRITICAL: Use the `write_file` + `terminal python3` pattern for ALL Notion API calls. Do NOT use raw `curl` commands or the `ntn` CLI — the Hermes approval system blocks commands containing API tokens, and `ntn` frequently times out. The working pattern (see pitfall #1b): write a Python script to `/tmp/` using the `write_file` tool, then execute it with `terminal` (`python3 /tmp/script.py`). This reads `NOTION_API_TOKEN` from the environment and consistently completes in <1s.**

### Step 1: Parse transcript

Extract from the input:
- Meeting title
- Date/time (use current time if not explicit)
- Attendees/participants (match against @example.org for internal)
- Raw transcript content (speaker turns, timestamps if available)
- Meeting description/agenda (if available)

### Step 2: Summarize and structure

**Output-language contract:** Meeting notes are always written in English, even when the source
transcript is Vietnamese or another language. Translate the meaning into concise English for the
Summary property, discussion topics, decisions, blockers, and proposed action items. Preserve
proper nouns, product names, ticket IDs, URLs, numbers, and quoted commitments exactly. Do not
translate or replace the attached readable/canonical transcript: the attachment remains in the
language actually spoken and is the provenance source for the English notes.

Generate a structured summary:
- **Discussion themes** (3–7 topics)
- **Notes per topic** (1–3 bullets each, 1–2 sentences)
- **Action items** — concrete follow-ups with: task description, owner name/email, due date (if known), project (if identifiable)
- **Decisions made**

### Step 3: Resolve attendees → Notion user IDs

**CRITICAL: Use the `write_file` + `terminal python3` pattern (see Pitfall #1b) to call the Notion API. Do NOT use `ntn` CLI (frequent timeouts) or raw `curl` (approval system blocks API tokens).**

Call `GET /v1/users` via a Python urllib script. For each attendee email, find the match by `person.email`:

```
email → { id: "notion-user-uuid", name: "Display Name" }
```

Attendees without Notion accounts can't be set as Assignee. If no match, ask before creating tasks for that person.

**Email ≠ Notion email:** A person's email in meeting invites/calendar may differ from their Notion account email. Example: Organizer is `person@example.org` in calendar invites but `person@example.org` in Notion. Always resolve via `GET /v1/users` and match `person.email` — never assume the calendar email matches the Notion email. If the attendee has multiple Notion accounts, pick the one that matches the meeting context.

### Step 4: Detect project context

From meeting title, transcript content, and attendees:

1. Search Projects DB by meeting keywords (client name, project codename)
2. Clear match → note the Project page ID for task relations
3. Ambiguous → ask the user
4. No project → tasks created without Project relation

### Step 5: Create Meeting Notes page

Create a page in the Example Organization Meeting Notes DB:

```json
{
  "parent": { "database_id": "${CONFIGURED_NOTION_ID}" },
  "properties": {
    "Meeting name": { "title": [{ "text": { "content": "<title>" } }] },
    "Date": { "date": { "start": "YYYY-MM-DD" } },
    "Category": { "multi_select": [{ "name": "<category>" }] },
    "Attendees": { "people": [{ "id": "<notion-user-id>" }, ...] },
    "Summary": { "rich_text": [{ "text": { "content": "<brief summary>" } }] }
  },
  "markdown": "<structured notes>"
}
```

4. **Multi-line markdown.** Write to a temp file, use `ntn api "v1/pages/{page_id}" -X PATCH -d @/tmp/notes.json`. ntn inline breaks on newlines.

**Page content:**
```
## Agenda
- ...

## Discussion Topics
### [Topic]
- ...

## Decisions
- [Decision]

## Action Items
- [ ] [Task] — **Owner:** [Name] — **Due:** [Date]

## Blockers
- ... or "None"

## Tasks Created
<!-- Filled in Step 7 -->
```

Capture the returned Notion page URL.

### Step 5b: Transform and attach transcript to page

After creating the meeting notes page, transform the raw JSON Lines transcript into a readable text file and upload it as a native file attachment on the page. The file appears as a downloadable file block in Notion — click to open or download.

**Use the helper scripts in `scripts/`:**

1. **Transform** — `scripts/transcript-to-readable.py` converts the raw transcript to a clean format with normalized speaker names, localized timestamps, and line breaks between speaker changes.

   ```bash
   python3 /path/to/skills/meeting/meeting-transcript-to-notion/scripts/transcript-to-readable.py \
     transcript.json \
     --title "Meeting Title" \
     --timezone Asia/Bangkok \
     --roster ~/.hermes/team-roster.yaml
   ```

   Output is written to `transcript.json.readable.txt` by default.

2. **Attach** — `scripts/attach-transcript-to-notion.py` does both steps (transform + upload + attach to Notion page):

   ```bash
   python3 /path/to/skills/meeting/meeting-transcript-to-notion/scripts/attach-transcript-to-notion.py \
     transcript.json \
     <notion_page_id> \
     --title "Meeting Title" \
     --roster ~/.hermes/team-roster.yaml
   ```

   The script:
   - Calls `transcript-to-readable.py` internally to transform the raw transcript
   - Creates a file upload via `POST /v1/file_uploads (mode: single_part)`
   - Uploads the file content via `POST /v1/file_uploads/{id}/send` (multipart/form-data)
   - Appends a file block to the page via `PATCH /v1/blocks/{page_id}/children` referencing the uploaded file

3. **From the main workflow**, after the page is created in Step 5 and you have the `page_id`:
   - Use `write_file` + `terminal python3` pattern to call `attach-transcript-to-notion.py`
   - Verify the output JSON indicates success (`"status": "transcript-attached-as-file"`)
   - Log the file_upload_id and file_name

**Edge cases:**
- **Empty transcript (0 lines).** The transformation script exits with error. The attachment script posts a visible yellow callout warning block on the Notion page: "⚠️ Transcript not available — capture failed or bot did not join this meeting." Log the event but do NOT block Steps 6–10.
- **Transcript file missing entirely.** Same behavior as empty transcript — the missing-file path posts the yellow callout warning block on the page, logs `TRANSCRIPT_MISSING` to stderr, and exits with status `"transcript-missing-warning-posted"`. The notes page always carries a visible indicator that something went wrong.
- **Sparse transcript (< 5 lines).** Still attach it as a file; the readable format is still useful for review.
- **Bot-self utterances.** The script automatically filters out `"You"` speaker labels (bot-self mic noise).
- **Page not created yet.** Never call this step without a valid `page_id` from Step 5. The page must exist first.
- **Speaker normalization.** The script has a built-in roster mapping (Operator Nguyen → Operator, Jirrattakarn Chantapakawin → Participant D, etc.), extended from `team-roster.yaml`. This is the same normalization described in Pitfall #11.

**Verification:** After the script returns, check the output JSON. A key shape for a successful upload:
```json
{"success": true, "file_name": "transcript-example-daily-standup.txt",
 "file_upload_id": "${CONFIGURED_NOTION_ID}",
 "blocks_appended": 1, "status": "transcript-attached-as-file"}
```

For a missing/empty transcript:
```json
{"success": false, "status": "transcript-missing-warning-posted",
 "blocks_appended": 1}
```

If `success` is false, the warning block was placed on the page — the failure is surfaced point-of-use. Log the status and proceed (Steps 6–10 continue; the missing transcript does not block task creation or Slack delivery).

### Step 5c: Back up the full transcript to the shared vault

Per Organizer's OQ-2 ruling (2026-07-26): every meeting transcription goes to Notion (system of record) **and** a full backup copy into the shared memory vault (`EXAMPLE_ORG/agent-memory`). After Step 5b (transcript transformed + attached to Notion), also run:

```bash
python3 ${HOME}/.hermes/skills/meeting/meeting-transcript-to-notion/scripts/backup-transcript-to-vault.py \
  <transcript>.json.readable.txt \
  --title "Meeting Title" \
  --date YYYY-MM-DD \
  --notion-url "<notion_page_url>"
```

- Writes `$OBSIDIAN_VAULT_PATH/raw/transcripts/<date>-<slug>.md` (frontmatter + link back to the Notion note + the full readable transcript) and appends a line to the vault `log.md`. **Idempotent** — re-runs on the same content report `already-backed-up`.
- If a **different** meeting collides on the same `<date>-<slug>` (same day + same title, different content), the backup is written to `<date>-<slug>-<sha8>.md` instead of overwriting the existing one — no transcript is ever silently clobbered.
- Uses the readable transcript produced in Step 5b (`.readable.txt`). If it is missing or empty the script **skips the backup gracefully** (`transcript-missing-skip-backup` / `transcript-empty-skip-backup`) — do NOT block Steps 6–10.
- A vault write error (read-only mount, disk full, perms) is also **fail-soft** (`backup-write-failed`, exit 0) — it never aborts the meeting flow.
- The script does **not** commit; the `vault-autocommit` systemd timer pushes the new transcript to GitHub within 15 minutes.
- Notion remains the **system of record**; the vault copy is the **backup**. Never write secrets/credentials into the vault (it is a private GitHub repo, pushed off-VM).

### Step 6: Generate Slack update

**Prerequisite: Resolve Slack user IDs for action-item owners.** Load `~/.hermes/team-roster.yaml` and extract `slack_user_id` for each owner. Team members get `<@UID>` mentions; avoid bracketed `[Name]` or `*[Name]*` placeholders.

Format a Slack message (mrkdwn):

```
**[Project] – [meeting type] ([date]) – team update**

*Notes:*

*[Topic A]:*
• [point]

*[Topic B]:*
• [point]

*Action items:*
• <@{slack_user_id}> — [task] — *[due if known]*

*Notion Tasks:*  (omit if no tasks created)
• [Task name](url) — Owner: [Name]

Full notes: [URL]
```

### Step 7: Create Notion tasks for action items

**⚠️ Before creating, check for existing tasks** — Query the Tasks DB by Task name (title filter) for each proposed action item. Use `POST /v1/databases/{db_id}/query` with a filter on the `Task name` title property containing key words from the item. If a match already exists (from a prior session, manual logging, or a recurring issue like a Google Maps fix), skip creation and log `skipped-existing: <task_name> -> <existing_url>`. Do NOT create duplicates. This prevents duplicating items that were already logged outside the meeting workflow.

For each action item with a clear owner:

```json
{
  "parent": { "database_id": "${CONFIGURED_NOTION_ID}" },
  "properties": {
    "Task name": { "title": [{ "text": { "content": "<task description>" } }] },
    "Assignee": { "people": [{ "id": "<notion-user-id>" }] },
    "Status": { "status": { "name": "Not Started" } },
    "Priority": { "select": { "name": "<Medium>" } },
    "Due": { "date": { "start": "YYYY-MM-DD" } },
    "Project": { "relation": [{ "id": "<project-page-id>" }] },
    "Tags": { "multi_select": [{ "name": "<tag>" }] }
  }
}
```

**Defaults:**
- Priority: `Medium` (escalate to `High` for "urgent", "ASAP", "blocker")
- Status: always `Not Started`
- Tags: infer from context ("Mobile", "Website", etc.)
- Due: omit if unknown; resolve relative dates ("this week") to concrete dates
- Project: omit if no project identified

**Do NOT create tasks for:** vague items without clear owners, informational/decisions (not actionable), owners without Notion accounts (ask first).

Capture each task URL.

### Step 8: Update Meeting Notes page with task links

**SKIPPED — the `PATCH /v1/pages/{page_id}/markdown` endpoint does not work.** Empirically verified June 2026: it returns `400 body failed validation: body.type should be defined`.

The Meeting Notes page already contains the full structured content (action items, decisions, blockers) from creation in Step 5. Task URLs are delivered inline in the Slack update (Step 9). No further page update is needed for the standard workflow.

If task links must appear in the page body in the future, use `PATCH /v1/blocks/{page_id}/children` to append block children (see the `notion` skill for block append syntax).

### Step 9: Deliver Slack update

Slack destination ladder (from `slack-update-from-meeting-notes`):

1. Project/team channel (resolve via Slack API `conversations.list`)
2. Meeting organizer DM (resolve via `users.lookupByEmail` → `conversations.open`)
3. Slack home channel (fallback with notice)

Always include `Full notes: [URL]` as the last line.

### Step 10: Confirm

Return to user:
- Meeting Notes page URL
- Notion tasks created (with links)
- Slack destination
- Done.

## Slack Format

- Bold: `*text*`
- Links: `<url|text>`
- Italic: `_text_`
- Bullets: `•`
- No tables

## Pitfalls

1. **Notion user resolution.** Assignee uses Notion user IDs, not emails. Resolve via `GET /v1/users` first.

1a. **Nested rich_text complexity in JSON.** When building Notion blocks with rich_text containing multiple inline text segments (bold, italic, links), the JSON structure becomes deeply nested and error-prone:
   ```json
   {"object": "block", "type": "paragraph", "paragraph": {
     "rich_text": [
       {"text": {"content": "Normal ", "bold": false}},
       {"text": {"content": "bold", "bold": true}},
       {"text": {"content": " text"}
     ]
   }}
   ```
   **Recommendation:** Keep rich_text simple when possible. For action items with inline formatting (assignee name in bold), build them as **separate text segments** in the rich_text array rather than trying to nest content and formatting. Alternatively, use the Notion block append endpoint (`PATCH /v1/blocks/{page_id}/children`) which is more tolerant of incremental structure. If nested rich_text is unavoidable, validate the JSON structure separately before sending (use Python's json.dumps with indent=2 to spot bracket mismatches).

1b. **Notion API: use `write_file` + `terminal python3` pattern, NOT `ntn` CLI or raw `curl`.** Both `curl` and `ntn` CLI frequently time out (20s+) on Notion API calls. Use the `write_file` tool to create a Python script at `/tmp/`, then execute with `terminal` (`python3 /tmp/script.py`). This consistently completes in <1s. Pattern:

1. Use `write_file` tool with `path="/tmp/notion_api_call.py"` and the Python script as `content`
2. Use `terminal` tool: `python3 /tmp/notion_api_call.py`

**Do NOT write Python scripts via terminal heredoc (`cat > /tmp/file.py << 'EOF'`)** — the `line.startswith("KEY=")` pattern breaks on shell quoting inside heredocs, producing `SyntaxError: unterminated string literal`. Use the `write_file` tool instead.

**IMPORTANT: The `write_file` tool sanitizes `KEY=VALUE` patterns in file content.** Any line containing `NOTION_API_TOKEN=` or `SLACK_BOT_TOKEN=` (i.e., env var name followed by `=`) gets replaced with `***`, corrupting the Python code. This causes `SyntaxError` at runtime.

**However: in this environment, secrets are already loaded in `os.environ` from Google Secret Manager at gateway startup.** The simplest and safest approach for Python scripts is to read directly from the environment:

```python
import os
token = os.environ["NOTION_API_TOKEN"]
```

No `.env` parsing needed. If the env var is not found (e.g., running on a remote backend), fall back to `.env` parsing (see below) or GCP Secret Manager.

**Canonical .env parsing pattern (sanitization-safe, for remote backends):**
```python
import urllib.request, json, os
env_path = os.path.expanduser("~/.hermes/.env")
env_vars = {}
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        if key.strip():
            env_vars[key.strip()] = val.strip()
token = env_vars.get("NOTION_API_TOKEN") or env_vars.get("NOTION_API_KEY")
```

**Why `partition("=")` instead of `startswith("KEY=")`:** The `write_file` tool sanitizes any `KEY=` pattern it recognizes as an env var assignment. Using `partition("=")` avoids putting `KEY=` as a contiguous string in the file content, sidestepping the sanitization entirely.

**Why parse `~/.hermes/.env` directly instead of `os.environ`:** Not all tokens in `.env` are exported to the shell environment. For example, `NOTION_API_TOKEN` may be in `os.environ` but `SLACK_BOT_TOKEN` may not be. Always parse `.env` directly for reliability.

Full Python script template:
```python
import urllib.request, json, os
env_path = os.path.expanduser("~/.hermes/.env")
env_vars = {}
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        if key.strip():
            env_vars[key.strip()] = val.strip()
token = env_vars.get("NOTION_API_TOKEN") or env_vars.get("NOTION_API_KEY")
req = urllib.request.Request(
    'https://api.notion.com/v1/...',
    data=json.dumps(payload).encode(),
    headers={'Authorization': f'Bearer {token}', 'Notion-Version': '2025-09-03', 'Content-Type': 'application/json'},
    method='POST'
)
with urllib.request.urlopen(req, timeout=20) as resp:
    result = json.loads(resp.read())
```

2. **Status type is `status`, not `select`.** Use `status.equals` / `status.does_not_equal` for Tasks. Meeting Notes Category is `multi_select` — different syntax.

3. **Rate limits.** ~3 req/s. Add ~300ms between task creation requests.

4. **Multi-line markdown.** Write to a temp file, use `ntn api "v1/pages/{page_id}" -X PATCH -d @/tmp/notes.json`. ntn inline breaks on newlines.

5. **Project DB IDs.** Query with `data_source_id=${CONFIGURED_NOTION_ID}`. Create pages with `database_id=${CONFIGURED_NOTION_ID}`.

6. **Client is free-text.** Projects `Client` field is `rich_text`, not a relation.

7. **Empty sections.** Omit `*Notion Tasks:*` heading if no tasks created. Don't leave empty section headers.

8. **Ambiguous action items.** Ask before guessing assignees or dates.

9. **Block append on DB pages.** Use `PATCH /v1/blocks/{page_id}/children` to append block children to an existing page. Do NOT use the non-functional `/v1/pages/{id}/markdown` PATCH endpoint — it returns `400 body failed validation: body.type should be defined`.

10. **Query filter complexity.** When querying a database to retrieve a newly-created page (e.g., to get its URL after creation), complex filters with `and` / `or` combinations may return `400 Bad Request`. **Workaround:** After creating a page via POST, extract the page ID from the response directly (field `id`) and construct the Notion URL as `${CONFIGURED_SOURCE_URL}'-', '')}` instead of querying. The query is unnecessary — the creation response contains all you need.

11. **Google Meet caption speaker labels are inconsistent.** Meet's auto-caption engine produces speaker labels that don't match team display names: lowercase (`"operator nguyen"`), full legal names (`"Jirrattakarn Chantapakawin"` instead of `"Participant D"`), inconsistent casing (`"Participant A Nguyen"` vs `"operator nguyen"`). Before writing the transcript into Notion, normalize speaker labels against the team roster (e.g., from `~/.hermes/team-roster.yaml` or the `/v1/users` Notion response). Map lowercase/partial matches to canonical display names. Without normalization, the Notion meeting notes look unpolished and speaker-based action-item extraction is harder.

12. **Code block `language` field rejects "text" — use "plain text".** When creating or appending a Notion page with a `code` block via `POST /v1/pages` or `PATCH /v1/blocks/{id}/children`, the `language` field MUST be one of the ~80 Notion-enum values (e.g., `javascript`, `python`, `json`). The plain-text option is `plain text`, NOT `text`. Setting `"language": "text"` returns `400 validation_error: body.children[N].code.language should be one of [...], instead was "text"`. Common safe values: `plain text`, `json`, `python`, `markdown`, `javascript`, `shell`, `yaml`. When in doubt, use `plain text`.

12b. **File upload flow: `POST /v1/file_uploads` then `POST /v1/file_uploads/{id}/send`.** When attaching a transcript as a file, the Notion API requires a two-step flow: (1) create a file upload with `POST /v1/file_uploads` with `{"mode": "single_part", "filename": "...", "content_type": "text/plain"}` → returns a file upload `id`; (2) send the file content via `POST /v1/file_uploads/{id}/send` with `Content-Type: multipart/form-data` containing the file. Then reference the file upload id in a file block: `{"type": "file", "file": {"file_upload": {"id": "..."}, "name": "..."}}`. The `attach-transcript-to-notion.py` script handles all of this automatically. Do NOT use `POST /v1/files` — that endpoint is for file metadata only and returns `400 invalid_request_url`.

## Tool call pattern

```
1. parse transcript
2. summarize
3. GET /v1/users → resolve attendees
4. search Projects DB → detect project
5. create Meeting Notes page
5b. transform transcript → upload + attach as file block on page
6. for each action item: create Notion task
7. PATCH Meeting Notes page with task links
8. format Slack update
9. send_message → Slack
```

## Reference Files

- `references/notion-schema.md` — Full schema for all 3 DBs
- `references/notion-api-working-patterns.md` — Tested urllib.request patterns, database IDs, rate-limit timing
- `references/database-pitfalls.md` — ntn limitations, multi-line content, block append
- `references/speaker-normalization.md` — Canonical speaker-name mapping for Google Meet captions (Operator Nguyen → Operator, Jirrattakarn Chantapakawin → Participant D, etc.), including match algorithm and edge cases

**Cross-reference:** The `meeting-finalize` skill has `references/write-file-env-var-sanitization.md` documenting the `write_file` tool's env var `KEY=` sanitization and the canonical `partition("=")` workaround.

## License

Example Organization internal tool. Use within crew scope. No external sharing.
