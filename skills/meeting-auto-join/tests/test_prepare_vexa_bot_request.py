from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare-vexa-bot-request.py"
SPEC = importlib.util.spec_from_file_location("prepare_vexa_bot_request", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class PrepareVexaRequestTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict("os.environ", {"VEXA_BOT_NAME": "Meeting Bot"})
        env.start()
        self.addCleanup(env.stop)

    def test_retry_clears_failed_attempt_terminal_state_without_losing_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(json.dumps({"event_id": "event-1", "meet_url": "https://meet.google.com/abc-defg-hij"}))
            context = root / "context.json"
            context.write_text(json.dumps({"version": "meeting-stt-context.v1", "meeting_id": "event-1", "language": "en", "keyterms": [{"text": "ExampleOrg"}]}))
            state = root / "state.json"
            state.write_text(json.dumps({"live_status": "failed_nonretryable", "batch_status": "disabled", "canonical_status": "disabled", "readability_status": "disabled", "finalization_ready": True, "completed_at": "old", "notion_context_path": "context-input.json"}))
            request = MODULE.prepare(event, context, root / "glossary.json", state)
            result = json.loads(state.read_text())
            self.assertEqual(request["transcription_keyterms"], ["ExampleOrg"])
            for field in ("batch_status", "canonical_status", "readability_status"):
                self.assertEqual(result[field], "pending")
            self.assertEqual(result["live_status"], "running")
            self.assertFalse(result["finalization_ready"])
            self.assertNotIn("completed_at", result)
            self.assertEqual(result["notion_context_path"], "context-input.json")

    def test_context_and_degraded_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(json.dumps({"event_id": "event-1", "meet_url": "https://meet.google.com/abc-defg-hij"}))
            context = root / "transcription.context.json"
            context.write_text(json.dumps({"version": "meeting-stt-context.v1", "meeting_id": "event-1", "language": "en", "keyterms": [{"text": "ExampleOrg"}]}))
            glossary = root / "glossary.json"
            glossary.write_text(json.dumps({"terms": [{"text": "Hermes", "always": True}]}))
            state = root / "transcription.state.json"

            request = MODULE.prepare(event, context, glossary, state)
            self.assertEqual(request["transcription_keyterms"], ["ExampleOrg"])
            self.assertEqual(request["bot_name"], "Meeting Bot")
            self.assertFalse(request["continue_meeting"])
            self.assertEqual(json.loads(state.read_text())["context_status"], "completed")
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

            context.unlink()
            request = MODULE.prepare(event, context, glossary, state)
            self.assertEqual(request["transcription_keyterms"], ["Hermes"])
            self.assertEqual(json.loads(state.read_text())["context_status"], "degraded")

            with mock.patch.dict("os.environ", {"VEXA_BOT_NAME": "Meeting Bot Notes"}):
                request = MODULE.prepare(event, context, glossary, state)
            self.assertEqual(request["bot_name"], "Meeting Bot Notes")


if __name__ == "__main__":
    unittest.main()
