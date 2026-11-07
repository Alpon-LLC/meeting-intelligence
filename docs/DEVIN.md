# Paste-ready Devin setup and repair prompt

Repository: https://github.com/Alpon-LLC/meeting-intelligence

Build and exercise this **original-workflow port** on my approved local development device. Read README, docs/ARCHITECTURE.md, docs/PORT.md, docs/PUBLIC-RUNTIME.md, docs/VERIFICATION.md, SECURITY and PRIVACY first. Inspect source, not only prose.

## Goal

My own Calendar triggers a uniquely named authenticated Vexa bot into a consenting Google Meet, with agent-led preparation and post-meeting notes/review through Hermes webhooks. Preserve that system; do not replace the JS scheduler, collapse skills to stubs, remove Calendar or Notion to obtain a superficial pass, or replace agent webhooks with local CLI runs.

## Exact architecture

- `scheduler/run.js` is the real original JS scheduler port. `npm start` serializes ticks. Preserve its phase windows, owner/meeting filters, leases, receipts, deferred finish, review flags and recovery.
- Start/join, status and stop call self-hosted Vexa directly. Context builder/preparer and direct worker retain the real helper algorithms.
- Prep and finish go to signed loopback **Hermes webhooks**. They load the full original internal-meeting-prep and meeting-finalize chains, not the first draft's tiny skills.
- Signed Vexa completion bridge materializes captured-signal evidence. Original postcall worker runs batch STT/canonical/readability; JS finish gate then sends the agent webhook.
- Full generic skills retain classification/context gathering, Notion structure/attachments, organizer review, triage-context/retrieval, approved task reconciliation and optional Slack update. Configure tenant settings; never execute literal placeholder IDs or historical legacy provisioning commands.
- `.build/vexa` is public upstream source at the exact commit plus patch. It is ignored, not a private/company repository dependency. Docker services are local; Hermes model provider may be external and paid.

## Authority and privacy

Work only in this checkout and its new isolated Hermes home/Docker stack. No production access, repository/image publication, pushes, merges, DNS/firewall changes, existing agent-profile changes or deletion of user data without explicit approval. Human supplies account grants, Google sign-in/MFA, credentials via private files/hidden prompts and recording consent. Never request passwords/MFA codes in chat or print tokens/env/real transcript content into public logs. Docker socket is host-privileged: explain and obtain authorization before using it. Do not disable approval safeguards globally.

Devin cloud workspace is not automatically my laptop. If you lack approved local access, say so and guide me to run local commands. Do not report cloud hosting as local reproduction.

## Execute

1. Record OS/CPU, Docker/Compose v2, Node22+, Python3.11+, resources and exact source refs. Run npm tests and Python port/helper suites; distinguish synthetic evidence from real services.
2. After approval, use setup.sh or its explicit setup_local.py steps to generate private config, install Node/Python deps, fetch/apply reviewed Vexa patch, build images and start local Compose. Diagnose build/health failures; local code fixes and regression tests are allowed. Report diffs and actual outputs. Do not silently switch to a company image/server or Vexa Cloud.
3. Bootstrap the local Vexa API user/key. Human signs into the browser at localhost:16080/vnc.html and handles MFA. Require confirmed durable profile upload. No unauthenticated/public VNC exposure.
4. Install isolated Hermes with scripts/install_hermes.py. Human configures own model and budget. Configure full skills and signed routes using scripts/configure_runtime.py; preserve existing model settings and immediate redacted siblings. Inspect original route payload/dispatch contract and confirm gateway acceptance, not just port listening.
5. Guide human through their own Google Cloud Calendar API/Desktop OAuth client and read-only grant, separately from Vexa browser login. No DWD/X.509. Configure actual calendar owner email. Do not bundle a shared event organizer's client secret.
6. Configure own Notion integration/shared parent page. For original database/project/task enrichment, inspect exact configured schemas/data-source IDs; no all-zero IDs or guessed fields. Slack/email/CRM/task destinations are optional and require explicit grants/authorization. Configure public-runtime policy; do not blindly run historical internal fallbacks.
7. Start isolated `hermes gateway run` and `npm start` in separate terminals. Use a dedicated synthetic meeting, unique bot display name, [record] marker and actual participant consent. For prep testing schedule 35 minutes ahead or use a clearly synthetic clock fixture; do not silently change production windows. Verify prep arrives via webhook, start directly creates one Vexa bot, exact provider ID transitions requested/joining to active with start time, and actual synthetic speech is captured.
8. Verify stop/terminal state, signed completion, signal manifest integrity, batch/canonical/readability outputs and finish webhook. Require original full finalizer outputs, transcript-grounded decisions/proposed actions, uncertainty and organizer triage context. A healthy container or accepted POST is not capture.
9. Show human the draft and exact Notion destination. Publish only as explicitly authorized; verify page/body/required attachment effects. Preserve pending review before task creation. Optional task adapter needs authenticated exact-actor/content authorization; if only prompt policy exists, leave tasks disabled and report that gap. No silent shared Slack/email/CRM actions.
10. Report actual pass/fail/unproven stages, commands, refs/image IDs, manual actions, local fixes/tests, costs known/unknown and bot teardown. If a prerequisite blocks you, preserve artifacts and identify exact human action. Use labeled synthetic fallback only; never invent provider results or call partial success complete.

## Done looks like

A real consented Calendar event triggers agent prep (when within prep window), one directly submitted authenticated bot, actual transcript evidence, signed durable completion, canonical/readable processing, a finish-agent draft/review context and an explicitly authorized verified Notion result. Scheduler duplicate/retry behavior is tested and the bot is terminal afterward. No legacy bot or Python scheduler substituted. No unapproved distribution/tasks. Any unproven stage is clearly listed, not hidden behind a success claim.
