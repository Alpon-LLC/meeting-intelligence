---
name: slack-update-from-meeting-notes
description: Generates a concise Slack team update from meeting notes or transcripts. Use when the user asks for a "Slack update," "team update from meeting notes," "summarize notes for Slack," or wants to post meeting outcomes to a channel or DM.
version: 1.2.0
metadata:
  hermes:
    tags: [slack, meetings, linear, notion]
    category: meeting
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Slack Update from Meeting Notes

## When to use

- User has meeting notes (Notion, transcript, or pasted text) and wants a message for Slack
- User says "Slack update," "team update," "send to my team," or "DM me" in the context of a meeting
- Output will be posted to a channel or DM'd

## Reference files

Install path mirrors Hermes layout: `${HERMES_HOME}/skills/meeting/slack-update-from-meeting-notes/`. Load bundled Markdown with `skill_view` (avoid repo-relative or `.cursor` paths):

| Need | Load |
| --- | --- |
| Copy-paste user prompts | `skill_view("slack-update-from-meeting-notes", "references/user-prompts.md")` |
| Full transcript → Notion → Tasks workflow (runs this skill in Step 6) | `skill_view("meeting-transcript-to-notion", "SKILL.md")` |

## Instructions

1. **Input**: Use the meeting notes or transcript in context (Notion page, pasted notes, or transcript).
2. **Extract**: Identify discussion themes, decisions, blockers, and **action items** (task + owner + due date when known).
3. **Format**: Produce a **single** Slack message with **three layers** — do **not** use one flat list of equal-weight bullets for everything.

### Prerequisite: Resolve Slack user IDs

Before composing the message, resolve every person who appears as an action-item owner to their Slack user ID. Load `${HERMES_HOME}/team-roster.yaml` and extract `slack_user_id` for each person. The roster is the authoritative source — do NOT fall back to Slack API calls.

**If a person is not in the roster (client, external collaborator):** use their name in plain text (e.g. `Samer`). Do NOT fabricate a Slack ID. Do NOT use `[Name]` brackets — either a valid `<@UID>` mention or plain text name, nothing else.

### Structure (required)

1. **Title line** (one line): `**[Project/Meeting name] – [brief descriptor] ([date]) – team update**`
2. **`*Notes:`*** — Group summary bullets **by discussion topic**. Each topic must be visually distinct:
   - Put the label `*Topic name:*` on its own line (or start a line with `*Topic —*` then the summary), then **1–3 bullets** (`•`) under that theme before moving to the next topic.
   - Use **3–7 topics** typical examples: *Design / UX*, *Product direction*, *Engineering / infra*, *Process / cadence*, *Client asks*, *Decisions* (if not folded into a topic), *Blockers* (if any).
   - Within a topic, each bullet stays one sentence or short phrase; no long paragraphs.
3. **`*Action items:`*** — A **separate** section after Notes. List only concrete follow-ups:
   - One bullet per item; use `<@{slack_user_id}>` for team members (resolved from team-roster.yaml), plain text name for externals. Prefer `• <@{owner_id}> — [verb-led task] — *[due if known]*`.
   - Do NOT use `[Name]` or `*[Name]*` bracketed placeholders — Slack interprets those as literal text, not mentions. The user considers this a style violation.
   - Do not bury action items inside the Notes topics; they belong here (you may one-line *reference* a theme in the task, e.g. "<@CONFIGURED_SLACK_ID> — implement Option 2 hybrid per design notes").
4. **`*Linear:`*** — When **Linear tickets exist**. One bullet per issue: `• <url|KEY> — short title` (Slack link syntax). **Omit entirely if no Linear tickets.**
5. **`*Notion Tasks:`*** — When **Notion tasks were created** (from meeting-transcript-to-notion Step 7). One bullet per task: `• [Task name](notion-task-url) — Owner: [Name] — Due: [date or "TBD"]`. **Omit entirely if no Notion tasks.**
6. **Full notes link** — Last line, explicit label: `Full notes: [URL]` (or `Notion: [URL]`). Never leave only a bare URL with no label.

**Section order:** Notes → Action items → Linear → Notion Tasks → Full notes. Both `*Linear:*` and `*Notion Tasks:*` are optional — omit any section where no items exist. Never leave empty section headings.

### Styling

- Slack mrkdwn: `**bold**` for the title; `*italic*` for section headings `*Notes:*`, `*Action items:*`, `*Linear:*`, `*Notion Tasks:*` and for topic labels.
- Use `•` for bullets; keep **topic groups** separated by a blank line for scanability.
- Links: `<url|text>` — NOT `[text](url)`.
- Bold: `*text*` — NOT `**text**` (Slack uses single asterisk for bold).

### Length and tone

- Aim for **short** Notes per topic (the link carries detail); **all** critical actions must appear under *Action items*; **ticket/task IDs** live under their own sections so Slack matches Notion.
- Tone: internal, factual, no client-facing polish.

## Pitfalls

- **No space after `|` in Slack link syntax.** `<url|text>` is correct — `<url| text>` with a space renders as a broken link with the URL literal showing.
- **No custom emoji.** `:notion:`, `:linear:` and similar non-standard emoji shortcodes are NOT part of Slack's built-in emoji set. They render as broken `:name:` text. Use standard Slack emoji (`:link:`, `:white_check_mark:`, `:rocket:`, `:calendar:`) or plain Unicode (🔗 ✓ 🚀 📅) instead.
- **No HTML entities.** Use plain `&` not `&amp;`, plain `<` not `&lt;`, etc. Slack renders HTML entities as literal text.
- **Verify your message visually before sending.** If you can't preview it, paste into a draft first. A broken Slack post is worse than no post — it makes the team read mangled output.

## Output template

```text
**[Project] – [meeting type] ([date]) – team update**

*Notes:*

*[Topic A]:*
• [point]
• [point]

*[Topic B]:*
• [point]

*[Topic C]:*
• [point]

*Action items:*
• <@{slack_user_id}> — [task] — *[due if known]*
• <@{slack_user_id_2}> — [task]

*Linear:*  _(omit section if no Linear tickets)_
• <https://linear.app/…/TEAM-123|TEAM-123> — short title

*Notion Tasks:*  _(omit section if no Notion tasks)_
• [Task name](${CONFIGURED_SOURCE_URL}) — Owner: Name — Due: date

Full notes: [URL]
```

## Optional: send via Slack

- When this skill is used **standalone**, sending is optional unless the user asks to post or DM.
- When chained from **`meeting-transcript-to-notion`**, the parent skill **defaults to** formatting and sending **after** Linear issues and Notion tasks exist so both sections use real keys/URLs.
- If the user asked for **Slack before tickets/tasks**, send once without `*Linear:*` and `*Notion Tasks:*`, then a **follow-up** with those sections after issues/tasks are created.

### Slack destination ladder (follow in order — stop at first success)

> ⚠️ **Tool availability in headless/webhook sessions:** `slack_send_message` and `slack_search_channels` are Slack MCP tools — **not available in webhook sessions**. Use `send_message` (native tool) and `terminal` (curl) instead.

**Step 1 — Project / team channel** (if meeting belongs to a named project or team):
- Use `terminal` to search for the channel:
  ```bash
  source ${HERMES_HOME}/.env
  curl -s "https://slack.com/api/conversations.list?types=public_channel,private_channel&exclude_archived=true" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" | python3 -c "
  import sys,json
  chs = json.load(sys.stdin).get('channels',[])
  for c in chs:
      if 'CHANNEL_NAME' in c['name'].lower(): print(c['id'], c['name'])
  "
  ```
- If found: `send_message target="slack:CXXXXXXXX"` with the returned channel ID.

**Step 2 — Target person DM** (organizer or a specific person — resolve dynamically):
- Look up user by email via `terminal`:
  ```bash
  source ${HERMES_HOME}/.env
  USER_ID=$(curl -s "https://slack.com/api/users.lookupByEmail?email=PERSON_EMAIL" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('id',''))")
  DM_ID=$(curl -s -X POST "https://slack.com/api/conversations.open" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H "Content-Type: application/json" \
    -d "{\"users\": \"$USER_ID\"}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('channel',{}).get('id',''))")
  echo "DM: $DM_ID"
  ```
- Then: `send_message target="slack:{DM_ID}"`.
- ⚠️ Do **not** use `target: "slack"` alone — that goes to the home channel.

**Step 3 — Slack home channel** (only if Steps 1–2 both fail):
- `send_message target="slack"` (uses `SLACK_HOME_CHANNEL` env var).
- **Prepend the message with a visible fallback notice**, e.g.:
  > ⚠️ *Could not find a Slack channel or DM for this meeting. Sending to the home channel as fallback. Original target: `{intended_target}`. Reason: `{failure_reason}`.*
- Always include what was attempted and why it failed so whoever reads it can act on it.

**Step 4 — Last resort**: paste the message in chat and explain why Slack delivery failed.

## Example

**Viamente – design review (21 Mar) – team update**

*Notes:*

*Design direction:*
• Option 3 dropped; ship Option 2 + bottom third of Option 1; no home scroll if possible; profile back to bottom nav.
• Copy back to "Your safe space (awaits)"; tighter header (logo + tagline).

*Brand / visuals:*
• No floral / WhatsApp-style emoji look; custom ViaMente geometry; light + dark on all new screens.

*Devices / Figma:*
• Fold / wide — Android Medium + auto layout; optional ~700px mock later.

*Process:*
• Daily drops after ~15 min internal review; client reviews after ~8pm her time; Figma over long decks.

*Engineering:*
• Terraform in progress; waiting on Samer's latest service info; DUNS may lag (Eid).

*Action items:*
• <@CONFIGURED_SLACK_ID> — Implement hybrid layout + brand guardrails; incremental screens daily — *ongoing*
• Samer — Send latest backend inputs to Nelson — *ASAP*
• <@CONFIGURED_SLACK_ID> / Kamat — Terraform + track DUNS — *this week*

*Notion Tasks:*
• [Implement hybrid layout](${CONFIGURED_SOURCE_URL}) — Owner: Participant D — Due: ongoing
• [Terraform setup](${CONFIGURED_SOURCE_URL}) — Owner: Nelson — Due: this week

Full notes: ${CONFIGURED_SOURCE_URL}
