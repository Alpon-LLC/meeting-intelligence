# Verification of local release candidate

## Actually exercised

- Wrote tests before implementing new scheduler/setup behavior; initial workflow/setup suites failed because implementations were absent. Additional unknown-submission ownership regression failed then passed after correction.
- `python -m unittest discover -s tests -v`: 18 passing tests, including real localhost HTTP roundtrip against a synthetic provider, exact bot identity, consent/window selection, one submission, unknown-outcome no-retry, stop-at-end, empty-transcript wait, partial-capture summary refusal, Notion approval/receipt dedup and safe path handling.
- `python scripts/offline_demo.py`: PASS, explicitly synthetic scheduling/transcript replay. No paid provider/model calls.
- Docker Compose v2.39.4 `config --quiet` against temporary generated credentials: exit 0. No credentials printed or committed.
- Public Vexa source fetched at f1a62fbbdfd2c98cb162582d82c31fe8102acc89. Original patch and generic-fixture patch both pass `git apply --check` on that source. Original applied tree `git diff --check` passed.
- Python syntax compilation and Bash syntax checks passed.
- Limited export scan found no matched private company email/domain/host path, listed credential patterns or company references in generic skills. This is not a complete security clearance.
- Exact-version PyPI metadata verified for the two declared direct Python dependencies; upstream Vexa/Hermes license texts retrieved at selected commits.

## Not exercised / release blockers

- Docker client exists but daemon/socket is not available inside this build container. Docker image builds, image architecture availability and service boot were NOT run. Compose plugin was downloaded only to a separate cache for configuration validation.
- Local Google login, durable session restore, Calendar consent/token refresh, actual Vexa admission/audio, local Whisper compatibility/performance and multiple attendees' same-room bots: NOT tested.
- Fresh isolated Hermes install/model summary and live approved Notion publication: NOT tested. Their subprocess/API paths exist but offline tests are not substitutes for real provider validation.
- No claim of source equivalence to an existing production bot image; this candidate rebuilds from reviewed source + patch.
- The candidate simplifies production: exact-ID transcript polling and local Whisper/storage replace the private router, remote store, second pass and signed callback pipeline. This new path needs a controlled end-to-end rehearsal.
- Full transitive lock/SBOM, image vulnerability/license inventory, rights-holder license approval and public release review remain required.
- No public repository/image created, code pushed, production state changed, or real meeting joined.

## Required next rehearsal

On an owner-approved disposable local Docker engine: run setup, build images, verify every service, authorize human Google browser login and separate Calendar OAuth, configure isolated Hermes/model and test Notion parent, then run a consenting synthetic calendar event. Verify bot requested → active/start time → completed, actual speech markers in transcript, grounded local draft, explicitly approved Notion page readback and provider teardown. Record costs, resources, host OS/architecture and each failed/manual step. Fix failures before announcing one-shot attendee reproduction.
