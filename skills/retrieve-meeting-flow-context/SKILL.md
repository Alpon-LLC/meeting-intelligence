---
name: retrieve-meeting-flow-context
description: "Retrieves the full meeting triage context from a context file created during the post-meeting-approval step. When an organizer replies to a Slack triage DM, this skill rehydrates the agent with the meeting transcript summary, action items, task URLs, and Notion page reference so the agent can process feedback without asking the organizer to re-explain. Triggered by running /retrieve-meeting-flow-context <context_ref> or when the agent detects a context reference in an incoming Slack message."
version: 1.0.0
metadata:
  hermes:
    tags: [meeting, context, triage, rehydrate, post-meeting]
    related_skills: [post-meeting-approval, meeting-finalize, meeting-transcript-to-notion]
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Retrieve Meeting Flow Context

## When to use

- When the meeting organizer replies to a Slack triage DM with feedback on action items / tasks
- When the agent receives a Slack message containing a context reference (e.g., `ctx:abc-defg-hij`)
- When the agent needs to rehydrate meeting context after a gap (organizer replied hours later)

## Input

- `context_ref` — the meeting context reference ID (e.g., `abc-defg-hij`)
  - Extracted from the Slack message if it contains `ctx:<meeting_id>` or `context:<meeting_id>`
  - Or passed explicitly by the agent

## Workflow

### Step 1: Locate the context file

The context file lives at:
```
${HERMES_HOME}/workspace/meetings/<context_ref>/triage-context.json
```

If the file does not exist:
- Return an error: "No triage context found for `<context_ref>`. The context may have expired (24h TTL) or the meeting may have already been finalized."
- Suggest the organizer check the Notion page directly

If the file exists but is older than 24 hours:
- Return a warning: "Context is from <age> hours ago. Proceeding with stale context."
- Still load the file

### Step 2: Load and return the context

Read `triage-context.json` and return the full content to the agent. The context file contains:

```json
{
  "meeting_id": "abc-defg-hij",
  "meeting_title": "Daily Standup",
  "meeting_date": "2026-06-08",
  "organizer": {
    "name": "Meeting Bot",
    "email": "person@example.org",
    "slack_user_id": "CONFIGURED_SLACK_ID"
  },
  "notion_page_url": "${CONFIGURED_SOURCE_URL}",
  "transcript_summary": "Organizer opened the standup. Participant A reported on Example Finance integration status. Operator updated on wallet connection flow. Action items were assigned.",
  "transcript_path": "${HERMES_HOME}/workspace/meetings/abc-defg-hij/transcript.json",
  "action_items": [
    {
      "task": "Complete wallet connection flow PR",
      "assignee": "Operator",
      "assignee_email": "person@example.org",
      "notion_task_url": "${CONFIGURED_SOURCE_URL}",
      "source_transcript_lines": ["[15:35:22] Operator: ...PR up by EOD"]
    }
  ],
  "tasks_created": [
    {
      "title": "Complete wallet connection flow PR",
      "notion_url": "${CONFIGURED_SOURCE_URL}",
      "assignee": "Operator"
    }
  ],
  "approval_status": "pending",
  "created_at": "2026-06-08T17:20:00Z",
  "expires_at": "2026-06-09T17:20:00Z"
}
```

### Step 3: Deep recall (tier 2 — session_search) — only WHEN NEEDED

The context file (tier 1) holds distilled facts. If the organizer asks about something the
JSON doesn't carry (why an item was dropped, what else was discussed, an earlier proposal),
use the stamped `session_id` with the **`session_search` tool** to recover the full working
memory of the finalize run:

```
session_search(session_id="<session_id from the context file>")
```

Skip this for a plain "approve" — only reach for it when the file is insufficient.

### Step 4: Instruct the agent

After loading the context, the agent should:

1. **Acknowledge** the organizer's feedback in the Slack thread
2. **Classify the whole reply before writing.** Approval language plus requested edits (for example, “looks good; mark X done”) is a **modified approval**, not merely a status-update request. Preserve the approval of every proposed action item, apply the requested edits, and then continue the complete approval/finalization workflow. Do not perform only the edits and leave the meeting in `pending`.
3. **Compare** the feedback against the existing tasks and action items.
4. **Update Notion tasks** as needed:
   - Reassign tasks (change assignee)
   - Remove tasks (delete from Notion)
   - Add new tasks (create in Notion)
   - Modify task descriptions
   - Update statuses of named existing tasks. Search the Tasks data source semantically using spelling variants and distinctive title fragments; require one unique task match, PATCH only `Status`, and re-fetch it. If one requested status update is ambiguous, complete and verify every unambiguous update plus the approved meeting actions, record the unresolved item in context, and report that single blocker rather than abandoning the rest of finalization.
   - If approval says **"check existing tasks"** (or equivalent), run a semantic duplicate search for **each proposed action item before creating anything**. Search with several distinctive terms and inspect live status/assignee/project metadata. Reuse a task only when it covers the same outcome; create only the missing items. Broadly related tasks are routing siblings, not duplicates.
4. For mixed reuse/create results, preserve one record per approved action in the context file with `resolution: "existing-task-reused"` or `resolution: "created"`, plus the live Notion page ID and URL. This keeps the approval audit aligned one-to-one with the proposal.
5. Add all approved task links—both reused and newly created—to the meeting notes under an approved-action-items section. Verify the links from Notion after the write.
6. **Update the context file** with the new `approval_status` (`approved`, `modified`, or `rejected`), one resolution record per proposed action, receipts for requested existing-task edits, and any unresolved ambiguity. Merge with the live file; never replace it from a stale in-memory copy.
7. **Notify the organizer** with separate **Reused existing**, **Created**, **Updated**, and **Needs exact task match** groups as applicable. Keep the receipt concise, but never imply the meeting is finalized when approved actions or context writes remain pending.
8. **Signal meeting-finalize** to write `.finalized` only after task writes/reuse mapping, meeting-note links, context state, and all unambiguous requested edits have been verified (if not already written).

## Pitfalls

1. **Stale context.** The context file has a 24-hour TTL. If the organizer replies after expiry, the agent should still try to help but warn about stale context.

2. **Concurrent modifications.** If the context file was already updated by a previous reply, load the latest version and work from there. Don't overwrite — merge changes.

3. **Missing Notion permissions.** If the agent can't update a Notion task (permissions issue), notify the organizer and suggest manual edits.

4. **Context file cleanup.** After finalization, the context file should be kept for 7 days for audit purposes, then auto-pruned.

5. **This skill does NOT write `.finalized`.** That is the responsibility of `meeting-finalize`. This skill only rehydrates context and processes feedback.
