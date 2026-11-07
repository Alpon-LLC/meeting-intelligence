import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_transcription_backup.py"
SPEC = importlib.util.spec_from_file_location("verify_backup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class VerifyTranscriptionBackupTest(unittest.TestCase):
    def fixture(self, root: Path):
        state_path = root / "transcription.state.json"
        state_path.write_text(json.dumps({
            "canonical_transcript_sha256": "sha256:canonical",
            "batch_manifest_sha256": "sha256:batch",
            "raw_signal_sha256": "sha256:signal",
            "stt_tape_sha256": "sha256:tape",
        }))
        return state_path

    def test_marks_verified_only_when_every_state_hash_is_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = self.fixture(root)
            manifest_path = root / "backup.json"
            manifest_path.write_text(json.dumps({
                "version": "transcription-backup-manifest.v1",
                "backup_id": "b2:snapshot-123",
                "artifacts": [
                    {"sha256": "sha256:canonical"}, {"sha256": "sha256:batch"},
                    {"sha256": "sha256:signal"}, {"sha256": "sha256:tape"},
                ],
            }))
            result = MODULE.verify(state_path, manifest_path)
            state = json.loads(state_path.read_text())
            self.assertEqual(result["verified_artifact_count"], 4)
            self.assertEqual(state["backup_id"], "b2:snapshot-123")
            self.assertTrue(state["backup_verified_at"])
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_rejects_incomplete_backup_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = self.fixture(root)
            before = state_path.read_bytes()
            manifest_path = root / "backup.json"
            manifest_path.write_text(json.dumps({
                "version": "transcription-backup-manifest.v1",
                "backup_id": "b2:incomplete",
                "artifacts": [{"sha256": "sha256:canonical"}],
            }))
            with self.assertRaisesRegex(ValueError, "missing 3"):
                MODULE.verify(state_path, manifest_path)
            self.assertEqual(state_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
