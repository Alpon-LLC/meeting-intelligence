from __future__ import annotations

import base64
import importlib.util
import json
import struct
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vexa_signal_batch_transcribe.py"
SPEC = importlib.util.spec_from_file_location("vexa_signal_batch_transcribe", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class BatchTranscriberTest(unittest.TestCase):
    def test_router_token_can_come_from_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "router.key"
            token_file.write_text("private-token\n")
            with patch.dict(MODULE.os.environ, {"MEETING_STT_KEY_FILE": str(token_file)}, clear=True):
                self.assertEqual(MODULE.router_token(), "private-token")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tape = self.root / "signal.jsonl"
        header = {"type": "captured_signal_header", "v": 1, "lane": "gmeet", "sample_rate": 16000}
        samples = struct.pack("<" + "f" * 1600, *([0.1] * 1600))
        frames = [
            {"seq": 0, "ts": 1_700_000_000_000, "speakerIndex": 0, "speakerName": "Alice", "pcm_len": 1600, "rms": 0.1, "pcm": base64.b64encode(samples).decode()},
            {"seq": 1, "ts": 1_700_000_000_100, "speakerIndex": 0, "pcm_len": 1600, "rms": 0.1, "pcm": base64.b64encode(samples).decode()},
            {"seq": 2, "ts": 1_700_000_020_000, "speakerIndex": 1, "speakerName": "Bob", "pcm_len": 1600, "rms": 0.1, "pcm": base64.b64encode(samples).decode()},
            {"seq": 3, "ts": 1_700_000_040_000, "speakerIndex": 0, "speakerName": "Carol", "pcm_len": 1600, "rms": 0.1, "pcm": base64.b64encode(samples).decode()},
        ]
        self.tape.write_text("\n".join(json.dumps(value) for value in [header, *frames]) + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_indexes_plans_and_renders_without_modifying_tape(self):
        before = MODULE.sha256_file(self.tape)
        header, frames = MODULE.index_tape(self.tape)
        chunks = MODULE.plan_chunks(header, frames)
        audio, mapping = MODULE.render_chunk(self.tape, header, chunks[0])
        self.assertEqual(before, MODULE.sha256_file(self.tape))
        self.assertEqual([chunk["speaker"] for chunk in chunks], ["Alice", "Bob", "Carol"])
        self.assertEqual([chunk["binding_generation"] for chunk in chunks], [1, 1, 2])
        self.assertTrue(audio.startswith(b"RIFF"))
        self.assertAlmostEqual(mapping[0]["source_start"], 1_700_000_000, places=3)
        self.assertAlmostEqual(MODULE.map_reconstructed_time(mapping, 0.15), 1_700_000_000.15, places=3)

    def test_normalizer_preserves_words_and_confidence(self):
        rows = MODULE.NORMALIZER.normalize_response(
            {"text": "ExampleOrg", "segments": [{"id": 0, "start": 0.1, "end": 0.5, "text": "ExampleOrg", "confidence": 0.98,
              "words": [{"word": "ExampleOrg", "start": 0.1, "end": 0.5, "probability": 0.99}]}]},
            speaker="Alice", speaker_key="channel-0", map_time=lambda value: 100 + value,
            iso_time=lambda value: f"iso:{value}",
        )
        self.assertEqual(rows[0]["speaker"], "Alice")
        self.assertEqual(rows[0]["mean_confidence"], 0.98)
        self.assertEqual(rows[0]["words"][0]["probability"], 0.99)


if __name__ == "__main__":
    unittest.main()
