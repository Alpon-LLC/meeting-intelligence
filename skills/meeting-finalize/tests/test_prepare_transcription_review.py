import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_transcription_review.py"
SPEC = importlib.util.spec_from_file_location("prepare_transcription_review", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PrepareTranscriptionReviewTest(unittest.TestCase):
    def test_export_and_import_round_trip_with_human_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "canonical.jsonl"
            transcript.write_text(
                json.dumps({"start": 1.0, "end": 2.5, "speaker": "A", "text": "ExampleOrg test", "mean_confidence": 0.75}) + "\n"
            )
            review = root / "review.tsv"
            reference = root / "reference.jsonl"
            self.assertEqual(MODULE.export_review(transcript, review, 1.0), 1)
            with review.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            rows[0]["human_speaker"] = "John"
            rows[0]["human_text"] = "ExampleOrg corrected"
            rows[0]["overlap"] = "true"
            self.assertEqual(rows[0]["audio_start"], "0.0")
            self.assertEqual(rows[0]["audio_end"], "1.5")
            self.assertEqual(rows[0]["review_priority"], "medium")
            self.assertEqual(rows[0]["review_reason"], "low confidence")
            with review.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=MODULE.FIELDS, delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
            self.assertEqual(MODULE.import_review(review, reference), 1)
            result = json.loads(reference.read_text())
            self.assertEqual(result["speaker"], "John")
            self.assertEqual(result["text"], "ExampleOrg corrected")
            self.assertTrue(result["overlap"])
            self.assertEqual(reference.stat().st_mode & 0o777, 0o600)

    def test_import_rejects_empty_human_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review = root / "review.tsv"
            with review.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=MODULE.FIELDS, delimiter="\t")
                writer.writeheader()
                writer.writerow({"row": 1, "start": 0, "end": 1, "speaker": "A", "text": "x", "human_speaker": "A", "human_text": "", "overlap": "false", "reviewer_notes": ""})
            with self.assertRaisesRegex(ValueError, "empty human_text"):
                MODULE.import_review(review, root / "reference.jsonl")

    def test_priority_marks_unknown_and_suspicious_terms(self):
        priority, reason = MODULE.review_priority(
            {"speaker": "Unknown", "text": "chat GBT to claw", "mean_confidence": 0.4}
        )
        self.assertEqual(priority, "high")
        self.assertIn("unknown speaker", reason)
        self.assertIn("technical-term", reason)


if __name__ == "__main__":
    unittest.main()
