import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "score_transcription_quality.py"
SPEC = importlib.util.spec_from_file_location("score_quality", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ScoreTranscriptionQualityTest(unittest.TestCase):
    def test_exact_controlled_fixture_scores_perfectly(self):
        text = "ExampleOrg uses Hermes. Alex Example approved $12,450 on 2026-09-25."
        rows = [{"speaker": "Alex Example", "start": 0, "end": 5, "text": text}]
        context = {
            "keyterms": [{"text": "ExampleOrg"}, {"text": "Hermes"}, {"text": "Alex Example"}],
            "canonical_entities": {"people": ["Alex Example"]},
        }
        result = MODULE.score(rows, rows, context)
        self.assertEqual(result["wer"], 0)
        self.assertEqual(result["keyterms"]["recall"], 1)
        self.assertEqual(result["participant_names"]["recall"], 1)
        self.assertEqual(result["numbers_and_dates"]["accuracy"], 1)
        self.assertEqual(result["speaker_attribution_non_overlap"]["accuracy"], 1)

    def test_reports_omissions_insertions_and_wrong_speaker(self):
        reference = [{"speaker": "A", "start": 0, "end": 2, "text": "ExampleOrg uses Hermes"}]
        candidate = [{"speaker": "B", "start": 0, "end": 2, "text": "ExampleOrg adds noise"}]
        context = {"keyterms": [{"text": "ExampleOrg"}, {"text": "Hermes"}], "canonical_entities": {"people": []}}
        result = MODULE.score(candidate, reference, context)
        self.assertGreater(result["wer"], 0)
        self.assertLess(result["keyterms"]["recall"], 1)
        self.assertEqual(result["speaker_attribution_non_overlap"]["accuracy"], 0)


if __name__ == "__main__":
    unittest.main()
