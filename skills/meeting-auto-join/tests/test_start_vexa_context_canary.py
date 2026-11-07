import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "start-vexa-context-canary.py"
SPEC = importlib.util.spec_from_file_location("start_vexa", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class StartVexaContextCanaryTest(unittest.TestCase):
    def test_bot_endpoint_accepts_service_root_or_explicit_bots_path(self):
        self.assertEqual(MODULE.bot_endpoint("http://vexa:8000"), "http://vexa:8000/bots")
        self.assertEqual(MODULE.bot_endpoint("http://vexa:8000/"), "http://vexa:8000/bots")
        self.assertEqual(MODULE.bot_endpoint("http://vexa:8000/bots"), "http://vexa:8000/bots")
        self.assertEqual(MODULE.bot_endpoint("http://vexa:8000/bots/"), "http://vexa:8000/bots")
        self.assertIsNone(MODULE.bot_endpoint(None))
        self.assertIsNone(MODULE.bot_endpoint(""))

    def test_ssh_fallback_requires_explicit_public_endpoint(self):
        with self.assertRaisesRegex(RuntimeError, "VEXA_API_ENDPOINT"):
            MODULE.submit(b"private payload", "host", "container")

    def test_direct_http_submit_uses_private_header_and_suppresses_error_body(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"id":"bot-http"}'

        def opener(request, **kwargs):
            seen["url"] = request.full_url
            seen["key"] = request.get_header("X-api-key")
            seen["data"] = request.data
            seen["timeout"] = kwargs["timeout"]
            return Response()

        response = MODULE.submit_http(b'{"transcription_keyterms":["ExampleOrg"]}', "http://vexa", "private-key", opener)
        self.assertEqual(response["id"], "bot-http")
        self.assertEqual(seen["url"], "http://vexa/bots")
        self.assertEqual(seen["key"], "private-key")
        self.assertEqual(seen["timeout"], 30)
        self.assertIn(b"ExampleOrg", seen["data"])

        def failing_opener(*_args, **_kwargs):
            raise RuntimeError("provider echoed Confidential Name")

        with self.assertRaisesRegex(RuntimeError, "Vexa request failed") as raised:
            MODULE.submit_http(b"secret", "http://vexa/bots", "key", failing_opener)
        self.assertNotIn("Confidential", str(raised.exception))

    def test_failure_state_is_terminal_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "transcription.state.json"
            MODULE.record_submit_failure(state_path)
            state = json.loads(state_path.read_text())
            self.assertEqual(state["live_status"], "failed_nonretryable")
            self.assertEqual(state["batch_status"], "disabled")
            self.assertEqual(state["canonical_status"], "disabled")
            self.assertTrue(state["finalization_ready"])
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_alert_artifact_contains_no_vocabulary_and_can_deliver(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, b"sent", b"")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            status = MODULE.emit_alert(
                workspace, "event-123", "dynamic context failed", "D123",
                workspace / "slack-dm.py", runner,
            )
            artifact = json.loads((workspace / "transcription.alert.json").read_text())
            self.assertEqual(status, "sent")
            self.assertEqual(artifact["delivery_status"], "sent")
            self.assertNotIn("keyterm", json.dumps(artifact).casefold())
            self.assertEqual((workspace / "transcription.alert.json").stat().st_mode & 0o777, 0o600)
            self.assertIn("event-123", calls[0][-1])

    def test_optional_notion_context_timeout_is_nonfatal(self):
        with patch.object(MODULE.subprocess, "run", side_effect=subprocess.TimeoutExpired(["python3"], 1)):
            result = MODULE.run_command(["python3", "notion.py"], timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
