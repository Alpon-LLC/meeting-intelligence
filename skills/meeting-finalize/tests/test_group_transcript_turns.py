import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "group_transcript_turns.py"
SPEC = importlib.util.spec_from_file_location("group_transcript_turns", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class GroupTranscriptTurnsTest(unittest.TestCase):
    def test_combines_only_consecutive_same_speaker_segments(self):
        rows = [
            {"ts": "2026-09-28T06:00:00Z", "speaker": "A", "text": "One."},
            {"ts": "2026-09-28T06:00:05Z", "speaker": "A", "text": "Two."},
            {"ts": "2026-09-28T06:00:07Z", "speaker": "B", "text": "Three."},
            {"ts": "2026-09-28T06:00:10Z", "speaker": "A", "text": "Four."},
        ]
        turns = MODULE.group_turns(rows)
        self.assertEqual([turn["speaker"] for turn in turns], ["A", "B", "A"])
        self.assertEqual(turns[0]["text"], "One. Two.")
        self.assertEqual(turns[0]["segments"], 2)

    def test_long_pause_starts_new_turn(self):
        rows = [
            {"ts": "2026-09-28T06:00:00Z", "speaker": "A", "text": "One."},
            {"ts": "2026-09-28T06:01:00Z", "speaker": "A", "text": "Two."},
        ]
        self.assertEqual(len(MODULE.group_turns(rows, max_gap_seconds=45)), 2)

    def test_renders_compact_single_line_turns(self):
        turns = MODULE.group_turns([
            {"ts": "2026-09-28T06:00:00Z", "speaker": "operator nguyen", "text": "Hi"},
            {"ts": "2026-09-28T06:05:19Z", "speaker": "Participant C Nguyen", "text": "Good morning"},
        ])
        rendered = MODULE.render(turns)
        self.assertIn("[0:00] Operator Nguyen: Hi", rendered)
        self.assertIn("[5:19] Participant C Nguyen: Good morning", rendered)
        self.assertNotIn("## Operator Nguyen", rendered)
        self.assertNotIn("â", rendered)

    def test_marks_a_dangling_final_connector_without_changing_words(self):
        turns = MODULE.group_turns([
            {"ts": "2026-09-28T06:00:00Z", "speaker": "Organizer", "text": "They stopped helping us. So"},
        ])
        self.assertIn("They stopped helping us. So…", MODULE.render(turns))


if __name__ == "__main__":
    unittest.main()
