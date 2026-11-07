# Public runtime adapter for the preserved meeting workflow

This file resolves the deployment-specific portions of the original skill. Read it first. The SKILL.md retains the real phase ordering, decisions, locks, evidence, retry/recovery, context, notes, organizer review and task-resolution workflow. It is not a replacement with a one-paragraph demo.

## Exact routing

- `scheduler/run.js` is the port of the original JS Calendar scheduler, with original prep/join/finish windows, deferred finalization, dispatch leases, executor receipts and bounded recovery.
- **Start/join goes directly to authenticated self-hosted Vexa** through `scheduler/vexa-lifecycle-worker.py` and `meeting-auto-join/scripts/start-vexa-context-canary.py`. Agent `meet_join`, legacy Playwright spawn and SSH/container fallbacks are not used in this public Vexa-only profile.
- **Prep and post-meeting composition/review go to the original signed Hermes webhook interface**. No local `hermes chat` replacement. Prep invokes `internal-meeting-prep`; finish invokes `meeting-finalize` and the downstream skills.
- Vexa stop/status calls are provider operations. Signed completion bridge downloads the captured signal into the exact event directory. Original deterministic post-call worker runs batch/canonical/readability processing before the finish agent runs.

## Resolve configuration, never execute placeholders

`HERMES_HOME` is the attendee's isolated `private/hermes` directory. Skills are installed under `$HERMES_HOME/skills/meeting/<skill>/`; scheduler helpers under `$HERMES_HOME/scripts/meeting-trigger/`. Resolve `${HERMES_HOME}`, `${HERMES_SOURCE}`, configured IDs, URLs and domain values from that local environment. The canonical meeting root is `$HERMES_HOME/workspace/meetings`, not a guessed HOME path.

Private settings are supplied through `private/config.json`, `.env` and generated profile env; never in prompts or committed files. `NOTION_NOTES_DATA_SOURCE_ID`, `NOTION_TASKS_DATA_SOURCE_ID`, `NOTION_PROJECTS_DATA_SOURCE_ID`, `NOTION_PARENT_PAGE_ID`, `MEETING_INTERNAL_DOMAIN`, `CALENDAR_OWNER_EMAIL` and optional roster/channel mappings replace old tenant constants. All-zero UUIDs and `CONFIGURED_*` values in retained examples mean missing configuration, not real objects. Stop/skip that integration explicitly; do not send a request using placeholder IDs.

## Preserve the original Vexa pipeline, not legacy deployment accidents

The original skill also documents legacy/shadow paths and operational incident fixes. Those are historical context, not required public setup. For this profile always select `vexa-primary`, require Vexa Google-login readiness and exact provider identity; do not launch a local captions bot. The persistent profile belongs to Vexa object storage. Legacy display, plugin auth_state, host SSH, canary and systemd commands are not portable requirements. Do not run provisioning/restarts/deletion from a skill's historical example.

Use the existing context builder/preparer, bounded optional Notion enrichment, stable fallback glossary, private lifecycle response, state machine and admission verification. A submit receipt is not admission. No second bot for an ambiguous submission. Preserve occurrence IDs, captures and audit receipts; do not bind a recurring URL alone to an occurrence.

## Optional integrations and approval

Notion is the main notes destination when configured. Preserve the original notes sections, attendee/project resolution, transcript attachment and pending action list. If only a parent page is configured, create a child page; do not invent a database schema. If actual data-source schemas are configured, use the original database flow. Do not create tasks while drafting: standalone transcript-to-Notion task sections run only after explicit organizer approval through the finalizer/retrieval flow.

Slack, email, CRM and vault backup are opt-in adapters. If not configured, write local artifacts and explicit delivery-skipped receipts, then ask for review locally. Do not invent a bot token, route to an unrelated home channel or impersonate an internal employee. No external messages, attendee email, CRM changes, broad channel distribution or task writes unless explicitly enabled and approved for the exact audience/content/action. This public profile overrides historical automatic-sharing defaults, not the underlying review/reconciliation workflow.

Organizer review must preserve `triage-context.json`, exact occurrence, proposed actions, approval status, context rehydration and one result per action. No first-name guessing, sentiment-only approval or fixed senior-employee fallback. Require the configured human organizer/delegate and explicit approve/edit/reject; content edits invalidate prior approval. The existing prose is not a machine-enforced authentication service: unattended task creation remains disabled until its separate authorization implementation is verified.

## Dispatch receipts

Webhook payload `_dispatch` is authoritative. Use the preserved executor-receipt helper to begin, heartbeat and finish using the literal claim/receipt/key/phase/attempt/delivery-ID values. Exit 3 fences the generation; stop side effects. Success only after real evidence lands. Do not mark phase done from transport acceptance, provider submission or a planned write.

## Historical references

Private incident narratives were not exported because they contain real meeting/person/customer details. The port manifest lists every excluded reference and every copied source file. Operational policies and helper algorithms remain in the full skills/scripts. A missing reference is not permission to improvise a destructive recovery; consult this runtime contract and current source, or stop for owner review.
