from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_transcription_state.py"
SPEC = importlib.util.spec_from_file_location("update_transcription_state", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class TranscriptionStateTest(unittest.TestCase):
    def test_terminal_gate_and_private_atomic_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "transcription.state.json"
            state = MODULE.load(path)
            self.assertFalse(MODULE.finalization_ready(state))
            state.update({"live_status": "completed", "batch_status": "completed", "canonical_status": "completed"})
            state["finalization_ready"] = MODULE.finalization_ready(state)
            MODULE.atomic_json(path, state)
            self.assertTrue(json.loads(path.read_text())["finalization_ready"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_failure_states_are_terminal_but_pending_is_not(self):
        self.assertTrue(MODULE.finalization_ready({
            "live_status": "completed", "batch_status": "retry_exhausted", "canonical_status": "disabled",
        }))
        self.assertFalse(MODULE.finalization_ready({
            "live_status": "completed", "batch_status": "running", "canonical_status": "pending",
        }))


if __name__ == "__main__":
    unittest.main()
