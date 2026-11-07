from __future__ import annotations

import argparse
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-transcription-context.py"
SPEC = importlib.util.spec_from_file_location("build_transcription_context", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class ContextBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.event = self.root / "event.json"
        self.event.write_text(json.dumps({
            "event_id": "meeting-123",
            "title": "ExampleOrg Hermes planning",
            "attendees": [
                {"email": "alex@example.com", "name": "Alex Example"},
                {"email": "operator@example.org", "name": "Operator Nguyen"},
                {"email": "unknown@example.com"},
            ],
        }))
        self.roster = self.root / "team-roster.yaml"
        self.roster.write_text("team:\n  - email: operator@example.org\n    name: Operator Nguyen\n")
        self.glossary = self.root / "glossary.json"
        self.glossary.write_text(json.dumps({
            "version": "transcription-glossary.v1",
            "terms": [
                {"text": "ExampleOrg", "source": "organization", "priority": 95, "always": True},
                {"text": "Hermes", "source": "product", "priority": 90, "always": True},
                {"text": "exampleorg", "source": "organization", "priority": 1, "always": True},
            ],
        }))

    def tearDown(self):
        self.temp.cleanup()

    def args(self, **overrides):
        values = dict(
            meeting_id=None, calendar_event_id=None, event_title=None, event_meta=self.event,
            project_id=None, context_input=None, workspace=self.root / "workspace", glossary=self.glossary,
            schema=Path(__file__).resolve().parents[1] / "references" / "meeting-stt-context.v1.schema.json",
            roster=self.roster, corrections=None, generated_at="2026-09-25T10:00:00Z",
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_builds_ranked_dynamic_context_without_email_local_inference(self):
        context = MODULE.build_context(self.args())
        MODULE.validate_context(context, self.args().schema)
        texts = [entry["text"] for entry in context["keyterms"]]
        self.assertEqual(texts[:2], ["Alex Example", "Operator Nguyen"])
        self.assertIn("ExampleOrg", texts)
        self.assertIn("Hermes", texts)
        self.assertNotIn("unknown", [text.casefold() for text in texts])
        self.assertEqual(texts.count("ExampleOrg"), 1)
        self.assertEqual(context["canonical_entities"]["people"], ["Alex Example", "Operator Nguyen"])

    def test_atomic_output_is_private_and_reproducible(self):
        context = MODULE.build_context(self.args())
        output = self.root / "workspace" / "transcription.context.json"
        MODULE.atomic_write_private(output, context)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(json.loads(output.read_text()), context)
        self.assertTrue(all(value.startswith("sha256:") for value in context["source_fingerprints"].values()))

    def test_budget_discards_low_priority_terms_first(self):
        candidates = [
            {"text": f"High Priority Term {index:03d}", "source": "calendar_attendee", "priority": 100}
            for index in range(100)
        ] + [{"text": "Low Priority Tail", "source": "agenda", "priority": 1}]
        selected = MODULE.select_candidates(candidates)
        self.assertLessEqual(len(selected), 100)
        self.assertNotIn("Low Priority Tail", [item["text"] for item in selected])
        self.assertLessEqual(sum(max(1, (len(item["text"]) + 3) // 4) for item in selected), 500)

    def test_rejects_control_characters_and_stop_words(self):
        for value in ["bad\nterm", "the", "x", "a" * 81]:
            with self.assertRaises(ValueError):
                MODULE.normalize_term(value)

    def test_adds_bounded_notion_context_with_a_source_fingerprint(self):
        context_input = self.root / "transcription.context.input.json"
        context_input.write_text(json.dumps({
            "version": "meeting-stt-context-input.v1",
            "terms": [
                {"text": "Example Analytics", "source": "project", "priority": 94},
                {"text": "Open Graph", "source": "agenda", "priority": 72},
            ],
        }))
        context = MODULE.build_context(self.args(context_input=context_input))
        texts = [entry["text"] for entry in context["keyterms"]]
        self.assertIn("Example Analytics", texts)
        self.assertIn("Open Graph", texts)
        self.assertIn("notion_meeting_context", context["source_fingerprints"])


if __name__ == "__main__":
    unittest.main()
