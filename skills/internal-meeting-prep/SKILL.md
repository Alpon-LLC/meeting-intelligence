---
name: internal-meeting-prep
description: "Intelligent meeting prep for Example Organization team members. Classifies meeting type (standup, sprint-planning, 1:1, sales-sync, etc.), gathers context from Notion, GitHub, session history, and sends personalized Slack DMs with type-appropriate prep content. Triggered by calendar webhook 30 minutes before meeting start."
version: 2.8.0
metadata:
  hermes:
    tags: [meeting, internal, notion, slack, prep, automation, github, sales]
    related_skills: [meeting-auto-join, meeting-finalize, webhook-subscriptions]
---

> PUBLIC VEXA PORT: Read `references/public-runtime.md` before this preserved workflow. It resolves deployment paths, configured IDs, direct Vexa versus agent webhook routing, legacy-only branches and optional adapter permissions. Do not execute literal configuration placeholders.



# Internal Meeting Prep v2

## When to use

- Triggered by calendar webhook ~30 minutes before an internal meeting starts.
- The webhook payload includes: `id` (calendar event ID — used as directory key), `title`, `start`, `end`, `attendees[]`, `description`, `meet_url` (optional).
- Lockfile directory: `${HERMES_HOME}/workspace/meetings/<event_id>/` where `event_id` is the webhook `id` field.
- Only fires for meetings where 2+ attendees are @example.org team members.
- Not invoked manually. For ad-hoc prep, ask the agent directly: "Prep the team for the 3pm standup."

## Related Skills

This skill is part of the Example Organization meeting family. For shared conventions (lockfile paths, Slack UID resolution, domain constants), see the `meeting` umbrella skill.

- `meeting-auto-join` — joins Meet on Meeting Bot's behalf when calendar webhook fires
- `meeting-finalize` — reads transcript, pushes to Notion, extracts action items
- `meeting-transcript-to-notion` — full transcript-to-Notion workflow

## Prerequisites

- **Webhook platform enabled** — `hermes webhook list` must show the gateway running. See `webhook-subscriptions` skill for setup.
- **Notion integration** — `NOTION_API_TOKEN` set in `${HERMES_HOME}/.env`. Integration must be shared with the Tasks and Projects databases. See `references/notion-tasks-schema.md` for IDs.
- **Slack delivery** — Prefer `send_message` tool if available (sends directly without dedup). Fallback: `SLACK_BOT_TOKEN` env var + direct Slack API call via Python script. Do NOT use `hermes send` CLI — it has session-level delivery dedup that blocks sends to channels the webhook session is already configured to deliver to. See Pitfall #24 for the fallback pattern.
- **Calendar webhook subscription** — A webhook subscription named `meeting-prep` must be registered (see Setup below).

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `INTERNAL_MEETING_PREP_DRY_RUN` | unset | When `1`, computes the prep but does not send Slack DMs. Logs everything. |
| `INTERNAL_MEETING_PREP_WINDOW_MIN` | `30` | Minutes before meeting start to trigger. |
| `MEETING_INTERNAL_DOMAIN` | `example.org` | Used to filter internal attendees. |

## Reference Files

| File | Content |
|---|---|
| `references/notion-tasks-schema.md` | Notion Tasks and Projects DB schemas, IDs, open-items filter, pitfalls |
| `references/notion-user-resolution.md` | Notion user ID resolution patterns |
| `references/webhook-payload-schema.md` | Expected webhook payload shape and field types |
| `references/python-prep-script-example.md` | Full working Python script template (Approach B) — Notion queries + Slack DMs + summary output |
| `references/two-phase-lockfile-pattern.md` | Lockfile dedup pattern: `.lock` for mutex, `summary.json` for debugging |
| `references/example-notion-workspace.md` | **Example Organization Notion hierarchy** — root workspace structure, which databases are accessible to the Meeting Bot CoS integration, and discovery call patterns |
| `references/sales-meeting-prep.md` | **External sales meeting prep** — two modes: automated webhook execution (research + Notion pages + Slack briefs) and manual ad-hoc workflow. Proven working in 2026-06-23 session |
| `references/bot-token-dm-limitation.md` | **Slack bot token DM delivery limitations** — why `chat.postMessage` may silently fail for DMs and how to detect it |
| `references/slack-dm-length-safe-rendering.md` | **Length-safe Slack rendering** — progressively re-render structured sections instead of slicing raw mrkdwn links; includes dry-run checks |
| `references/project-titled-weekly-sync.md` | **Project-scoped weekly syncs** — when a `* Sync` title uniquely matches an active project, prioritize that project's tasks/PRs and keep unrelated work out of the main DM |
| `references/ad-hoc-chat-request-prep.md` | **Ad-hoc prep from chat excerpts** — resolve sparse Slack/Teams requests against Calendar, detect requested-platform vs booked-link mismatches, distinguish existing engagements from new leads, and frame engineering-resource scoping questions |
| `references/helper-safety-preflight.md` | **Helper safety preflight** — audit event scoping, type-specific filters, unique Projects DB resolution, API-layer project filtering, nested roster parsing, Slack `ts` verification, dry-run parity, and final artifacts before trusting the maintained helper |
| `references/recurring-occurrence-runner-reuse.md` | **Recurring occurrence runner reuse** — safely adapt a prior event-scoped runner; rebuild attendees/time, resolve the roster at runtime, and drive skip reporting from attendees rather than a copied roster |

## Setup

### 1. Configure calendar to POST to the webhook URL

The actual Google Calendar webhook payload looks like:

```json
{
  "id": "5j6dcepnuedkc6ujq3qua0eb9q",
  "title": "Sprint Planning",
  "start": "2026-06-08T15:00:00+07:00",
  "end": "2026-06-08T16:00:00+07:00",
  "meet_url": "https://meet.google.com/xxi-hgha-nawk",
  "organizer": { "email": "person@example.org", "display_name": null, "self": false },
  "attendees": [
    { "email": "person@example.org", "response_status": "accepted", "organizer": true, "self": false, "optional": false },
    { "email": "person@example.org", "response_status": "accepted", "organizer": false, "self": false, "optional": false },
    { "email": "person@example.org", "response_status": "needsAction", "organizer": false, "self": true, "optional": false }
  ]
}
```

The `id` field is the calendar event ID. Use it as the directory name for lockfiles — NOT the Meet slug from `meet_url`. Recurring events share the same `meet_url` but each occurrence has a unique `id`.

### 2. Verify Notion access

```bash
source ${HERMES_HOME}/.env
curl -s -X POST "https://api.notion.com/v1/data_sources/${CONFIGURED_NOTION_ID}/query" \
  -H "Authorization: Bearer $NOTION_API_TOKEN" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"page_size": 1}' | jq '.results | length'
```

Should return `1` (or more).

---

## Workflow

### Scheduler dispatch receipt — mandatory first and last action

Webhook payloads from `meeting-trigger/run.js` include `_dispatch` with `key`, `phase`, `attempt`,
`delivery_id`, `claim_path`, and `receipt_path`. When `_dispatch` is present:

1. Before Step 0 or any external read/write, run:
   `node ${HERMES_HOME}/scripts/meeting-trigger/executor-receipt.js begin --claim-path <claim_path> --receipt-path <receipt_path> --key <key> --phase prep --attempt <attempt> --delivery-id <delivery_id>`.
2. Exit code `3` means a newer attempt superseded this run. Stop immediately. Any other non-zero
   exit is a hard failure; do not continue untracked.
3. Run the same command with `heartbeat` immediately before Slack/Notion delivery. A fenced attempt
   must not send anything.
4. After `.lock` and `summary.json` are verified, run `finish --status succeeded --outcome prep-done`.
   For a deliberate terminal skip use `finish --status skipped --outcome <stable-skip-reason>`.
   For a recoverable crash/blocker use `finish --status failed --outcome <stable-failure-reason>
   --retryable true`.

Never infer completion from HTTP acceptance. The terminal receipt is written only after the prep
artifacts and intended delivery receipts are verified.

### Step 0: Dedup lock check + claim (MANDATORY — first thing, before anything else)

**Before any other work**, extract `event_id` from the payload and check `${HERMES_HOME}/workspace/meetings/<event_id>/.lock`.

The `event_id` is the calendar event ID from the webhook payload's `id` field. This is unique per occurrence even for recurring events. Do NOT use the Meet slug from `meet_url`.

Resolution logic:

1. **No lockfile exists** → create directory (`mkdir -p`) and write `.lock` immediately with `{"status": "prepping", "prepping_since": "<ISO8601>"}`. Proceed to Step 1.
2. **Lockfile exists with `status: "done"`** → output `internal-meeting-prep: skip event="<title>" reason="already-prepped"` and **stop immediately**.
3. **Lockfile exists with `status: "prepping"` AND `prepping_since` is < 5 minutes old** → another session is actively working. Output `internal-meeting-prep: skip event="<title>" reason="in-progress"` and **stop immediately**.
   - **Future-timestamp trap:** If `prepping_since` is AFTER the current system time (`prepping_since > datetime.now(timezone.utc)`), the age calculation yields a negative number, which passes the `< 5 minutes` check. The lock is actually stale — someone used a meeting-derived timestamp instead of `datetime.now()` (Pitfall #34 violation). **Detect this case explicitly:** parse `prepping_since` and compare to actual system time before applying rule #3. If `prepping_since` is in the future, bypass rule #3 and treat as stale (rule #4 path — overwrite and proceed).
4. **Lockfile exists with `status: "prepping"` AND `prepping_since` is >= 5 minutes old** → stale lock. Overwrite and proceed. **After writing, re-read the lockfile to verify** — Hermes may emit a sibling-subagent warning (see Pitfall #30) that looks alarming but is benign if the content matches what you wrote.

**CRITICAL — recurring events:** The directory is named by `event_id` (calendar event ID), NOT the Meet slug. Recurring events reuse the same Meet URL.

### Step 1: Parse webhook payload

Extract from the payload:
- `event_id` — from the `id` field. Primary key for lockfile directory.
- `meet_url` — for the DM join link only. NOT for directory naming.
- `title` — meeting title
- `start` / `end` — ISO 8601 timestamps. Display in ICT (UTC+7).
- `attendee_emails[]` — from `attendees[].email`. Filter to @example.org addresses. Exclude bot accounts (e.g. `person@example.org`).
- `organizer_email` — from `organizer.email`.
- `description` — meeting agenda/context.

If fewer than 2 human internal (@example.org) attendees (excluding bots), skip.

### Step 2: Classify the meeting type

**Inputs:** `title`, `attendee_emails[]` (count + domains), `description` (if available)

**Classification logic (first match wins):**

| # | Condition | Type |
|---|---|---|
| 1 | title matches sales/partnership/lead keywords AND one or a few attendees is external party (email is not @example.org) and our CEO Organizer person@example.org) is also in the meeting | `external-sales` |
| 2 | exactly 2 human internal attendees (excluding person@example.org - you) | `1:1` |
| 3 | title contains "standup" or "daily" | `standup` |
| 4 | title contains "sprint plan" or "planning" | `sprint-planning` |
| 5 | title contains "grooming" or "backlog" | `grooming` |
| 6 | title contains "demo" and "sprint" | `sprint-demo` |
| 7 | title contains "retro" | `retrospective` |
| 8 | title contains "sales" and all attendees are @example.org | `sales-sync` |
| 9 | title contains "sync" or "weekly" | `weekly-sync` |
| 10 | title matches a known project name (cross-ref Notion Projects DB or description keywords) | `project` |
| 11 | none of the above | `generic` |

**`external-sales` keyword list:** sales, partnership, lead, client, prospect, proposal, pitch, RFP, SOW, BD, business development.

**Multi-keyword titles resolve by first-match ordering.** Real titles often carry two type keywords. Trust the table order — do not agonize. Example: **"Demo & Backlog Refinement"** contains both "demo" and "backlog". Rule 5 (`grooming`, matches "backlog") fires before rule 6 (`sprint-demo`, which requires BOTH "demo" AND "sprint"). Result: `grooming` — correct, because there's no "sprint" so rule 6 can't match anyway. Confirmed 2026-07-24. Similarly a "Sprint Demo & Retro" title hits rule 6 (`sprint-demo`) before rule 7 (`retrospective`). If the first-match result feels wrong for a recurring title, add an explicit higher-priority rule rather than reordering — reordering can regress other titles.

**`grooming` confirmed end-to-end run (2026-07-31, "Demo & Backlog Refinement", 5 human attendees: Organizer/Participant A/Operator/Participant C/Participant D).** Full path verified: classify=`grooming` (via the "Demo & Backlog" example above), per-attendee Tasks query = `Not Started` + `Story Points is_empty:true`, sort `Due asc` (Step 4a) PLUS an overdue query (`Due < today`, not Done/Archived) — grooming DMs include both a "Backlog items needing estimates" section and an "OVERDUE items needing grooming" section (Organizer was the only attendee with overdue: 6 items, oldest due 2026-06-26). Projects DB (Step 4b) queried for the 11-active-project context header. Organizer's proton alias (`person@example.org → person@example.org`, Pitfall #26) was required — without it he'd show 0 tasks; with it, 22 backlog + 6 overdue. Participant D correctly skipped (no Notion account per Pitfall #42, and no other content → `attendees_skipped` reason `no_notion_account`). `send_message` tool was ABSENT → GSM-into-mode-600-tempfile → positional-arg pattern (Pitfall #40) delivered all 4 DMs `ok:true` + valid `ts` first try (the `missing_charset` warning in the response is cosmetic, unrelated to delivery). The `--dry` positional-flag preview (Pitfall #40) caught the priority re-sort + truncation correctness before the live send. GSM reachable — 5th consecutive independent session. This is now the settled grooming reference run.

**`sprint-planning` confirmed end-to-end run (2026-08-03, "Sprint Planning", 5 human attendees: Organizer/Participant A/Operator/Participant C/Participant D).** Full path verified. Classify=`sprint-planning` (Rule 4, title contains "planning"). Per-attendee Tasks query = all open (not Done/Archived), `page_size 50` (Organizer & Operator both hit the 50 cap — they have more), PLUS a separate overdue query (`Due < today`). Organizer was the only attendee with overdue (7 items, oldest the outbound-CRM deploy due 2026-06-26 — and Organizer carried ALL 7 of the team's overdue). Projects DB (Step 4b) returned 12 active projects for the team-snapshot line. Organizer's proton alias (Pitfall #26) required — resolved him via `person@example.org`; without it, 0 tasks. Participant D correctly skipped (`no_notion_account`, no other content). GitHub PRs (Step 4d) via `gh search prs --owner EXAMPLE_ORG --author <gh>` — worked cleanly this run (Aorganizerin/operator-example/qnhu-fe; Participant A has no `github_username`, correctly skipped for gh). DM template: *Your open items (N)* capped at 15 shown + "+N more", *OVERDUE (N)*, *Team snapshot* (137+ open tasks | 11 PRs in flight). `send_message` tool ABSENT → dual-tempfile GSM → positional-arg pattern (Pitfall #40): `gather.py "$(cat .ntok)"` then `send.py "$(cat .stok)" gather_out.json prs_out.json`, with the `--dry` positional-flag preview (Pitfall #40) run first to verify the Python priority re-sort, `Med`→Medium normalization, and truncation before the live send. All 4 DMs `ok:true` + valid `ts` first try. GSM reachable — 5th consecutive independent session (07-23/27/28/29, 08-03). This is now the settled sprint-planning reference run.

**`standup` confirmed end-to-end run (2026-08-05, "Example Organization Daily Standup", 6 human attendees: Organizer/Participant D/Operator/Participant C/Participant A/Participant B).** Settled reference run for the highest-frequency type. Classify=`standup` (Rule 3, title contains "standup"). Per-attendee Tasks query = In Progress + Review only via the triple `does_not_equal` shape (Pitfall #1: exclude Done, Archived, AND Not Started at the Notion layer to dodge the page_size-truncation trap), `page_size 50`, PLUS a separate overdue query (`Due < today`, not Done/Archived). Counts: Organizer 14 focus / 12 overdue, Operator 21 focus / 0 overdue, Participant C 5 / 1, Participant A 7 / 0, Participant B 4 / 1. Organizer's proton alias (Pitfall #26: `person@example.org → person@example.org`) required and confirmed working — without it he'd show 0 tasks; with it, 14+12. GitHub PRs (Step 4d) via `gh search prs --owner EXAMPLE_ORG --author <gh> --state open` — then the CRITICAL client-side 48h `createdAt` re-filter (Pitfall #32): the raw query returned 10 open PRs for Organizer and 2 for Participant C, but only PR #39 (Organizer, 08-04) and #12 (Participant C, aeostars, 08-03) survived the 48h cut — Operator's only PR was from June and was correctly dropped so his DM omitted the Recent-activity section entirely. Participant A/Participant D/Participant B have no `github_username` → gh skipped. Python `PRI_ORDER` severity re-sort (Pitfall #43) was load-bearing: Organizer's High items sorted to top of a 14-item DM (10 shown + "+4 more"); a `[None]`-priority Participant C task rendered clean. Participant D skipped `no_notion_account` (Pitfall #42, no other content). **Participant B skipped `no_dm_channel (not in roster)` despite REAL content (Pitfall #45 standing gap — now 4+ consecutive standups).** `send_message` tool ABSENT → dual-tempfile GSM → positional-arg pattern (Pitfall #40): tokens fetched with `timeout 12` into mode-600 `.ntok`/`.stok` (Notion 50-char `ntn_`, Slack 59-char `xoxb-`, both `rc=0`), `gather.py "$(cat .ntok)"` then `send.py "$(cat .stok)" gather_out.json prs_out.json`, with the `--dry` positional-flag preview (Pitfall #40) run first to verify priority re-sort + truncation + skip-logic before the live send. All 4 deliverable DMs `ok:true` + valid `ts` first try. GSM reachable — now 6+ consecutive independent sessions (07-23/27/28/29, 08-03, 08-05). Token tempfiles wiped at finalize (`rm -f .ntok .stok .nerr .serr`). This is now the settled standup reference run.

**BEFORE assuming `external-sales`: is this a NEW lead or an EXISTING engagement?** External attendee + Organizer present *looks* like Rule 1, but Rule 1 requires **sales keywords in the title** (see keyword list below). A recurring client/partner sync for an already-onboarded engagement is NOT `external-sales` — it's a `weekly-sync` / `project`. See Pitfall #44. Do NOT create a duplicate lead or Discovery Call page for an existing client.

**`external-sales` handling:** When classified as `external-sales`, log `internal-meeting-prep: classify="external-sales" event="..." — executing sales-meeting-prep workflow` and check if `references/sales-meeting-prep.md` exists.

If yes, load and execute it *programmatically* from the webhook. The reference labels itself "Manual Workflow" for user-initiated ad-hoc requests, but the following steps automate reliably:

1. **Company + people research** — web search the company name, key attendees, CEO/founder, Clutch/G2 ratings, portfolio links, recent news.
2. **Create lead in Tasks DB** — Title: `[PO][MKT] Lead - {Name} / {Company} - Discovery Call`, assigned to Organizer, Status: In Progress, Priority: High, Tags: Marketing. Body blocks: Contact Info, Company Info, Meeting Purpose, Actions.
3. **Prepare Discovery Call page** — Invoke `discovery-call-prep`. It owns the canonical template at `${CONFIGURED_SOURCE_URL}`, duplicate checks, native Notion block structure, creation or normalization, and read-back verification. Do not generate the former generic Meeting Details / Agenda / Decision Log layout.
4. **Send tailored briefs to internal attendees** — Company profile, key people, portfolio links, Notion URLs, suggested questions. Deliver via Slack DM (Python fallback if `send_message` tool absent).
5. **Finalize lockfile** — include `resources_created` dict with `lead_page` and `discovery_call_page` URLs.

If the reference does NOT exist, log `internal-meeting-prep: classify="external-sales" event="..." — deferring to sales-meeting-prep (reference not found)`, write the lockfile as done, and stop. Do NOT send a generic DM.

**`sales-sync` context:** Internal sales pipeline discussion. Senior team (typically Organizer, Participant A, Participant D) reviewing leads, opportunities, watchlist clients. All attendees @example.org — NO lead/Discovery Call creation (that's `external-sales` only).

**`sales-sync` confirmed run (2026-07-28, "Sales Sync - Send Outbound", Organizer + Participant A):** The type fires the Projects DB query (Step 4b, ALL statuses incl. Done/Canceled for pipeline history), a Notion `/v1/search` on sales keywords (`outbound`, `pipeline`, `lead`), and `session_search` for recent sales discussions. It does NOT query the per-person Tasks DB by assignee (per Step 4a table — `sales-sync` is excluded). Instead, to surface concrete open work items, run one Tasks DB query sorted by `last_edited_time desc` (page_size 50) and Python-filter titles for sales keywords (`lead|outbound|sales|discovery|pitch|proposal|prospect`) — this yielded the outbound-workflow open items (Finalize Sales Workflow EPIC, Jordan PRD, Batch outreach, Apollo lead form). Both senior attendees get the SAME pipeline-focused DM (they're reviewing the same board together — no need to personalize per-assignee for sales-sync). DM sections used: *Pipeline — Active opportunities* (from Projects DB, In Progress first), *Outbound workflow — open items* (keyword-filtered Tasks query + search hits, with Notion deep-links), *Recent discussions* (session_search snippets — e.g. Jordan inbound-lead Loop 1, Hotline→Jordan handover webhook). GSM-tempfile→positional-arg token pattern (Pitfall #40) delivered both DMs `ok:true` + valid `ts` first try; `send_message` tool was absent.

### Step 3: Resolve Notion user IDs

Before querying tasks, resolve each attendee email to their Notion user ID:

1. Call `GET /v1/users` to list all workspace users. Cache for the run.
2. Match each attendee email against `person.email` in the response.
3. If a user is not found (external, bot, no Notion account), skip them.

### Step 4: Gather context per meeting type

This is where v2 diverges from v1. Instead of just pulling tasks, gather type-appropriate context from multiple sources.

**Context gathering is non-blocking.** If any source fails, log the failure and continue with whatever was gathered. The DM is the critical path — it must go out even if GitHub is down or session_search returns nothing.

#### 4a. Notion Tasks (all types except `external-sales` and `sales-sync`)

For each internal attendee with a Notion user ID, query the Tasks DB. Filter by meeting type:

| Type | Status filter | Sort | Max items |
|---|---|---|---|
| `standup` | In Progress, Review only | Priority desc | 10 |
| `sprint-planning` | All open (not Done, not Archived) | Priority desc, Due asc | 15 |
| `grooming` | Not Started, story_points is null | Due asc | 15 |
| `sprint-demo` | Done, Completed on in last 14 days | Completed on desc | 10 |
| `1:1` | All open, filter to shared projects only | Due asc | 10 |
| `weekly-sync` | In Progress, Review | Priority desc | 10 |
| `retrospective` | All open (overdue focus) + Done in last 14 days | Due asc | 10 |
| `project` | All open for matching project | Priority desc | 15 |
| `generic` | All open | Due asc | 10 |

Also query for overdue items (Due < today, Status not Done/Archived) for types that highlight them: `standup`, `sprint-planning`, `grooming`, `weekly-sync`, `retrospective`, `generic`.

**Standup overdue rationale:** Overdue items are explicit blocker signals — exactly what a standup is meant to surface. A dev with 9 overdue PR tasks needs to explain why in standup. Querying overdue for standup makes the DM a more useful pre-meeting artifact.

**Grooming overdue rationale:** Overdue Not Started items are tasks no one has estimated or started working on past their deadline. These are the highest-leverage items to discuss in grooming — either estimate them, reprioritize them, or kill them. Without the overdue query, the DM only shows unestimated backlog, missing the crucially stale items that need triage.

**Important:** Add ~200ms delay between Notion queries. Write query JSON to `/tmp/` files and use `curl -d @/tmp/file.json` (see Pitfall #6).

**Status options:** Not Started, In Progress, Review, Done, Archived. Open = not Done AND not Archived.

**`page_size` trap for Python-side status filtering:** When filtering by status in Python (e.g. standup's "In Progress, Review only"), the Notion API returns `page_size` results, but the Python filter only sees those. If someone has many Not Started tasks that sort higher (Priority desc), active In Progress tasks get silently truncated. Fix: either increase `page_size` to 50, or add a `"does_not_equal": "Not Started"` filter to the Notion query itself. The latter is preferred for standup types. This applies to all types where the Python code filters to a subset of the returned results (`standup`, `grooming`, `weekly-sync`). For grooming the risk is reversed: tasks that DO have story points (already groomed) may push unestimated tasks off the page when sorting by Due asc. Fix: add `"is_empty": true` on Story Points to the Notion query itself rather than filtering in Python.

#### 4b. Notion Projects DB (for `project`, `sales-sync`, `sprint-planning`, `grooming`, `retrospective`)

Query the Projects DB for project status context:

```json
POST /v1/data_sources/${CONFIGURED_NOTION_ID}/query
{
  "filter": {
    "and": [
      {"property": "Status", "status": {"does_not_equal": "Done"}},
      {"property": "Status", "status": {"does_not_equal": "Canceled"}}
    ]
  },
  "page_size": 50
}
```

For `sales-sync`: include ALL projects (even Done/Canceled) to show pipeline history. Filter by `Client` field for company names.

For `project` type: filter to the specific project matching the title.

#### 4c. Notion Workspace Search (for `1:1`, `project`, `sales-sync`)

When the meeting title contains keywords that might match Notion pages:

```bash
curl -s -X POST "https://api.notion.com/v1/search" \
  -H "Authorization: Bearer $NOTION_API_TOKEN" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"query": "<keyword>", "filter": {"property": "object", "value": "page"}, "page_size": 5}'
```

For `sales-sync`: search for company names, lead names, or "sales"/"pipeline"/"opportunity" keywords.
For `1:1`: search for the meeting topic from the title.
For `project`: search for the project name.

#### 4d. GitHub PRs and Issues (for `standup`, `sprint-planning`, `weekly-sync`, `1:1`, `project`)

Pull relevant GitHub activity:

```bash
# Open PRs across EXAMPLE_ORG (team-wide) — requires being inside a git repo or specifying --repo
gh pr list --repo EXAMPLE_ORG/<repo> --state open --json number,title,author,createdAt,headRefName

# PRs by a specific person (last 48h) — prefer gh search prs (works from any directory)
gh search prs --owner EXAMPLE_ORG --author <github_username> --limit 10 --json number,title,state,repository,createdAt

# Alternative: gh pr list (requires being inside a git repo checkout)
gh pr list --author <github_username> --state all --limit 10 --json number,title,state,headRepository,createdAt

# Issues matching keywords
gh search issues "<keyword>" --owner EXAMPLE_ORG --state open --json number,title,repository
```

**GitHub username mapping:** Check `${HERMES_HOME}/team-roster.yaml` for a `github_username` field. If absent, skip GitHub enrichment for that person (do not guess). The field can be added later.

**Rate limits:** GitHub allows 5000 req/hr. Not a concern.

#### 4e. Session Search (for all types)

Search past agent conversations for relevant context:

```
session_search(query="<person_name> <topic_keywords>", limit=3)
```

Valuable for:
- `1:1` — what did these two people discuss last time?
- `project` — what decisions were made?
- `sales-sync` — recent leads/sales discussions
- Any type — recent blockers or issues

Filter by recency (last 7 days default). Don't dump entire transcripts — use snippets only.

#### 4f. ~~Linear Issues~~ (REMOVED — Example Organization no longer uses Linear for task tracking. All task data is in Notion. This step is kept as a placeholder for reference but should not be executed.)

---

### Step 5: Build per-person Slack DM (type-aware)

Build a personalized DM for each attendee based on the classified meeting type. All DMs share a common header, then diverge.

**Common header (all types):**
```
{type_label}: {title}
{start_time} - {end_time} (ICT)
{Meet: <url> if present}
```

#### Type-specific DM bodies:

**`standup`:**
```
Your current focus:
* {task} [{priority}] — {project}

OVERDUE:
* {task} — Was due: {date}

Recent activity (48h):
* {PR or commit info from GitHub}
* {session snippet if relevant}
```

Include the OVERDUE section only when the attendee has overdue items. Standup is the primary meeting for surfacing blockers — overdue tasks ARE blockers. Omit the section entirely when the list is empty.

Omit the "Recent activity (48h)" section when the attendee has no GitHub PRs within the last 48 hours (see Pitfall #32). An absent section is cleaner than an empty one.

**`sprint-planning`:**
```
Your open items ({count}):
* {task} [{priority}] — Due: {date} — {project}

OVERDUE ({count}):
* {task} — Was due: {date}

Team snapshot:
* {N} total open tasks | {N} PRs in flight
```

**`grooming`:**
```
Backlog items needing estimates ({count}):
* {task} [{priority}] — Due: {date} — {project}

OVERDUE items needing grooming ({count}):
* {task} — Was due: {date} — {project}
```

Include the OVERDUE section only when the attendee has overdue items. Overdue Not Started items are prime grooming candidates — they've sat untriaged past their due date and need refinement attention. See Step 4a for the overdue query logic for grooming.

**`sprint-demo`:**
```
Completed this sprint ({count}):
* {task} — Completed: {date} — {project}

Merged PRs:
* {PR} — {repo}
```

**`retrospective`:**
```
Sprint stats:
* Completed: {N} tasks ({N} story points)
* Slipped: {N} overdue items

What slipped:
* {task} — Was due: {date}, still {status}
```

**`weekly-sync`:**
```
Your in-progress:
* {task} [{priority}] — {project}

Team projects:
* {project} — {status} — {summary}

Active PRs:
* {PR} — {repo}
```

**`1:1`:**
```
{person_a} ↔ {person_b}

Shared context:
* {project or task overlap}
* {recent PR interactions}

Topic: {title keyword search results}
```

**`sales-sync`:**
```
Pipeline — Active opportunities:
* {project} — Client: {client} — Status: {status}

Recently closed:
* {project} — Client: {client} — Completed: {date}

Recent discussions:
* {session_search snippet about leads/sales}
```

**`project`:**
```
Project: {name} — Status: {status}
Client: {client if available}

Open tasks ({count}):
* {task} [{priority}] — {assignee}

Active PRs:
* {PR} — {repo}
```

**`generic`:**
```
Your open tasks ({count}):
* {task} [{priority}] — Due: {date} — {project}

OVERDUE:
* {task} — Was due: {date}

{description if present}
```

**`external-sales`:** No DM sent. Defers to sales-meeting-prep skill (not yet built).

#### DM rules:
- **Slack formatting:** `*bold*`, `<url|text>`, `_italic_`, `* ` bullets. NO standard markdown.
- **List caps follow the type-specific limits in Step 4a.** Sprint-planning and project open-item lists may show up to 15; standup, grooming, sprint-demo, 1:1, weekly-sync, retrospective, and generic lists show up to 10 unless their template explicitly says otherwise. Keep `OVERDUE` lists capped at 10. If more: `_...and {N} more. Check Notion._`. This rule overrides copied runner defaults; do not silently reduce sprint-planning's 15-item cap to the generic 10-item cap.
- **Truncate descriptions** to 200 chars.
- **Skip attendees with zero relevant content** for the meeting type (e.g., in standup, skip people with no In Progress tasks AND no recent GitHub activity).
- **DM length limit:** ~4000 chars practical. If over, progressively re-render with smaller per-section caps; never slice the final mrkdwn string because that can cut through `<url|label>` links. Preserve the header and operational sections (especially `OVERDUE` for standups), add `_...and N more_` receipts, and shorten/omit lower-value project labels before reducing critical content. See `references/slack-dm-length-safe-rendering.md`.
- **Compose DM text as a variable** to pass directly to `send_message(target="slack:<channel>", message=<dm_text>)`.

---

### Step 6: Resolve Slack user IDs and send DMs

For each attendee with content:
# Roster lookups (Slack UID, Slack DM channel): match attendee email DIRECTLY
# against the roster YAML keys. As of June 2026 the roster uses person@example.org
# directly — NO alias needed. If a future roster update changes the key,
# the runtime read will handle it. See Pitfall #26.
routable_email = attendee_email  # match raw webhook email, no alias
# Read ${HERMES_HOME}/team-roster.yaml at runtime to get slack_dm_channel_id

**FIRST — check whether `send_message` is in your tool list before composing any send calls.** In gateway/webhook sessions it is usually present; in some webhook sessions (confirmed 2026-07-27) it is genuinely absent and calling it returns `Tool 'send_message' does not exist`. Do NOT attempt a `send_message` call speculatively to "see if it works" — that wastes a round-trip and you have to redo the composition for the Python fallback anyway. Look at your available tools: if `send_message` is present, use path 5 below; if absent, go straight to the Python Slack API fallback (Pitfall #24) with the GSM-token-into-positional-arg pattern (Pitfall #40).

5. Send via `send_message` tool ONLY: `send_message(target="slack:<channel_id>", message=<dm_text>)`. **This is the ONLY reliable delivery path for DM channels.** The `send_message` tool goes through the gateway's Socket Mode, which delivers to any DM channel without the bot token `chat.postMessage` limitations (see Pitfall #38). Do NOT use the Python Slack API fallback for DM delivery unless `send_message` is genuinely absent from your tool list — even then, consider embedding the prep content in the webhook response instead.
6. Log the outcome for each attendee: sent/failed.

**Send DMs to ALL attendees — never skip anyone based on the webhook delivery target.** The webhook's `deliver` target (e.g., Operator's DM for telemetry) is completely independent of DM delivery. Always send a DM to every attendee's own channel. Resolve each attendee's `slack_dm_channel_id` from `${HERMES_HOME}/team-roster.yaml` (match on email). Do NOT hardcode channel IDs here — the roster is the single source of truth and handles roster changes automatically.

**Use `send_message` tool, NOT `hermes send` CLI.** The `hermes send` CLI has session-level delivery dedup that incorrectly blocks DMs. The `send_message` tool sends directly without dedup.

**After all DMs are sent, the agent's final response (what you output last) will be auto-delivered to the webhook's `deliver` target (Operator's DM) as telemetry.** This is fine — it's the ops log. The actual meeting prep content goes to each attendee via the DMs you just sent. Do NOT skip a DM just because the final response happens to land in the same channel.

**When Slack DMs cannot be sent (Slack token unavailable, GCP Secret Manager timeout, no `send_message` tool):** The final response becomes the primary delivery mechanism. Compose the full per-person prep content as a structured markdown block in your final output. Include headings for each attendee, their tasks, and any overdue items. The webhook's `deliver` target channel receives this as a single ops message — this is the best-effort fallback. Log `internal-meeting-prep: slack-unavailable ...` (see Output section) and set `summary.json` `attendees_notified: []` with a `note`. Do NOT skip the prep entirely just because Slack delivery failed.

If `INTERNAL_MEETING_PREP_DRY_RUN=1`, log the composed DM text instead of sending.

**Never use hardcoded Slack UID tables or state files** — always read from the roster at runtime.

### Step 7: Finalize lockfile

Overwrite the lockfile with:

```json
{"status": "done", "event_id": "...", "title": "...", "meeting_type": "...", "prepped_at": "ISO8601", "attendees_notified": [...], "attendees_skipped": [...], "dry_run": false}
```

Must be written even if no DMs were sent.

---

## Data Source Failure Handling

37. Terminal Token Substitution Volatility. Direct calls to external tools like `curl` via the `terminal` tool often result in shell substitution errors (e.g., 'bad substitution') for environment variables like `$NOTION_API_TOKEN` when embedded in command strings containing complex arguments. **DO NOT** rely on the terminal tool for calls requiring sensitive, templated tokens. Always enforce token loading within self-contained execution environments like Python scripts using the braced syntax workaround: `subprocess.run(['bash', '-c', 'source ${HERMES_HOME}/.env && echo "${NOTION_API_TOKEN}"'])`. This ensures the token is loaded correctly *after* the file creation masking step.
| GitHub API down | Skip PR/commit sections, log warning |
| session_search empty | Skip "recent discussions" section |
| Notion search empty | Skip keyword-match section |
| All sources fail | Send minimal: meeting title, time, attendee list |

**The DM is the critical path. Enrichment is non-blocking.**

---

36. **GitHub search fallback failure.** `gh search prs --owner EXAMPLE_ORG` frequently fails on permission/query errors (Pitfall #22). The fallback—discovering accessible repos via `gh repo list EXAMPLE_ORG` and then iterating with `gh search prs --repo EXAMPLE_ORG/<repo>`—is required for reliable results when using the main org owner filter. This multi-stage mechanism must be implemented in any subordinate scripts.

1. **Notion user ID vs email.** Tasks `Assignee` references Notion user IDs, not emails. Resolve via `GET /v1/users` first. **The working per-attendee filter shape (confirmed 2026-07-31 standup, all 4 attendees returned correct task sets):** `Assignee` is a `people` property, so filter with `{"property": "Assignee", "people": {"contains": "<user_id>"}}` — NOT `equals`, and NOT the email. Combine in an `and` with the status excludes. Full standup filter that worked (In Progress + Review only, priority-sorted):
   ```json
   {"filter": {"and": [
     {"property": "Assignee", "people": {"contains": "<uid>"}},
     {"property": "Status", "status": {"does_not_equal": "Done"}},
     {"property": "Status", "status": {"does_not_equal": "Archived"}},
     {"property": "Status", "status": {"does_not_equal": "Not Started"}}
   ]}, "sorts": [{"property": "Priority", "direction": "descending"}], "page_size": 50}
   ```
   The triple status `does_not_equal` (excluding Not Started at the Notion layer) avoids the `page_size` truncation trap from Pitfall #6 — active tasks can't be pushed off the page by a pile of Not Started items. Overdue query uses `{"property": "Due", "date": {"before": "<today ISO>"}}` alongside the Done/Archived excludes.

2. **Status filter: exclude Done AND Archived.** Five options: Not Started, In Progress, Review, Done, Archived. Open = not Done AND not Archived.

3. **Notion rate limits.** ~3 req/s. Add ~200ms delay between queries.

4. **Slack user resolution — roster first.** Always read from `${HERMES_HOME}/team-roster.yaml`. Never hardcode UIDs.

5. **Bot accounts.** `person@example.org` and `person@example.org` are bot accounts. Both should be skipped in attendee counting, Notion resolution, and DM delivery.

6. **Shell variable expansion + write_file name masking.** Two related problems:

   **Problem 1 — Shell expansion (terminal):** `$NOTION_API_TOKEN` gets mangled inline in shell commands — not just in JSON bodies but also in Authorization headers (`-H "Authorization: Bearer *** will silently send a blank or partial token, returning null/empty results with no error).

   **Problem 2 — write_file name masking (file content):** When you write a Python script via the `write_file` tool, the env var NAME `$NOTION_API_TOKEN` inside a string literal gets masked to `$NOTIO...EN` in the file on disk. This breaks `subprocess.run(['bash', '-c', 'echo "$NOTION_API_TOKEN"'])` because the file contains `$NOTIO...EN` instead of `$NOTION_API_TOKEN`.

   **The braced notation workaround:** Use `"${NOTION_API_TOKEN}"` (curly braces around the name) instead of `"$NOTION_API_TOKEN"`. The braced syntax survives write_file masking on this environment. This was confirmed in session: `"${NOTION_API_TOKEN}"` wrote correctly while `"$NOTION_API_TOKEN"` was corrupted.

   TWO approaches that work:

   **Approach A — Bash script (simple queries):** Write the entire curl command sequence into a bash script (`/tmp/notion-queries.sh`) with `source ${HERMES_HOME}/.env` at the top and `chmod +x`, then execute `bash /tmp/notion-queries.sh`. This applies to ALL Notion API calls — queries, user resolution, page creation.

   **Approach B — Python + requests (complex queries, reliable):** Use Python with `subprocess.run` to source `.env`, then `requests` for API calls. CRITICAL: use braced syntax in the subprocess command to survive write_file masking:

   ```python
   import subprocess, json, time, requests
   # Load token via subprocess (use ${VAR} — braced — not $VAR)
   proc = subprocess.run(
       ['bash', '-c', 'source ${HERMES_HOME}/.env && echo "${NOTION_API_TOKEN}"'],
       capture_output=True, text=True, timeout=10
   )
   token = ${CONFIGURED_SECRET}
   # Also load Slack token the same way
   proc2 = subprocess.run(
       ['bash', '-c', 'source ${HERMES_HOME}/.env && echo "${SLACK_BOT_TOKEN}"'],
       capture_output=True, text=True, timeout=10
   )
   slack_token = ${CONFIGURED_SECRET}
   # Use the loaded tokens
   H = {"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03", "Content-Type": "application/json"}
   r = requests.post(
       f"https://api.notion.com/v1/data_sources/{DS_ID}/query",
       json=payload, headers=H, timeout=15
   )
   ```

   For a complete working example of the combined Notion-queries + Slack-DMs script, see `references/python-prep-script-example.md` in this skill.

7. **Projects DB title property is `Project name`.** Not `title` or `Name`. Use bracket notation.

8. **Notion property names with spaces.** Use `properties["Task name"]`, not `properties.Task_name`.

9. **Empty content lists.** Do NOT send DMs to attendees with zero relevant content for the meeting type.

10. **Webhook payload variations.** Handle missing optional fields. Required: `id`, `title`, `start`, `attendees`.

11. **Timezone handling.** Display in ICT (UTC+7). Webhook uses UTC (`Z` suffix).

12. **Lockfile directory.** `${HERMES_HOME}/workspace/meetings/<event_id>/.lock`. Event ID from webhook `id` field — NOT the Meet slug.

13. **Do NOT send DMs for < 2 human internal attendees.**

14. **`external-sales` vs `sales-sync` distinction.** If ALL attendees are @example.org AND title has "sales" → `sales-sync` (internal pipeline discussion). If ANY attendee is external AND title has sales keywords → `external-sales` (defers to future skill). The attendee domain check is the key differentiator.

15. **GitHub username mapping.** The roster YAML may not have `github_username` for all members. If missing, skip GitHub enrichment for that person rather than guessing. The field can be added to the roster later.

16. **`send_message` tool is the preferred Slack delivery method.** Use `send_message(target="slack:<channel_id>", message=<text>)` to send DMs. Do NOT use `hermes send` CLI. The `send_message` tool sends directly to the specified channel — it is never blocked by webhook delivery target configuration. **Always send DMs to ALL attendees regardless of what the webhook's `deliver` target is.** The webhook deliver target is for_ops telemetry only_, not a constraint on DM delivery.

17. **Gateway restart required after webhook config changes.** Fix: `pkill -f "hermes_cli.main gateway run"` then `hermes gateway run --replace`.

18. **Recurring events.** Directory named by `event_id` (calendar event ID), NOT Meet slug. Recurring events share the same Meet URL — using the slug would block all occurrences after the first.

19. **Non-critical path rule.** GitHub, session_search, Notion search are all enrichment. If any fail, the DM still goes out. Never block the send.

20. **`sales-meeting-prep` placeholder.** External sales meetings with external attendees are detected and logged but NOT processed by this skill. A dedicated `sales-meeting-prep` skill (not yet built) will handle those with web research, company intel, and deeper preparation. If `references/sales-meeting-prep.md` exists, execute it. Otherwise log and stop.

21. **`gh pr list --json` field names.** The `repository` field does not exist in `gh pr list` JSON output — use `headRepository` instead. For `gh search prs`, the field IS `repository`. Check which command you're using and use the correct field name.

22. **`gh pr list` requires a git repo working directory.** Running `gh pr list --author <user>` from a non-git directory (like `${HOME}`) fails with "fatal: not a git repository." Use `gh search prs --owner EXAMPLE_ORG --author <user>` instead — it works from any directory. **However, `--owner EXAMPLE_ORG` requires org-level repo search permissions on the GitHub token. If it fails with `"Invalid search query "author:<gh_user> type:pr user:EXAMPLE_ORG". The listed users and repositories cannot be searched either because the resources do not exist or you do not have permission to view them."`, fall back to one of:**

    - **`gh repo list EXAMPLE_ORG --json name --limit 50`** — discovers all accessible repos in the org (works if the token has org repo read access but NOT org-level search). Then iterate: `gh search prs --repo EXAMPLE_ORG/<repo> --author <user> --limit 5 --json number,title,state,repository,createdAt`. This is the preferred fallback when `--owner` fails — it's the most likely to return actual PRs.
    - **Omit `--owner`** — searches public repos globally (may return zero results if PRs are in private org repos).
    - **`gh pr list` from inside a git checkout** — works for the one repo you have cloned. Requires being in a directory that is inside a git repo.
    - **`gh search prs --repo EXAMPLE_ORG/<known-repo>`** — per-repo search, works if the token has access to that specific repo. Useful when you know which repo the person works on.

23. **`hermes send` auto-skips the webhook origin channel — DO NOT USE `hermes send`.** When this skill runs inside a webhook-triggered session, `hermes send --to slack:<channel_id>` silently skips delivery if the target channel matches the session's origin channel. **This is a `hermes send` bug, not a reason to skip DMs.** Always use the `send_message` tool instead — it bypasses this dedup entirely. If `send_message` is unavailable, use the Python Slack API fallback (Pitfall #24). **Never skip an attendee's DM. Never include someone's private meeting prep in the final response as a "workaround" — that leaks it to the wrong channel.**

24. **`send_message` tool is the PRIMARY path — Python Slack API fallback is unreliable for bot tokens.** If the agent's tool list does not include `send_message` (expected in CLI/ad-hoc sessions — `send_message` is only available when running inside a gateway/webhook session where webhook subscriptions are active), use Python with `SLACK_BOT_TOKEN` from `${HERMES_HOME}/.env` to call `chat.postMessage` directly via `urllib` or `requests`. Compose DM text in a Python dict with `{"channel": "<dm_channel_id>", "text": "<mrkdwn_text>"}`, POST to `https://slack.com/api/chat.postMessage`, and verify `result["ok"]`. Write the script inside the event-scoped lockfile directory (`${HERMES_HOME}/workspace/meetings/<event_id>/send-dms.py`) to avoid temp file collisions between concurrent webhook sessions (see Pitfall #28). Execute with `python3 <path>/send-dms.py`. Log each channel sent/failed. If the token is missing, skip DMs and deliver prep content in the webhook response only.

    **CRITICAL — false-positive notification reporting:** Populate `notify_list` ONLY after confirming `result["ok"]` from the Slack API. Do NOT append to `notify_list` before the API call — if all Slack API calls fail (bot token DM limitation, wrong token, rate limit), the `summary.json` will falsely report all attendees as notified. See Pitfall #38 for why the bot token may silently fail.

25. **Notion API endpoint: use `data_sources`, NOT `databases`.** The correct endpoint for querying Notion databases under the 2025-09-03 API version is `POST /v1/data_sources/{data_source_id}/query`. The endpoint `POST /v1/databases/{database_id}/query` returns `{"object": "error", "message": "Invalid request URL."}`. The Tasks database uses `data_source_id: ${CONFIGURED_NOTION_ID}`; the Projects database uses `data_source_id: ${CONFIGURED_NOTION_ID}`. These IDs are NOT the same as the `database_id`. Always use the `data_sources` endpoint — both user-level and integration-level tokens work with it.

26. **Organizer's email alias — TWO contexts, but as of June 2026 the roster already uses `person@example.org`.** Organizer Example uses `person@example.org` in calendar webhooks. Important: the team roster ALSO uses `person@example.org` (confirmed on 2026-06-22). His Notion workspace email is `person@example.org`. Handle per context:

    - **Roster lookups (Slack UID, Slack DM channel):** check the actual `${HERMES_HOME}/team-roster.yaml` at runtime. As of June 2026 it uses `person@example.org` directly — no alias needed. **Do NOT assume it needs aliasing.** Read the file and match on the raw attendee email. If a future roster update changes the key, the runtime read will handle it.
    - **Notion user resolution (GET /v1/users + person.email match):** Organizer's Notion account is registered under a PERSONAL email — `person@example.org` (display name "Organizer J. Example"), NOT `person@example.org`. `GET /v1/users` returns NO entry for `person@example.org`. If you match only on the webhook email you will find no Notion user for Organizer, skip him, and silently miss all his tasks. **Fix:** after building the `email → user_ids` map from `/v1/users`, alias his account: `if "person@example.org" in users: users["person@example.org"] = users["person@example.org"]`. Confirmed in the 2026-07-24 grooming run — without the alias Organizer showed 0 tasks; with it, 5 backlog + 6 overdue. The authoritative user→ID table is in `references/notion-user-resolution.md`. (Roster/Slack lookups still use `person@example.org` — this alias is Notion-only.)

    Common failure pattern: you resolve Notion users first, find no match for Organizer (because his Notion account is under the proton email), and skip him. Then you query tasks for Organizer and get no data. Always alias `person@example.org → person@example.org` for Notion, and use the raw webhook email for roster.

27. **Multiple Notion accounts per person — query ALL matching IDs.** A single person *can* have multiple Notion workspace accounts under different emails (same person created a second account, or an old email is still present alongside the current one). A flat `{email: user_id}` mapping from `GET /v1/users` will only capture the account matching the attendee webhook email. Tasks assigned to the *other* account will be silently missed.

    **Fix:** After building the email-to-ID map, also build a name-to-IDs map. For each attendee with a Notion match, check if the resolved user name appears under any other email. If the names overlap (strong hint the same person has a second account), query tasks for ALL matching IDs and merge results. A practical heuristic: normalize the name from the webhook attendee's Notion user, then scan all `/v1/users` results for similar names and query each match.

    **At the same time, do NOT assume every attendee has a Notion account (see Pitfall #42).** Some team members (e.g., `person@example.org`) may have zero Notion accounts despite being full team members with Slack and calendar presence.

28. **Temp file collisions between concurrent webhook sessions.** When two webhook-triggered sessions process the same event (stale lock recovery, race condition), both may write to `/tmp/send-dms.py` or `/tmp/notion-queries.sh`. The lockfile Step 0 handles meeting-level dedup, but the `/tmp/` files are shared across sessions. Use process-unique temp file names — either append the event ID (e.g., `/tmp/send-dms-<short_event_id>.py`) or write temp scripts inside the event-scoped lockfile directory (`${HERMES_HOME}/workspace/meetings/<event_id>/script.py`) which is guaranteed unique per occurrence.

29. **Helper script stale roster path.** `scripts/meeting-prep-helper.py` defaults `--roster` to `${HERMES_HOME}/config/team-roster.yaml` but the actual file lives at `${HERMES_HOME}/team-roster.yaml`. If invoked without `--roster`, the script silently skips all Slack channel resolution and produces "no_dm_channel" failures for every attendee. Always pass `--roster ${HERMES_HOME}/team-roster.yaml` or patch the default. (The script has been patched in this update.)

30. **Sibling subagent lockfile warning — verify after write.** When writing the lockfile in Step 0 or Step 7, Hermes may emit a warning like `"was modified by sibling subagent '<session_id>' at <timestamp> — after this agent's last read at <timestamp>. Re-read the file before writing."` This happens when a previous session left a stale lock and the current session overwrites it. The warning is benign if the write succeeded — verify by re-reading the lockfile content immediately after writing. If the content matches what you wrote, proceed. If it doesn't (rare race condition), re-claim the lock and restart from Step 1.

31. **`gh search prs` repository field is an object, not a string.** The `--json repository` output from `gh search prs` returns `{"name": "<repo_name>", "nameWithOwner": "EXAMPLE_ORG/<repo_name>"}`, not a plain string. When formatting DM text, use `repository.nameWithOwner` for display (e.g., `_EXAMPLE_ORG/hermes-agent-config_`). This is different from `gh pr list` which uses `headRepository` as a similar object. Do NOT use `repository` as a string — it will render as `[object Object]` in the DM.

32. **Standup "Recent activity" section — omit when empty, AND apply the 48h window CLIENT-SIDE.** For the standup DM template, the "Recent activity (48h)" section should only appear when the attendee has GitHub PRs within the last 48 hours. If `gh search prs` returns no results within that window, omit the section entirely. Do NOT show "No recent PRs" or an empty bullet list — an absent section is cleaner than an empty one. This applies to all meeting types that include a "Recent activity" section.

    **CRITICAL — the 48h window is a Python filter, NOT something `gh` does for you.** `gh search prs --owner EXAMPLE_ORG --author <gh> --state open` returns ALL currently-open PRs regardless of age — a PR opened 3 weeks ago is still "open" and comes back in the result set. If you drop the raw `gh` results straight into the "Recent activity (48h)" section, you'll show weeks-old open PRs as "recent activity," which is wrong and misleading in a standup. ALWAYS re-filter in Python on `createdAt` before rendering:
    ```python
    now = datetime.now(timezone.utc)
    def recent(created):
        t = datetime.fromisoformat(created.replace("Z","+00:00"))
        return (now - t).total_seconds() <= 48*3600
    prs = [p for p in raw_prs if recent(p.get("createdAt",""))]
    ```
    Confirmed 2026-08-04 standup: the raw `gh` query returned 10 open PRs for Organizer, 1 for Operator, 2 for Participant C — but after the 48h `createdAt` filter only Participant C's PR #12 (aeostars, free-trial copy) survived and rendered; Organizer's and Operator's older open PRs were correctly dropped, so their DMs omitted the section entirely per the empty-omit rule above. Without the client-side filter, all three would have shown stale PRs as "recent."

33. **`gh search prs` does NOT support `-s` shorthand, AND `--state all` is invalid.** Two separate issues here:

    - **No `-s` shorthand:** Unlike many gh commands that accept `-s` as shorthand for `--state` (e.g. `gh pr list -s open`), `gh search prs` has NO `-s` shorthand flag. Using `-s open` produces `unknown shorthand flag: 's' in -s` and returns zero results.
    - **`--state` only accepts `{open|closed}`:** The `--state` flag on `gh search prs` does NOT accept `all` as a value. Using `--state all` produces `invalid argument "all" for "--state" flag: valid values are {open|closed}`. Compare with `gh pr list` where `--state all` works.

    **Fix:** Query open PRs only (`--state open`) or omit the `--state` flag entirely (open is the default). For standup prep, open PRs are the relevant signal anyway — closed PRs from the last 48h don't help the team surface blockers. If you need both open and closed (`--state` is useless here), run two separate queries or switch to `gh pr list` which supports `--state all`.

34. **`prepping_since` must use `datetime.now()`, NOT meeting start minus window.** The Step 0 lockfile example shows `"prepping_since": "<ISO8601>"` — that ISO8601 timestamp MUST be the actual system time at the moment of claiming the lock, NOT the calculated meeting prep trigger time (`meeting.start - INTERNAL_MEETING_PREP_WINDOW_MIN`). Writing a future timestamp causes a race condition: if another session checks the lock at a system time before the written `prepping_since`, the calculated age will be negative or zero, making it look like the lock was just claimed (rule #3) — so the second session correctly backs off. But if clock drift pushes the second session's system time past `prepping_since` (e.g. lock written as `02:30Z` but system clock reads `02:22Z`), the second session sees a 7+ minute age (actual real time elapsed: ~0 minutes) and incorrectly treats the lock as stale. **Fix:** always use `datetime.now(timezone.utc).isoformat()` for `prepping_since`. Never derive it from the meeting start time.

35. **Step 7: write both `.lock` and `summary.json`.** The `.lock` file is the mutex for Step 0 dedup (minimal JSON, single line). A companion `summary.json` in the same directory with indented JSON is useful for human debugging, post-mortems, and downstream skills that may want to read prep results from disk. Write `.lock` first (the dedup contract), then `summary.json` (the debug artifact). Both go in the event-scoped directory.

36. **Counting queries require adequate `page_size`.** If you write a helper like `get_team_open_tasks()` that queries task counts aggregate, use `page_size: 100` (or the actual count you need). Using `page_size: 1` followed by `len(results)` returns the number of results, but Notion caps results at the `page_size` — so you'll always get 1 (or `page_size`) back, not the real count. The only reliable count is `len(results)` with a `page_size` larger than the expected total. If you need an exact count, set `page_size: 100` and accept that it returns at most 100. For team-level aggregates in sprint-planning DMs, this is sufficient — if you see counts > 90, just note "90+" in the DM.

38. **Bot token `chat.postMessage` silent DM failure on first contact.** When the Python fallback script (Pitfall #24) uses `SLACK_BOT_TOKEN` (`xoxb-*`) to call `chat.postMessage` for a DM channel, Slack returns `ok: true` even when the message is silently dropped. This happens when the bot has never been added to (or had any message history with) the target user's DM conversation. Bot tokens cannot initiate DM conversations — the user must message the bot first. **Socket Mode delivery (gateway webhook response, `send_message` tool) does not have this limitation** because it rides a persistent WebSocket connection with the user's already-open DM session.

    **Detection:** The only way to detect this is to look beyond `ok: true`. Check for these signs of silent failure:
    - `ts` (message timestamp) missing from the API response
    - `noop: true` present in the response
    - A `warning` field like `"message_ts_missing"` in the response

    **Fix:**
    - **ALWAYS prefer `send_message` tool** over the Python Slack API fallback. The `send_message` tool goes through the gateway's Socket Mode, which reliably delivers to any DM channel the bot has access to.
    - If Python fallback is the only option (no `send_message` tool in the agent's tool list), ensure each target user has messaged the bot at least once before the automated prep runs.
    - If a DM fails silently (API returns `ok: true` but the user reports no message), escalate to using `send_message` tool or embed the prep content in the gateway's webhook delivery response instead.
    - **Never trust `ok: true` alone.** Parse the `ts` field: if `ts` is missing from the response, log as FAIL even if `ok` is `true`.

39. **Discovery Call database pre-flight check is mandatory — never assume the DB is shared.** The target Discovery Calls database (`${CONFIGURED_NOTION_ID}`) must be pre-flight checked before writing. Query `POST /v1/data_sources/{db_id}/query` with `page_size: 1`. A 404 means the database hasn't been shared with the Meeting Bot CoS integration — fall back to creating a standalone child page under Example Organization Organization (`${CONFIGURED_NOTION_ID}`). A 200 means it's shared and you should write the entry there. Do NOT hardcode either path — always check at runtime, because the share state can change (an admin may share it without telling you). The two-option logic is documented in `references/sales-meeting-prep.md` Step 5.

40. **SLACK_BOT_TOKEN may be loaded from GCP Secret Manager, not .env.** The `.env` file may have `SLACK_BOT_TOKEN=*** (redacted placeholder) with a comment like "# now loaded from Google Secret Manager (CONFIGURED_SLACK_SECRET)". The braced syntax `${SLACK_BOT_TOKEN}` will return `***` which is not a valid token — any Python script using it will fail the `len(slack_token) < 10` check. Always use a three-stage approach:

    ```python
    # Stage 1: Try .env first
    proc = subprocess.run(
        ['bash', '-c', 'source ${HERMES_HOME}/.env && echo "${SLACK_BOT_TOKEN}"'],
        capture_output=True, text=True, timeout=10
    )
    candidate = proc.stdout.strip()
    slack_token = None
    if candidate and candidate != "***" and candidate != "":
        slack_token = candidate
    else:
        # Stage 2: Fallback — GCP Secret Manager (with short timeout)
        try:
            proc2 = subprocess.run(
                ['gcloud', 'secrets', 'versions', 'access', 'latest',
                 '--secret=CONFIGURED_SLACK_SECRET', '--project=CONFIGURED_CLOUD_PROJECT'],
                capture_output=True, text=True, timeout=8  # short timeout — don't hang
            )
            if proc2.returncode == 0:
                slack_token = ${CONFIGURED_SECRET}
            else:
                print(f"  GCP Secret Manager returned: {proc2.stderr.strip()}")
        except subprocess.TimeoutExpired:
            print("  GCP Secret Manager timed out (8s)")
        except FileNotFoundError:
            print("  gcloud binary not found")
    ```

    **Stage 3 — when both fail:** If `slack_token` is still None or empty after Stage 2, skip Slack DMs entirely and deliver the prep content as the webhook response (final agent output). The webhook's `deliver` target receives this as telemetry — this is the best-effort fallback. Do NOT let missing Slack tokens block the prep from going out. See Pitfall #41 for the full escalation.

    This three-stage pattern should be used in any Python script that needs `SLACK_BOT_TOKEN`. The timeout on Stage 2 is critical — GCP Secret Manager calls can hang indefinitely.

    **Detection:** Before writing any Slack-delivery script, check `grep SLACK_BOT_TOKEN ${HERMES_HOME}/.env`. If the value is `***`, go directly to Stage 2 (skip Stage 1). If the secret name changes (the project or secret key may be renamed), update the `--secret` parameter accordingly. The GCP project is `CONFIGURED_CLOUD_PROJECT`.

    See `references/python-prep-script-example.md` for the updated template with this pattern built in.

    **NOTION_API_TOKEN is ALSO in GSM (secret `hermes-notion-api-key`, same project `CONFIGURED_CLOUD_PROJECT`), not usable from `.env`.** As of 2026-07-27 both `NOTION_API_TOKEN` and `SLACK_BOT_TOKEN` in `${HERMES_HOME}/.env` are redacted `***` with a "now loaded from Google Secret Manager" comment. Use the same GSM-into-mode-600-tempfile → positional-arg pattern for Notion: `gcloud secrets versions access latest --secret=hermes-notion-api-key --project=CONFIGURED_CLOUD_PROJECT > .ntok` then `python3 gather.py "$(cat .ntok)"`, reading `TOKEN = sys.argv[1].strip()`. Confirmed working 2026-07-27 (50-char token). Delete `.ntok` immediately after.

    **Cleanest working pattern (confirmed 2026-07-23, GSM reachable, no `send_message` tool):** Do NOT embed the token in the Python script body at all — write_file masks token-looking string literals AND `xoxb-…` tokens aren't in the terminal subshell env anyway. Instead:
    1. Fetch the token from GSM in the `terminal` tool into a mode-600 temp file, then pass it as a positional arg to the send script:
       ```bash
       cd ${HERMES_HOME}/workspace/meetings/<event_id>
       umask 077
       timeout 12 gcloud secrets versions access latest \
         --secret=CONFIGURED_SLACK_SECRET --project=CONFIGURED_CLOUD_PROJECT > .tok 2>/dev/null
       python3 send.py "$(cat .tok)"; rm -f .tok
       ```
    2. In `send.py`, read `TOKEN = sys.argv[1].strip()` — no env var, no braced-syntax dance, nothing for write_file to mangle.
    3. This one call delivered all 4 standup DMs (`ok:true` + valid `ts` each). It is simpler and more reliable than the three-stage in-Python token load when GSM works. Verify `ts` per Pitfall #38, delete `.tok` immediately after.

    **Corroborated again 2026-07-27 (sprint-planning, `send_message` tool absent):** Same pattern — `send.py` reading `TOKEN = sys.argv[1].strip()`, GSM fetched with `timeout 12` into a mode-600 `.tok`, passed as positional arg — delivered all 4 DMs (`ok:true` + valid `ts` each) on the first attempt. GSM has now been reachable in two independent sessions (2026-07-23, 2026-07-27); the 2026-07-17 timeout appears to have been a one-off runtime auth issue, not a standing condition. Confirms: try GSM once, don't preemptively skip it.

    **Corroborated a THIRD time 2026-07-28 (standup, `send_message` tool absent) — and confirmed the pattern works for BOTH tokens in ONE flow.** Fetched Notion AND Slack tokens back-to-back into two mode-600 tempfiles in a single `terminal` call:
    ```bash
    cd ${HERMES_HOME}/workspace/meetings/<event_id>; umask 077
    timeout 12 gcloud secrets versions access latest --secret=hermes-notion-api-key --project=CONFIGURED_CLOUD_PROJECT > .ntok 2>.nerr
    timeout 12 gcloud secrets versions access latest --secret=CONFIGURED_SLACK_SECRET --project=CONFIGURED_CLOUD_PROJECT > .stok 2>.serr
    ```
    Then `python3 gather.py "$(cat .ntok)"` (Notion queries) and `python3 send.py "$(cat .stok)" gather_out.json prs_out.json` (Slack send). Notion token = 50 chars `ntn_`, Slack token = 59 chars `xoxb-`; both `rc=0`. All 4 standup DMs delivered `ok:true` + valid `ts` on the first attempt. GSM has now been reachable in THREE consecutive independent sessions (07-23, 07-27, 07-28) — treat GSM-into-tempfile → positional-arg as the DEFAULT delivery path when `send_message` is absent, not a fallback to agonize over. Delete all token tempfiles immediately after (a single `rm -f .ntok .stok .nerr .serr` at finalize).

    **Pre-send dry-run verification technique (2026-07-28):** To preview composed DM text WITHOUT sending, exec the send script's builder functions with the `main()` call stripped, then call `build_dm(...)` per attendee and print. Pattern: `src = open("send.py").read(); exec(src.replace("\nmain()\n", "\n"), ns); print(ns["build_dm"](email, focus, overdue, prs))`. This catches formatting/truncation/skip-logic bugs (e.g. verifying Participant D is correctly skipped, "+N more" truncation renders, priority re-sort put High items first) before any live Slack API call. Cheap insurance on a from-scratch script.

    **PREFERRED dry-run technique — bake a `--dry` positional flag into `send.py` (confirmed 2026-07-29, cleaner than the `exec()` hack above).** Instead of `exec()`-ing the script body with `main()` stripped (fragile — depends on the exact `\nmain()\n` string, and re-runs module-level code), give the send script a `DRY = len(sys.argv) > 3 and sys.argv[3] == "--dry"` flag near the top and branch inside the per-attendee loop: if `DRY`, `print` the composed DM + resolved channel and `continue` instead of calling the Slack API. Invoke with a dummy token: `python3 send.py "DRYTOKEN" gather_out.json --dry`. This exercises the EXACT production `build_dm` + skip-logic + truncation code path (no re-implementation, no string surgery), prints every DM for inspection, and touches no network. Then the real send is the identical command minus `--dry` with the live token: `python3 send.py "$(cat .stok)" gather_out.json`. Verified 2026-07-29 standup: dry run confirmed Organizer's 10-focus/6-overdue split, Operator's "+14 more" truncation, High-priority re-sort to top, and Participant D's correct skip — all before the live send delivered 4/4 DMs `ok:true`.

    **Corroborated a FOURTH time 2026-07-29 (standup, `send_message` tool absent).** Same dual-tempfile pattern (`.ntok` 50-char `ntn_`, `.stok` 59-char `xoxb-`, both `rc=0` via `timeout 12`), `gather.py "$(cat .ntok)"` → `send.py "$(cat .stok)" gather_out.json`, all 4 standup DMs `ok:true` + valid `ts` first attempt. GSM now reachable in FOUR consecutive independent sessions (07-23, 07-27, 07-28, 07-29). The GSM-into-tempfile → positional-arg flow is the settled default when `send_message` is absent — no agonizing, no preemptive skip.

41. **GCP Secret Manager timeout — escalate to webhook response delivery.** When Stage 2 of the Slack token loading (Pitfall #40) times out or fails, the Slack token is genuinely unreachable — not just redacted or in a different location. This is a distinct failure mode from the bot token DM limitations (Pitfall #38) because here you never got a token at all.

    **Signs of this failure:**
    - `gcloud secrets versions access` consistently times out (even with 8s timeout)
    - `gcloud auth application-default print-access-token` also times out
    - The `subprocess.TimeoutExpired` exception fires repeatedly
    - Secret Manager returns a permanent setup/policy error such as `PERMISSION_DENIED` with `reason: BILLING_DISABLED`

    **Permanent setup/policy errors are not timeout failures.** Inspect stderr and the shell return code after the first token fetch. For `BILLING_DISABLED`, IAM denial, missing API enablement, or an explicit credential error, do not retry with a longer timeout and do not attempt the second secret: waiting cannot repair policy state. Finalize `.lock` and `summary.json` with `Mapping_Confidence=Unknown`, include only the meeting header/time/join link/attendee list in webhook telemetry, and record the exact remediation URL or setup action from stderr without exposing secret values. Always remove any partial `.ntok`, `.stok`, `.nerr`, and `.serr` files before finalizing, including when the first fetch fails under `set -e`.

    **Why it happens:** gcloud authentication may depend on a credentials daemon (gcloud auth login, ADC, workload identity) that is untethered in this Hermes gateway runtime. The command hangs because it blocks waiting for an authentication flow that never completes. This is NOT a transient rate-limit issue — retrying with longer timeouts only extends the delay.

    **Correct response — do NOT loop or retry:**
    1. Log: `internal-meeting-prep: slack-unavailable event="<title>" type="<type>" delivered="webhook-response" reason="gcp-secret-manager-timeout"`
    2. Skip ALL Slack DMs. Do NOT fall back to a hardcoded token, do NOT attempt `send_message` as a workaround (it will also be unavailable in this context).
    3. Set `summary.json` `attendees_notified: []` and `attendees_skipped: [all attendee emails]` with a `note` explaining why.
    4. Deliver the full per-person prep content in the agent's final response (auto-delivered to the webhook's `deliver` target as telemetry).
    5. Finalize the lockfile normally.

    **Prevention:** Stage 2 (GSM) is NOT reliably broken — it was reachable and returned a valid `xoxb-` token in the 2026-07-23 session (a single `gcloud secrets versions access` with a 12s `timeout` succeeded immediately). Do NOT preemptively skip Stage 2 based on the 2026-07-17 failure below. Try GSM once with a short timeout (8–12s); only escalate to webhook-response delivery if it actually times out or errors. Do NOT retry Stage 2 more than once.

    **GSM client prerequisite:** Before fetching either token, check that `gcloud` is installed (`command -v gcloud`). If absent, do not execute multiple doomed token fetches. Restore the runtime prerequisite by installing the Google Cloud CLI using Google's supported package instructions, or arrange platform-native injection of the two secrets into the process; then rerun the event only after resetting its failed/done lock deliberately. During the blocked run, finalize `.lock` and `summary.json` with `Mapping_Confidence=Unknown` and emit only the meeting header, time, join link, and attendee list through the webhook response. Do not claim or synthesize personalized task/PR sections when Notion data was never retrieved. This captures the remediation path rather than treating a missing binary as a permanent platform limitation.

    **Refinement (confirmed 2026-08-03 weekly-sync):** distinguish a hard `timeout`-command kill (shell `rc=124`, i.e. GSM was just SLOW to respond) from a real auth error (`rc≠0` with an ADC/permission message on stderr). On a `rc=124` timeout, ONE retry with a LONGER timeout (e.g. `timeout 25`) is worth it before escalating — the 08-03 Notion-token fetch hit `rc=124` at `timeout 12`, then returned a valid 50-char `ntn_` token cleanly at `timeout 25`. This is the ONE permitted retry (still "don't retry more than once"): first attempt short, second attempt longer, then escalate. If stderr shows an auth/credentials error rather than a bare timeout, do NOT retry — escalate immediately (that's the 2026-07-17 untethered-gcloud case, unfixable by waiting).

    **When this happened (session 2026-07-17):** GCP Secret Manager timed out across 3 independent attempts (subprocess.run with 8s, 15s, and terminal-based 20s timeouts). The root cause was not transient — gcloud auth was never configured for the Hermes runtime session. The fix was to deliver the grooming prep content via the webhook response, which the team received as a single DM from the webhook deliver target channel.

42. **Team member has no Notion account — skip them gracefully, do not crash.** Not every team member has a Notion account. `person@example.org` (Participant D) has no Notion user in the workspace — `GET /v1/users` returns no entry for her. Other team members may also lack accounts. When a team member has no Notion account:

    - **Do NOT crash** or abort the script. Log `No Notion account, skipping` and continue.
    - **Do NOT query the Tasks DB** for that person — there's no user ID to filter by, and any query without a user filter will return all tasks (wrong).
    - **Do NOT send a DM** for that person if they have zero content from other sources (no GitHub PRs, no session search hits). See Step 5 DM rules: "Skip attendees with zero relevant content."
    - **If the person has GitHub activity** (standup types) or other enrichment content, still send a DM — just without the Notion task section. The DM should note that task data is unavailable.
    - **Record in `attendees_skipped`** in the summary with the reason.
    - **Keep the Notion user cache current** (see `references/notion-user-resolution.md`). The cached table goes stale — people join, leave, or change emails. Re-verify `GET /v1/users` if the cached table is more than 7 days old.

    **Detection:** After calling `GET /v1/users`, build an `email → user_id` dict. If a team member's email is not in the dict, they have no Notion account. Do NOT fall back to name-matching or guessing.

45. **Attendee present + routable in Notion but MISSING from `team-roster.yaml` — the inverse of #42. Detect it, flag it, don't silently drop them.** Pitfall #42 covers "has no Notion account." The opposite gap is just as real: an attendee who HAS a Notion account (so they show up in the gather step) but has NO entry in `${HERMES_HOME}/team-roster.yaml`, so there is no `slack_dm_channel_id.to_john` to deliver to. They get silently skipped and never receive their prep. Confirmed 2026-07-30, 2026-07-31, 2026-08-04 AND 2026-08-05 standups: `person@example.org` was a standup attendee with a Notion account but absent from the roster — correctly skipped for DM (`reason: "no_dm_channel (not in roster)"`), but the absence is a data gap, not a permanent condition. On 07-30/07-31 he had 0 In-Progress/Review tasks (so no content anyway), but **on 2026-08-04 he had REAL content — 5 focus items + 2 overdue TARA tasks (rate-limiting + CI/CD DNS cutover)** — a fully-composed DM that could NOT be delivered because there's no `to_john` channel. **Corroborated AGAIN 2026-08-05: 4 focus + 1 overdue (`[BE][TARA] Waitlist rate-limiting`, due 08-03) — again fully composed, again undeliverable.** So the "no content anyway" softener no longer applies: the gap is now actively costing Participant B his prep on consecutive runs. **This is a STANDING gap across ≥4 consecutive standups** — Participant B (Backend/Smart Contract Eng, Slack UID `CONFIGURED_SLACK_ID`) still has no roster entry because his Meeting Bot-bot DM channel has never been observed. Do NOT re-derive this from scratch each run: skip his DM, record the gap, flag it once — but the fix is blocked until Participant B DMs Meeting Bot's bot at least once (only then is a real `to_john` channel ID observable). Do NOT guess/hardcode the channel ID from his Slack UID.

    **Handling:**
    - Skip the DM (there's genuinely no channel to send to) and record in `attendees_skipped` with `reason: "no_dm_channel (not in roster)"` — keep this distinct from the `no_notion_account` reason so the two gaps are separable in `summary.json`.
    - **Actively flag the roster gap in the final response.** A new-hire attendee who joined recently (e.g. Participant B joined officially the prior Wednesday per session history) is the most likely cause — they land on the calendar and in Notion before someone adds them to the roster. Name them, note they're a recurring attendee for this meeting type, and offer to add a stub roster entry (`email`, `name`, `slack_dm_channel_id.to_john`, optional `github_username`). Do NOT guess or hardcode a DM channel ID — the bot must have OBSERVED a DM channel first (see the roster YAML header comment: a bare/guessed ID is the old wrong format).
    - This is a to-the-user callout, not an auto-fix: the roster is the single source of truth and editing it needs the actual observed channel ID, which only exists once the person has DMed the bot.

43. **Ad-hoc Notion query scripts must apply the type-specific sort from Step 4a's table, not just default to `Due asc`.** When writing a fresh Python/curl script for a manual or webhook-triggered run instead of using `scripts/meeting-prep-helper.py`, it's easy to reuse a generic `{"property": "Due", "direction": "ascending"}` sort for every query and forget that the Step 4a table specifies **`Priority desc`** for `standup`, `sprint-planning`, `weekly-sync`. This matters a lot in practice: most open tasks have `Due: null`, so sorting by Due leaves the result set in Notion's default (roughly creation) order — priority is NOT reflected. If you then truncate to "top 10" for the DM without re-sorting in Python, you can silently drop high-priority items and keep low-priority filler.

    **Fix:** Either (a) check for and prefer `scripts/meeting-prep-helper.py` first — it's the maintained implementation and should already encode the correct per-type sort — or (b) if writing an ad-hoc script, explicitly set `"sorts": [{"property": "Priority", "direction": "descending"}]` (or whatever the table specifies for that meeting type) in the Notion query payload, or at minimum re-sort the Python-side result list by priority before truncating. Verified in a 2026-07-22 standup run: a from-scratch script sorted by Due asc, and the top-10 truncation for a person with 21 open tasks had to be manually priority-corrected when composing the DM text — it worked out by luck, not by the script's logic. Don't rely on luck next time.

    **CRITICAL — Notion `select` sort is ALPHABETICAL, not by severity.** Priority is a `select` property with options {High, Medium, Low}. A Notion-side `"sorts": [{"property": "Priority", "direction": "descending"}]` sorts the option *names* alphabetically, so descending yields **Medium → Low → High** (M > L > H alphabetically) — i.e. High tasks sink to the BOTTOM, the exact opposite of what you want. Do NOT trust the Notion-side Priority sort to surface High-priority items. Instead ALWAYS re-sort in Python with an explicit severity map before the top-10 truncation:
    ```python
    PRI_ORDER = {"High": 0, "Medium": 1, "Med": 1, "Low": 2, "": 3, None: 3}  # "" / None = no priority -> last
    focus = sorted(items, key=lambda t: PRI_ORDER.get(t.get("priority",""), 3))
    ```
    **The Priority `select` has DIRTY option values — include the variants in the map.** Real Tasks-DB data is NOT limited to the clean `{High, Medium, Low}` set. Confirmed 2026-08-03 sprint-planning: multiple attendees had a `"Med"` value sitting alongside `"Medium"` (Organizer's board had both). If `PRI_ORDER` only maps `"Medium"`, a `"Med"` task falls through to the `.get(..., 3)` default and sinks to the bottom as if it had no priority — wrong. Always map the variant `"Med": 1` (same bucket as Medium) AND `None: 3` (Notion returns `None`, not `""`, when the select is empty). When formatting the DM label, also normalize `"Med"` → `"Medium"` for display so the `[Med]` tag doesn't leak into the message. If you ever see an unmapped priority string in the data, add it to the map rather than letting the default swallow it.\n\n    Confirmed in the 2026-07-24 standup run: a Notion `Priority desc` sort was applied but the Python severity re-sort was what actually put High items at the top of a 24-task DM (top-10 shown, "+14 more"). The Notion-side sort was cosmetic/wrong; the Python re-sort was load-bearing. This applies to any `select`-typed sort field — always map to a numeric severity in Python.

    **`grooming` also needs the Python priority re-sort — not just standup.** Grooming's Step 4a query sorts by `Due asc`, but most `Not Started` backlog items have `Due: null`, so they land in Notion's default order with priority NOT reflected. Before the top-10 truncation, re-sort the backlog list in Python with the `PRI_ORDER` severity map (tie-break on due date). Confirmed 2026-07-31 grooming run ("Demo & Backlog Refinement"): Organizer had 22 backlog items, Operator 16 — the Python re-sort surfaced High-priority items (DNS/TXT record, Jordan email provisioning, Hotline deploy) to the top of both DMs before "+N more" truncation. Without it, High items would have been buried below Medium/Low filler in the shown 10.

44. **External attendee + Organizer ≠ `external-sales`. Distinguish NEW lead from EXISTING client engagement before creating any Notion resources.** Rule 1 (`external-sales`) has THREE conjunctive conditions: (a) sales/partnership/lead **keywords in the TITLE**, (b) ≥1 external (person@example.org) attendee, (c) Organizer present. All three must hold. A recurring weekly/project sync with an external client whose title has NO sales keyword (e.g. "Example OrganizationXExampleAnalytics_Weekly Sync") does NOT match Rule 1 — the presence of an external attendee + Organizer is a false trigger. It falls through to Rule 9 (`weekly-sync`, "sync"/"weekly") or Rule 10 (`project`).

    **Why this matters — the expensive mistake it prevents:** the `external-sales` handler CREATES a new Tasks-DB lead + a Discovery Call page. Running that path for an *already-onboarded* client duplicates records and pollutes the pipeline. Before ever executing the external-sales workflow, verify the client is genuinely NEW:
    - `session_search` the company/person name — a prior discovery call or recurring prep means existing engagement.
    - Check the Projects DB (Step 4b) for a matching project name — an active/in-progress project (e.g. "Example Analytics (Replit)") means existing engagement.
    - Check for a prior `[PO][MKT] Lead - .../<Company> - Discovery Call` task — if it exists, the lead was already created; do NOT make another.

    **Correct handling for an existing-client external sync:** treat as `weekly-sync` (or `project`). Gather the normal per-attendee task/PR context, and ENRICH the DMs with the external party's background pulled from session history (company profile, key contact, what the project is) plus the Projects DB status line. Do NOT create a lead or Discovery Call page. Skip the external attendee for DMs (no @example.org roster/Notion entry) but name them in the internal attendees' context header so the team knows who's on the call. Confirmed 2026-07-27: "Example OrganizationXExampleAnalytics_Weekly Sync" (External Participant Singh / ExampleClient Digital, external) + Organizer → correctly classified `weekly-sync`, enriched with the June-16 discovery-call research from session history, no duplicate lead created.

    **`weekly-sync` confirmed end-to-end run (2026-08-03, same "Example OrganizationXExampleAnalytics_Weekly Sync", 3 routable attendees: Participant A/Organizer/Operator + external External Participant + john.ai bot).** Settled reference run. Classify=`weekly-sync` (Rule 9; external+Organizer is the Rule-1 false trigger — title has no sales keyword). Existing-engagement check passed via `session_search` (prior 07-27 run) AND Projects DB showing `Example Analytics (Replit)` = In Progress → no lead/Discovery Call created. Per-attendee Tasks query = In Progress + Review only (triple `does_not_equal` incl. Not Started, `page_size 50`, Pitfall #1 shape) PLUS overdue query (`Due < today`). Counts: Participant A 2 / Organizer 13 in-prog + 7 overdue / Operator 23 in-prog. Organizer's proton alias (Pitfall #26) resolved his tasks. Projects DB = 13 active for the team-snapshot line. GitHub PRs (Step 4d) `gh search prs --owner EXAMPLE_ORG --author <gh>` clean this run (Aorganizerin 5, operator-example 1; Participant A no `github_username` → skipped). DM body: Example Analytics context header (ExampleClient/External Participant, Leeds UK, Replit — framed as recurring project sync NOT sales), *Your in-progress / review (N)* (Python `PRI_ORDER` re-sort incl. `Med`→Medium, 10 shown + "+N more"), *OVERDUE (N)*, *Active PRs*, *Team snapshot*. `send_message` ABSENT → dual-tempfile GSM → positional-arg (Pitfall #40) with `--dry` preview first; all 3 DMs `ok:true` + valid `ts` first try. External External Participant correctly skipped (no roster/Notion entry), named in header. This is the settled weekly-sync reference run.

## Output

Log lines:

```
internal-meeting-prep: classify="<type>" event="<title>" attendees=<n> notified=<n> skipped=<n>
internal-meeting-prep: classify="external-sales" event="<title>" — executing sales-meeting-prep workflow
internal-meeting-prep: classify="external-sales" event="<title>" — deferring to sales-meeting-prep (reference not found)
internal-meeting-prep: dry-run event="<title>" type="<type>" would-notify=<n>
internal-meeting-prep: slack-unavailable event="<title>" type="<type>" delivered="webhook-response" reason="<gcp-secret-manager-timeout|env-redacted|no-token-available>"
internal-meeting-prep: skip event="<title>" reason="only-1-internal-attendee"
internal-meeting-prep: skip event="<title>" reason="already-prepped"
```

`deliver: slack-dms` — the skill sends DMs directly via `send_message` tool (or Python Slack API fallback if the tool is not available). Never `hermes send`.

## License

Example Organization LLC internal tool. Use within crew scope. No external sharing.
