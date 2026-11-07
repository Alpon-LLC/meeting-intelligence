# Port contract and changes

## Authoritative direction

Generalize the actual implementation. Keep the original JS scheduler. Call Vexa directly for join/status/stop. Keep existing signed Hermes webhooks for prep and post-meeting agent work. Preserve the real skill family and supporting algorithms; do not replace them with a new minimal application.

## Source provenance

Scheduler and skill source were read from the deployed data tree `/opt/data/scripts/meeting-trigger` and installed meeting skills. Source hashes are recorded per file in PORT-MANIFEST.json. The configuration repository anchor at initial inspection was `0d6763c29eac1601cc254f4638dbcc0cd82f4681`; some live source files differ from that commit, so hashes identify copied inputs rather than pretending committed history equals deployment. The port does not publish that private repository history.

## Retained

- Original JS calendar OAuth provider, owner involvement, event normalization and phase windows.
- Dispatch claims, generation fencing, executor receipts, retries/backoff/hard bounds, duplicate handling and deferred finalization.
- Direct Vexa context starter, bounded Notion vocabulary enrichment, stable glossary fallback, request preparer, lifecycle/state artifacts.
- Full meeting-auto-join policy; full internal-meeting-prep classification/context/per-person workflow; full finalizer with readiness, sparsity checks, notes, attachments, organizer triage, context rehydration and post-approval task reconciliation.
- Original captured-signal batch transcriber, deterministic canonicalization, readability editor/verifier/ledgers, quality/review/backup/retention helpers and regression fixtures.
- Signed completion bridge and signal materialization, exact IDs, hashes, review gates and bounded-rejoin worker.

## Intentional public adaptations

- Scheduler's deterministic bypass now covers **start only**. Prep and finish retain original signed HTTP transport. No local Hermes CLI agent launcher replaces webhooks.
- Authenticated Vexa-only profile: no legacy Playwright plugin/display preflight or private SSH fallback. Historical legacy/shadow sections remain explicitly non-executable context, not a fallback to silently run.
- Public opt-in marker plus actual human consent enablement narrows calendar eligibility. Original scheduling windows remain unchanged.
- Exact provider status/stop synchronization uses configured Vexa API; admission is separate from submission. Stop request receipts prevent blind repeated deletes.
- Completion bridge materializes evidence but defers agent dispatch to JS finish readiness, avoiding an early finish invocation before canonical artifacts.
- Existing internal deterministic Notion/Slack recovery publisher is not exported as an alternative agent engine; finish resumes the original agent webhook for publication/review.
- IDs, domains, paths, recipient mappings, glossary and secret providers are attendee configuration, never company constants. Original rich Notion database flow is retained when its data-source/schema is configured; simple parent-page mode is a runtime adaptation, not a claim of automatic schema equivalence.
- Slack/email/CRM/vault/task adapters are opt-in; public runtime contract prohibits historical auto-broadcast/default-person behavior. Approval policy is preserved and tightened to explicit human review; no claim of a new authenticated task-approval service.
- Quarantined prep helper is shipped with `.py.disabled`, not installed as a reliable entrypoint. Agent prep workflow remains complete. Private incident narratives are catalogued, not exported. Missing references require inspection/owner decision, not invented destructive commands.
- Batch STT router URL/token/model are configurable (`MEETING_STT_*`), local CPU STT in default Compose. Full signal replay/canonical processing is retained. Provider-specific model/accuracy compatibility remains live acceptance work.

## Tests changed for justified public differences

- Explicit overall timeout fixture fixes a stale inherited lease test that otherwise inherited a longer overall timeout.
- Finish-gate fixtures now expect agent-needed when canonical evidence is ready, rather than launching the internal Notion/Slack publisher.
- SSH-fallback tests now expect configuration refusal; direct HTTP header/body/error tests are retained.
- Email/Notion fixtures explicitly configure synthetic domains instead of inheriting company defaults.
- Header-only editor fixture now includes a synthetic frame, respecting the original empty-capture safety gate.

These are documented contract adaptations, not removal of inconvenient tests. Tests do not establish live compatibility. See VERIFICATION.md.

## Remaining real gates

Build/run Docker on an approved isolated device; human Google login/Calendar consent; gateway HMAC/route compatibility against selected Hermes release; live signal materialization and STT; real model notes; approved Notion draft; optional Slack organizer review and task adapter authorization. The public-runtime overlay must be reviewed against retained procedures for residual deployment assumptions before attendees rely on unattended operation.
