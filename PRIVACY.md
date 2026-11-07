# Privacy and consent

Use a dedicated consenting demo room and synthetic discussion. Organizer admission is not sufficient notice/consent for every participant. Explain bot attendance, audio capture/transcription, processing destinations and retention; stop when consent is withdrawn.

Local data: Calendar OAuth token, Google browser profile in Docker object storage, database transcript, replayable raw signal audio, local transcripts/notes, model cache and publication receipts. `recording_enabled=false` disables the ordinary recording mode, not all raw audio capture.

Speech-to-text in this candidate is a local CPU Whisper service; first boot downloads model weights. Hermes sends transcript content to whichever model provider the attendee configures, unless they choose a genuinely local model. Approved Notion publication sends notes to Notion. Each provider has its own terms/retention. No training or finetuning hooks are included in our integration.

No automatic shared-channel posts, task creation, email, CRM, company roster or cross-user calendar access. Retention is manual in this first draft. Compose down does not delete volumes. Do not promise deletion from providers/backups you cannot inspect. Review local recording/privacy law before use; this is not legal advice.
