import base64
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "render_signal_tape_review_audio.py"
SPEC = importlib.util.spec_from_file_location("render_signal_tape_review_audio", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class RenderSignalTapeReviewAudioTest(unittest.TestCase):
    def test_sparse_channels_preserve_timeline_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tape = root / "signal.jsonl"
            samples = struct.pack("<ff", 0.25, -0.25)
            records = [
                {"type": "captured_signal_header", "v": 1, "lane": "gmeet", "sample_rate": 2},
                {"type": "audio", "ts": 1000, "speakerIndex": 1, "pcm": base64.b64encode(samples).decode()},
                {"type": "audio", "ts": 2000, "speakerIndex": 2, "pcm": base64.b64encode(samples).decode()},
            ]
            tape.write_text("\n".join(json.dumps(item) for item in records) + "\n")
            header, frames = MODULE.index_tape(tape)
            channels, origin, duration = MODULE.write_sparse_channels(tape, header, frames, root)
            self.assertEqual(origin, 1.0)
            self.assertEqual(duration, 2.0)
            self.assertEqual(len(channels), 2)
            self.assertEqual(channels[0].read_bytes()[:8], samples)
            self.assertEqual(channels[1].read_bytes()[8:16], samples)

    def test_rejects_non_gmeet_tape(self):
        with tempfile.TemporaryDirectory() as directory:
            tape = Path(directory) / "signal.jsonl"
            tape.write_text(json.dumps({"type": "captured_signal_header", "v": 1, "lane": "teams"}) + "\n")
            with self.assertRaisesRegex(ValueError, "gmeet"):
                MODULE.index_tape(tape)


if __name__ == "__main__":
    unittest.main()
