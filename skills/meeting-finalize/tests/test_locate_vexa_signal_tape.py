import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "locate_vexa_signal_tape.py"
SPEC = importlib.util.spec_from_file_location("locate_tape", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class LocateVexaSignalTapeTest(unittest.TestCase):
    def write_tape(self, root: Path, trace: str, meeting: str, started: str):
        path = root / f"{trace}.captured-signal.jsonl"
        path.write_text(json.dumps({
            "type": "captured_signal_header", "v": 1, "native_meeting_id": meeting,
            "trace_id": trace, "started_at": started,
        }) + "\n")
        return path

    def test_selects_newest_matching_tape_and_sibling_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tape(root, "old", "abc-defg-hij", "2026-09-25T09:00:00Z")
            latest = self.write_tape(root, "new", "abc-defg-hij", "2026-09-25T10:00:00Z")
            (root / "new.transcript.jsonl").write_text("{}\n")
            result = MODULE.locate(root, "abc-defg-hij")
            self.assertEqual(result["signal_tape"], str(latest))
            self.assertEqual(result["trace_id"], "new")
            self.assertEqual(result["live_transcript"], str(root / "new.transcript.jsonl"))

    def test_time_window_prevents_cross_occurrence_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = self.write_tape(root, "first", "abc-defg-hij", "2026-09-25T09:00:00Z")
            self.write_tape(root, "second", "abc-defg-hij", "2026-09-26T09:00:00Z")
            result = MODULE.locate(
                root, "abc-defg-hij", None,
                datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(result["signal_tape"], str(expected))

    def test_missing_tape_fails_without_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                MODULE.locate(Path(tmp), "abc-defg-hij")


if __name__ == "__main__":
    unittest.main()
