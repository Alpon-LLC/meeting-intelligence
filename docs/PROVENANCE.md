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

## Faithful port correction

The first draft unnecessarily replaced the JS scheduler and skill chain. The correction restores the original JS phase/lease/receipt machinery and full generalized skill bodies, direct Vexa join/status/stop, signed completion bridge, batch/canonical processing and prep/finish agent webhooks. Local object storage and configurable local STT replace deployment-specific services; original processing algorithms remain. See PORT.md and PORT-MANIFEST.json for source hashes and explicit exclusions.

## Publication rights

New integration code/skills are prepared for an MIT release, pending the rights holder's approval. Existing Vexa patch modifications require owner approval and Apache-2.0 change attribution before public distribution. Repository publication was subsequently authorized. License selection and rights review remain explicit unresolved items; do not assume publication makes every component MIT.
