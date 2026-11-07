# Source and build provenance

## Vexa

Public source: https://github.com/Vexa-ai/vexa
Exact upstream commit: `f1a62fbbdfd2c98cb162582d82c31fe8102acc89` (v0.12.28).
`patches/vexa.patch` adapts the meeting integration patch developed by Alpon Digital LLC, including keyterms, Google Meet `/meet/<code>` lobby handling and capture/lifecycle changes.
The release candidate replaces company/person names in synthetic patch fixtures with generic examples. The diff is not limited to speech vocabulary; review all touched files before building.

No Vexa source tree or private company Git history is committed to this package. `setup_local.py fetch` downloads public source to ignored `.build/vexa`, checks patch applicability and applies it. The build marker contains the full upstream commit and patch SHA-256. `deploy/Dockerfile.meeting-api` overlays only the reviewed spawn seams/schema on Vexa 0.12.28.

Upstream license is Apache-2.0; exact source LICENSE is included under licenses/vexa-LICENSE. Preserve applicable upstream NOTICE and third-party notices in built images. The upstream bot image includes browser/audio/model dependencies with separate notices; do not remove those. No claim of an exact reproduction of the existing production image is made: that image's marker names the upstream base prefix but not the exact patch hash.

## Hermes

Public source: https://github.com/NousResearch/hermes-agent
Candidate commit: `93dd24359dc011283a02098af84f63404f72ad15`.
Hermes uses MIT; exact LICENSE included under licenses/hermes-LICENSE. The candidate integration invokes Hermes CLI in an isolated home; it does not bundle or depend on the old Google Meet plugin. Fresh-install compatibility remains a live release gate, not a test result.

## Local-first simplification

Production had an external object store, private speech router, completion webhook bridge, second STT pass and rich publication orchestration. This candidate deliberately uses local object storage, upstream CPU Whisper and exact-meeting-ID transcript polling instead. That avoids a public callback and private routing dependencies while keeping Calendar automation. It is a new public-oriented integration, not a claim that production was copied unchanged. This changed STT/storage/polling path requires an end-to-end rehearsal.

## Publication rights

New integration code/skills are prepared for an MIT release, pending the rights holder's approval. Existing Vexa patch modifications require owner approval and Apache-2.0 change attribution before public distribution. No public publication or licensing authorization is inferred from the internal build request. Do not distribute the draft until that approval and release checks are complete.
