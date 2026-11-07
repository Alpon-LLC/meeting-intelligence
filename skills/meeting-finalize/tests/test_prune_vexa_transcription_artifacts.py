import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "prune-vexa-transcription-artifacts.py"
SPEC = importlib.util.spec_from_file_location("prune_vexa", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PruneVexaArtifactsTest(unittest.TestCase):
    def fixture(self, root: Path, *, backup=True, replay=False):
        workspace = root / "meetings" / "meeting-1"
        workspace.mkdir(parents=True)
        canonical = workspace / "transcript.canonical.jsonl"
        canonical.write_text('{"text":"ok"}\n')
        raw_root = root / "raw"
        raw = raw_root / ("replay-baselines" if replay else "meeting-1")
        raw.mkdir(parents=True)
        (raw / "signal.jsonl").write_text("signal")
        tape = workspace / "transcript.batch.provider.json"
        tape.write_text("{}")
        old = (datetime.now(timezone.utc) - timedelta(days=20)).timestamp()
        os.utime(raw, (old, old))
        os.utime(tape, (old, old))
        state = {
            "canonical_status": "completed",
            "canonical_transcript_path": str(canonical),
            "canonical_transcript_sha256": MODULE.sha256(canonical),
            "raw_signal_path": str(raw),
            "stt_tape_path": str(tape),
        }
        if backup:
            state.update({"backup_verified_at": "2026-09-25T00:00:00Z", "backup_manifest_sha256": "sha256:test"})
        state_path = workspace / "transcription.state.json"
        state_path.write_text(json.dumps(state))
        return state_path, raw_root, raw, tape

    def test_dry_run_never_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, raw_root, raw, tape = self.fixture(Path(tmp))
            result = MODULE.process(state, raw_root, datetime.now(timezone.utc) - timedelta(days=14), False)
            self.assertTrue(result["eligible"])
            self.assertTrue(raw.exists())
            self.assertTrue(tape.exists())

    def test_apply_requires_verified_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, raw_root, raw, tape = self.fixture(Path(tmp), backup=False)
            result = MODULE.process(state, raw_root, datetime.now(timezone.utc) - timedelta(days=14), True)
            self.assertFalse(result["eligible"])
            self.assertIn("backup_not_verified", result["reasons"])
            self.assertTrue(raw.exists())
            self.assertTrue(tape.exists())

    def test_apply_removes_only_approved_expired_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, raw_root, raw, tape = self.fixture(Path(tmp))
            result = MODULE.process(state, raw_root, datetime.now(timezone.utc) - timedelta(days=14), True)
            self.assertTrue(result["eligible"])
            self.assertFalse(raw.exists())
            self.assertFalse(tape.exists())
            self.assertTrue((state.parent / "transcription.retention.json").exists())

    def test_replay_baseline_is_never_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, raw_root, raw, tape = self.fixture(Path(tmp), replay=True)
            result = MODULE.process(state, raw_root, datetime.now(timezone.utc) - timedelta(days=14), True)
            self.assertFalse(result["eligible"])
            self.assertIn("raw_signal_path_immutable_replay", result["reasons"])
            self.assertTrue(raw.exists())
            self.assertTrue(tape.exists())


if __name__ == "__main__":
    unittest.main()
