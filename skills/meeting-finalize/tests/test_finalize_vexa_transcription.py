import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "finalize_vexa_transcription.py"
SPEC = importlib.util.spec_from_file_location("finalize_vexa", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FinalizeVexaTranscriptionTest(unittest.TestCase):
    def args(self, root: Path):
        signal = root / "signal.jsonl"
        signal.write_text("signal")
        context = root / "transcription.context.json"
        context.write_text("{}")
        return argparse.Namespace(
            signal_tape=signal, context=context, live_transcript=None, lifecycle_metadata=None,
            aliases=None, output_dir=root / "output", state=root / "transcription.state.json",
            router_url="http://router", concurrency=4, meeting_end_at="2026-09-25T10:00:00Z",
            batch_script=root / "batch.py", canonical_script=root / "canonical.py",
        )

    def test_success_records_all_artifact_hashes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                args.output_dir.mkdir(exist_ok=True)
                if len(calls) == 1:
                    (args.output_dir / "transcript.batch.normalized.jsonl").write_text('{"text":"ExampleOrg"}\n')
                    (args.output_dir / "transcript.batch.manifest.json").write_text("{}")
                    (args.output_dir / "transcript.batch.provider.json").write_text("{}")
                else:
                    (args.output_dir / "transcript.canonical.jsonl").write_text(
                        '{"ts":"2026-09-25T09:00:00Z","speaker":"Organizer","text":"ExampleOrg"}\n'
                    )
                return subprocess.CompletedProcess(command, 0, b"ok", b"")

            with patch.dict(os.environ, {
                "TRANSCRIPT_EDITOR_API_URL": "",
                "TRANSCRIPT_EDITOR_API_KEY_FILE": "",
                "TRANSCRIPT_EDITOR_MODEL": "",
            }):
                result = MODULE.run(args, runner)
            state = json.loads(args.state.read_text())
            self.assertEqual(result["status"], "completed")
            self.assertTrue(state["finalization_ready"])
            self.assertEqual(state["readability_status"], "canonical_fallback")
            self.assertFalse(state["readability_ai_attempted"])
            self.assertIn("[0:00] Organizer: ExampleOrg", (args.output_dir / "transcript-readable.md").read_text())
            self.assertEqual(state["stt_tape_sha256"], MODULE.digest(args.output_dir / "transcript.batch.provider.json"))
            with patch.dict(os.environ, {
                "TRANSCRIPT_EDITOR_API_URL": "",
                "TRANSCRIPT_EDITOR_API_KEY_FILE": "",
                "TRANSCRIPT_EDITOR_MODEL": "",
            }):
                self.assertEqual(MODULE.run(args, runner)["status"], "already_completed")
            self.assertEqual(len(calls), 2)

    def test_configured_editor_is_run_before_finalization_becomes_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            args.signal_tape.write_text('{"type":"captured_signal_header","started_at":"2026-09-25T08:58:00Z"}\n{"type":"synthetic-frame"}\n')
            key = Path(tmp) / "editor.key"
            key.write_text("key")
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                args.output_dir.mkdir(exist_ok=True)
                if "batch.py" in str(command[1]):
                    (args.output_dir / "transcript.batch.normalized.jsonl").write_text('{"text":"I I agree."}\n')
                    (args.output_dir / "transcript.batch.manifest.json").write_text("{}")
                    (args.output_dir / "transcript.batch.provider.json").write_text("{}")
                elif "canonical.py" in str(command[1]):
                    (args.output_dir / "transcript.canonical.jsonl").write_text(
                        '{"ts":"2026-09-25T09:00:00Z","speaker":"Organizer","text":"I I agree."}\n'
                    )
                else:
                    (args.output_dir / "transcript-readable.md").write_text("# Readable transcript\n\n[0:00] Organizer: I agree.\n")
                    (args.output_dir / "transcript.editor.manifest.json").write_text(json.dumps({
                        "status": "completed", "model": "cx/gpt-5.6-sol", "turn_count": 1,
                        "edited_turn_count": 1, "fallback_turn_count": 0,
                    }))
                return subprocess.CompletedProcess(command, 0, b"ok", b"")

            with patch.dict(os.environ, {
                "TRANSCRIPT_EDITOR_API_URL": "http://router/v1/chat/completions",
                "TRANSCRIPT_EDITOR_API_KEY_FILE": str(key),
                "TRANSCRIPT_EDITOR_MODEL": "cx/gpt-5.6-sol",
            }):
                result = MODULE.run(args, runner)
            state = json.loads(args.state.read_text())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(calls), 3)
            self.assertTrue(state["finalization_ready"])
            self.assertTrue(state["readability_ai_attempted"])
            self.assertEqual(state["readability_edited_turn_count"], 1)
            self.assertIn("--origin", calls[2])
            self.assertEqual(calls[2][calls[2].index("--origin") + 1], "2026-09-25T08:58:00Z")

    def test_signal_origin_reads_capture_header_and_rejects_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signal.jsonl"
            path.write_text('{"type":"captured_signal_header","started_at":"2026-09-25T08:58:00Z"}\n')
            self.assertEqual(MODULE.signal_origin(path), "2026-09-25T08:58:00Z")
            path.write_text("not-json\n")
            self.assertIsNone(MODULE.signal_origin(path))

    def test_batch_failure_reaches_terminal_fallback_state_without_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))

            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, b"", b"secret transcript text")

            with patch.dict(os.environ, {
                "TRANSCRIPT_EDITOR_API_URL": "",
                "TRANSCRIPT_EDITOR_API_KEY_FILE": "",
                "TRANSCRIPT_EDITOR_MODEL": "",
            }):
                result = MODULE.run(args, runner)
            state = json.loads(args.state.read_text())
            error = json.loads((args.output_dir / "transcription.error.json").read_text())
            self.assertEqual(result, {"status": "failed", "stage": "batch"})
            self.assertEqual(state["batch_status"], "retry_exhausted")
            self.assertEqual(state["canonical_status"], "disabled")
            self.assertTrue(state["finalization_ready"])
            self.assertNotIn("secret", json.dumps(error))


if __name__ == "__main__":
    unittest.main()
