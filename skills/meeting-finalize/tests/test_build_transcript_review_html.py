import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_transcript_review_html.py"
SPEC = importlib.util.spec_from_file_location("build_transcript_review_html", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class BuildTranscriptReviewHtmlTest(unittest.TestCase):
    def test_builds_private_player_and_escapes_script_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review = root / "review.tsv"
            fields = ["row_id", "audio_start", "audio_end", "speaker", "text", "human_text", "human_speaker", "overlap", "reviewer_notes"]
            with review.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fields, delimiter="\t")
                writer.writeheader()
                writer.writerow({"row_id": "1", "audio_start": "1", "audio_end": "2", "speaker": "A", "text": "hello </script>", "human_text": "hello", "human_speaker": "A", "overlap": "false", "reviewer_notes": ""})
            output = root / "review.html"
            self.assertEqual(MODULE.build(review, output, "audio.opus", "Test"), 1)
            document = output.read_text()
            self.assertIn("Download reviewed TSV", document)
            self.assertIn('src="audio.opus"', document)
            self.assertNotIn("hello </script>", document)
            self.assertIn("hello <\\/script>", document)

    def test_rejects_missing_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Path(directory) / "review.tsv"
            review.write_text("row_id\ttext\n1\thello\n")
            with self.assertRaisesRegex(ValueError, "missing"):
                MODULE.load_rows(review)


if __name__ == "__main__":
    unittest.main()
