import importlib.util
import json
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "edit_transcript_with_ai.py"
SPEC = importlib.util.spec_from_file_location("edit_transcript_with_ai", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class EditTranscriptWithAiTest(unittest.TestCase):
    def test_parses_streamed_openai_compatible_response(self):
        payload = (
            'data: {"choices":[{"delta":{"content":"{\\\"turns\\\":["}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"{\\\"turn_id\\\":\\\"t\\\",\\\"edited_text\\\":\\\"Hi.\\\"}]"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"}"}}]}\n\n'
            'data: [DONE]\n'
        ).encode()
        self.assertEqual(
            MODULE.parse_completion_response(payload),
            {"turns": [{"turn_id": "t", "edited_text": "Hi."}]},
        )

    def test_groups_consecutive_segments_and_preserves_source_ids(self):
        turns = MODULE.group_turns([
            {"segment_id": "a", "ts": "2026-09-28T00:00:00Z", "speaker": "M", "text": "Well, well"},
            {"segment_id": "b", "ts": "2026-09-28T00:00:03Z", "speaker": "M", "text": "let's start."},
            {"segment_id": "c", "ts": "2026-09-28T00:00:04Z", "speaker": "N", "text": "Okay."},
        ])
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["segment_ids"], ["a", "b"])
        self.assertEqual(turns[0]["text"], "Well, well let's start.")

    def test_validator_rejects_changed_numbers_and_glossary(self):
        source = "Ship ExampleOrg 9router by 12:30 for $500."
        self.assertEqual(MODULE.validate_edit(source, source, ["ExampleOrg", "9router"]), [])
        reasons = MODULE.validate_edit(source, "Ship it by 13:30 for $600.", ["ExampleOrg", "9router"])
        self.assertIn("numbers_changed", reasons)
        self.assertIn("glossary_term_removed:ExampleOrg", reasons)

    def test_validator_rejects_changed_negation_url_email_and_excessive_contraction(self):
        source = (
            "I do not approve https://example.org/spec and owner@example.org should not ship this "
            "because the required review has not finished yet."
        )
        reasons = MODULE.validate_edit(source, "I approve shipping it.", [])
        self.assertIn("url_or_email_changed", reasons)
        self.assertIn("negation_changed", reasons)
        self.assertIn("contracted_beyond_limit", reasons)

    def test_removes_exact_word_and_sentence_repetitions(self):
        self.assertEqual(
            MODULE.remove_exact_local_repetitions(
                "I I I agree. Wait. Wait. Gradient gradient less harsh."
            ),
            "I agree. Wait. Gradient less harsh.",
        )

    def test_validator_rejects_word_substitution_or_reordering(self):
        self.assertIn(
            "words_added_or_reordered",
            MODULE.validate_edit("I have create a ticket.", "I have created a ticket.", []),
        )
        self.assertIn(
            "words_added_or_reordered",
            MODULE.validate_edit("alpha beta", "beta alpha", []),
        )

    def test_editor_accepts_cleanup_and_falls_back_on_invalid_change(self):
        turns = MODULE.group_turns([
            {"id": "1", "ts": "2026-09-28T00:00:00Z", "speaker": "M", "text": "I I think ExampleOrg ships 2 sites."},
            {"id": "2", "ts": "2026-09-28T00:00:05Z", "speaker": "N", "text": "It costs $500."},
        ])

        def requester(endpoint, key, model, messages, timeout):
            if "Verify an edited transcript" in messages[0]["content"]:
                return {"verdict": "pass", "issues": []}
            supplied = json.loads(messages[1]["content"])["turns"]
            return {"turns": [
                {"turn_id": supplied[0]["turn_id"], "edited_text": "I think ExampleOrg ships 2 sites."},
                {"turn_id": supplied[1]["turn_id"], "edited_text": "It costs $600."},
            ]}

        edited, ledger = MODULE.edit_turns(turns, ["ExampleOrg"], "http://editor", "key", "model", 10_000, 5, requester)
        self.assertEqual(edited[0]["edited_text"], "I think ExampleOrg ships 2 sites.")
        self.assertEqual(edited[0]["edit_status"], "edited")
        self.assertEqual(edited[1]["edited_text"], "It costs $500.")
        self.assertEqual(ledger[1]["status"], "fallback_original")
        self.assertIn("numbers_changed", ledger[1]["reasons"])

    def test_editor_preserves_source_when_model_errors(self):
        turns = MODULE.group_turns([
            {"id": "1", "ts": "2026-09-28T00:00:00Z", "speaker": "M", "text": "Original."},
        ])

        def requester(*args):
            raise OSError("offline")

        edited, ledger = MODULE.edit_turns(turns, [], "http://editor", "key", "model", 10_000, 5, requester)
        self.assertEqual(edited[0]["edited_text"], "Original.")
        self.assertEqual(ledger[0]["status"], "fallback_original")

    def test_rejected_model_rewrite_keeps_safe_exact_repetition_cleanup(self):
        turns = MODULE.group_turns([
            {"id": "1", "ts": "2026-09-28T00:00:00Z", "speaker": "M", "text": "I I create it."},
        ])

        def requester(endpoint, key, model, messages, timeout):
            if "Verify an edited transcript" in messages[0]["content"]:
                return {"verdict": "pass", "issues": []}
            return {"turns": [{"turn_id": "turn-000000", "edited_text": "I created it."}]}

        edited, ledger = MODULE.edit_turns(turns, [], "http://editor", "key", "model", 10_000, 5, requester)
        self.assertEqual(edited[0]["edited_text"], "I create it.")
        self.assertEqual(ledger[0]["status"], "fallback_safe_cleanup")
        self.assertIn("words_added_or_reordered", ledger[0]["reasons"])

    def test_failed_large_requests_are_retried_in_smaller_batches(self):
        turns = MODULE.group_turns([
            {"id": "1", "ts": "2026-09-28T00:00:00Z", "speaker": "M", "text": "I I agree."},
            {"id": "2", "ts": "2026-09-28T00:00:01Z", "speaker": "N", "text": "We we agree."},
        ])

        def requester(endpoint, key, model, messages, timeout):
            payload = json.loads(messages[1]["content"])["turns"]
            if len(payload) > 1:
                raise TimeoutError("batch too large")
            if "Verify an edited transcript" in messages[0]["content"]:
                return {"verdict": "pass", "issues": []}
            item = payload[0]
            return {"turns": [{
                "turn_id": item["turn_id"],
                "edited_text": MODULE.remove_exact_local_repetitions(item["text"]),
            }]}

        edited, ledger = MODULE.edit_turns(turns, [], "http://editor", "key", "model", 10_000, 5, requester)
        self.assertEqual([turn["edited_text"] for turn in edited], ["I agree.", "We agree."])
        self.assertEqual([item["status"] for item in ledger], ["edited", "edited"])

    def test_editor_preserves_source_when_verifier_is_unavailable(self):
        turns = MODULE.group_turns([
            {"id": "1", "ts": "2026-09-28T00:00:00Z", "speaker": "M", "text": "I I agree."},
        ])
        calls = 0

        def requester(endpoint, key, model, messages, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"turns": [{"turn_id": "turn-000000", "edited_text": "I agree."}]}
            raise OSError("verifier offline")

        edited, ledger = MODULE.edit_turns(turns, [], "http://editor", "key", "model", 10_000, 5, requester)
        self.assertEqual(edited[0]["edited_text"], "I agree.")
        self.assertEqual(ledger[0]["status"], "fallback_safe_cleanup")
        self.assertIn("verifier_error:OSError", ledger[0]["reasons"])

    def test_rendered_editor_output_uses_compact_turn_format(self):
        turns = MODULE.group_turns([
            {"ts": "2026-09-28T00:00:00Z", "speaker": "operator nguyen", "text": "I agree."},
        ])
        rendered = MODULE.render_markdown(turns, "Readable transcript", "2026-09-27T23:58:00Z")
        self.assertIn("[2:00] Operator Nguyen: I agree.", rendered)
        self.assertNotIn("## operator nguyen", rendered)


if __name__ == "__main__":
    unittest.main()
