from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "canonicalize_transcript.py"
SPEC = importlib.util.spec_from_file_location("canonicalize_transcript", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class CanonicalizeTranscriptTest(unittest.TestCase):
    def test_applies_only_canonical_aliases_and_preserves_boundaries(self):
        rows = [{"segment_id": "s1", "speaker": "Alice", "start": 1.0, "end": 2.0, "text": "Alton uses hermes for twelve dollars."}]
        context = {"canonical_entities": {"people": [], "organizations": ["ExampleOrg"], "products": ["Hermes"]}}
        aliases = [{"original": "Alton", "replacement": "ExampleOrg", "reason": "confirmed alias", "context_source": "organization"}]
        corrected, ledger = MODULE.correct_rows(rows, context, aliases)
        self.assertEqual(corrected[0]["text"], "ExampleOrg uses Hermes for twelve dollars.")
        self.assertEqual((corrected[0]["start"], corrected[0]["end"], corrected[0]["speaker"]), (1.0, 2.0, "Alice"))
        self.assertEqual(len(ledger["applied"]), 2)
        self.assertEqual(rows[0]["text"], "Alton uses hermes for twelve dollars.")

    def test_number_bearing_and_noncanonical_replacements_require_review(self):
        rows = [{"segment_id": "s1", "speaker": "Alice", "start": 1, "end": 2, "text": "Nova three and Alton"}]
        context = {"canonical_entities": {"people": [], "organizations": ["ExampleOrg"], "products": ["Deepgram Nova-3"]}}
        aliases = [
            {"original": "Nova three", "replacement": "Deepgram Nova-3", "reason": "alias", "context_source": "product"},
            {"original": "Alton", "replacement": "Not Canonical", "reason": "alias", "context_source": "organization"},
        ]
        corrected, ledger = MODULE.correct_rows(rows, context, aliases)
        self.assertEqual(corrected[0]["text"], rows[0]["text"])
        self.assertEqual(len(ledger["review"]), 2)


if __name__ == "__main__":
    unittest.main()
