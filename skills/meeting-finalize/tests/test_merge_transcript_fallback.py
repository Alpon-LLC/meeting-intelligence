import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merge_transcript_fallback.py"
SPEC = importlib.util.spec_from_file_location("merge_transcript_fallback", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MergeTranscriptFallbackTest(unittest.TestCase):
    def test_uses_fallback_only_before_canonical_cutover(self):
        fallback = [
            {"ts": "2026-09-28T06:00:00", "speaker": "A", "text": "early"},
            {"ts": "2026-09-28T06:10:00", "speaker": "A", "text": "overlap"},
        ]
        canonical = [{"ts": "2026-09-28T06:10:00Z", "speaker": "A", "text": "canonical"}]
        rows = MODULE.merge(fallback, canonical, datetime(2026, 9, 28, 6, 10, tzinfo=timezone.utc))
        self.assertEqual([row["text"] for row in rows], ["early", "canonical"])
        self.assertEqual(rows[0]["source"], "legacy_google_captions_fallback")


if __name__ == "__main__":
    unittest.main()
