# Paste this into Devin

Repository: https://github.com/Alpon-LLC/meeting-intelligence

## Goal and expected result

Build and reproduce a local autonomous meeting assistant, not just a collection of healthy containers. My Calendar event should automatically send my uniquely named, signed-in Vexa bot into Google Meet, capture real consenting synthetic discussion, produce a Hermes draft, and publish reviewed notes to my own Notion page. The shared-room event lets attendees see whose scheduled bot joins first; do not assume multi-bot/account/device compatibility before rehearsing it.

Read `docs/ARCHITECTURE.md` first. It contains the system diagram, component/file map, authentication boundaries, actual scheduler behavior, missing features and what I should see when finished. Read `docs/VERIFICATION.md` to separate existing offline evidence from untested live behavior. Inspect the code before running it; documentation describes intent, not proof.

## Scope and architecture

- Vexa services, database, queue, browser profile storage and CPU speech transcription run in my local Docker engine.
- `setup.sh` is the setup entrypoint; `setup_local.py` provides individual steps. The build fetches exact upstream Vexa source and applies `patches/vexa.patch`; no company image/server is required.
- `meeting_app.py run` is the long-running Calendar scheduler and Vexa lifecycle controller. It polls my Calendar, selects [record] events after explicit consent enablement and near their scheduled time, submits once, tracks exact bot ID and retrieves that occurrence's transcript.
- The bot's Google browser login and Calendar API OAuth are separate human authorizations, even using one Google account. Calendar requires my own Desktop OAuth client and Calendar API enabled, not DWD/X.509.
- An isolated Hermes instance generates summary.md through a generic skill. My model may be a paid external provider; disclose that egress/cost. Only approved publication writes to Notion. No task creation, Slack broadcast, custom dashboard or automatic rich prep briefing is included.
- Original transcript, draft, state and receipts remain local under private/meetings. Credentials/profile/audio remain private; retention is not automatic.

## Your assignment

Install, exercise and repair this release candidate where necessary to achieve the defined result. Do not stop at producing a plan or passing mocked tests. You may make local code/doc fixes inside this checkout, add regression tests, and report the diff. Do not silently drop Calendar, authenticated Vexa or Notion to make the demo look green. If a real external prerequisite blocks you, identify the exact human action, preserve evidence and report partial completion honestly. Do not push fixes or publish images without approval.

Set up this repository on my approved **local development environment**, not production. Read README, SECURITY, PRIVACY and provenance first. If your workspace cannot access my device/Docker engine, say so and guide me to execute the commands locally; do not claim cloud setup is local.

Do not publish/push code or images, use the authors' infrastructure, inspect unrelated credentials, change my existing Hermes profile, or execute cutover scripts. Use only generated local secrets and my own Google/Notion/model accounts. Keep secrets in the ignored private directory; never request passwords/MFA codes or dump environment variables. Ask before provisioning, installing system dependencies, paid model use, recording or external writes.

1. Run the repository's offline tests. Record real results and failures.
2. Run `setup_local.py init`, then doctor. Check supported OS/architecture/resources and Docker Compose v2. Do not pretend an unavailable daemon works.
3. After my approval, install Python dependencies, fetch the pinned public Vexa source + patch, build local images and start Compose. Explain first build/model downloads. Verify all actual service health checks and report image IDs/source revisions. No pre-existing private image is required.
4. Guide me through bootstrap and the local Google login browser. I sign in and handle MFA myself. Require the durable-login success receipt. Do not expose the localhost VNC port publicly.
5. Install/configure the isolated Hermes instance using docs/HERMES.md. I choose and authorize my model provider and budget. Source activate.sh for scheduler/Hermes isolation.
6. Guide me through my Google Cloud Desktop OAuth client and Calendar API authorization in docs/CALENDAR.md. No DWD/X.509. Explain that Vexa browser login and Calendar OAuth are separate grants.
7. Guide me through creating a Notion integration and sharing my own test parent page. Run setup_local.py notion; I enter the token in the hidden prompt, not in this chat.
8. Before live testing, ask me for organizer/participant recording and transcription consent and a controlled test event. Use synthetic discussion only. Give the bot my unique name, use [record] in the calendar title, enable consent and start the scheduler. Verify a single exact bot transitions requested → active with start_time → completed; verify actual spoken synthetic markers in the transcript. Do not blindly resubmit on timeout or admission failure.
9. Verify Hermes produced grounded summary.md with decisions, proposed actions and uncertainties. Ask me to review it. Publish to my designated Notion page only after my explicit approval, then verify exact page readback. Do not create tasks or broadcast anything.
10. Report commands, actual passes/failures, synthetic versus live evidence, unresolved manual steps and teardown state. A healthy container is not proof of capture. If Docker/login/API fails, preserve local evidence, explain the exact blocker, and use only the clearly labeled offline synthetic demo. Never fabricate a live pass.

Done means a real controlled calendar-triggered meeting produced the expected transcript, a Hermes draft, an approved Notion page, and verified bot teardown. If any step is unproven, say so. Do not announce/publicize the repo or promise other frameworks/platforms work.
