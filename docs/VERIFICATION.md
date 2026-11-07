# Verification: original JS/skill port

This supersedes the first draft's Python-scheduler verification as evidence for the advertised runtime.

## Executed

- `npm test`: nine JS test programs passed. Includes retained original dispatch-lease, concurrency, executor-receipt, review-flag, Vexa finish/rejoin tests, plus public routing structure, direct exact-ID status/single-stop and real localhost signed prep-webhook transport/duplicate suppression.
- Python original auto-join helper suite: 15 tests passed.
- Python original finalizer/helper suite: 49 tests passed.
- Public port/runtime installation suite: 3 tests passed, including full skill bodies, HTTP-only endpoint behavior, actual installed agent routes and private/redacted configuration generation.
- Python source compilation passed for exported scripts; Node source syntax checked. Compose configuration validation is re-run against generated temporary secrets.
- Original Vexa patch applicability was checked against the exact public base; generic fixture patch applicability passed in the earlier build step.

Original tests changed only for documented public contracts: explicit overall lease timeout, direct HTTP instead of removed private SSH path, configured synthetic domains, header-only capture fixture safety, and finish-agent routing rather than internal deterministic publisher. See PORT.md.

## Source coverage

PORT-MANIFEST.json records 275 copied/excluded source records: 66 skill-family files initially exported, 199 private reference narratives excluded, ten scheduler source files ported as base. Additional runtime contracts, status helper and tests were added. The complete main skill bodies and operational scripts are preserved; private incident narratives are not automatically public content. Their absence is explicit, not a claim every original byte was exported.

## Not executed

No Docker daemon in authoring container: images not built, stack not started. No actual Google sign-in, Calendar OAuth grant, live Vexa meeting/capture, multi-bot race, full gateway/model workflow, real Notion/Slack/email/task write or provider teardown was exercised. Localhost webhook tests use a synthetic HTTP server, not a real Hermes model session.

The selected Hermes gateway version, browser authentication on attendee hardware, local STT compatibility/latency and completion materialization remain real release gates. Public-runtime overrides need human review against retained internal procedure assumptions. No claim of faithful deployed image equivalence or fully operational one-shot installer is made.

## Security limits

Targeted export scans remove known company domains/names/IDs, credential-like assignments and private source/endpoint references. No regex scan proves all secrets, customer details or proprietary prompts are cleared. Historical references are excluded by default. Keep all runtime private data ignored; no credentials are in test reports. Preserve upstream licenses and obtain owner rights approval. Public publication is authorized, not a legal or security certification.
