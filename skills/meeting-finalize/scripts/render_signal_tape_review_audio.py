#!/usr/bin/env python3
"""Render a private mixed Opus review track from a captured-signal.v1 tape."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def index_tape(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frames = []
    with path.open("rb") as handle:
        first = handle.readline()
        if not first:
            raise ValueError("signal tape is empty")
        header = json.loads(first)
        if header.get("type") != "captured_signal_header" or header.get("v") != 1:
            raise ValueError("input is not captured-signal.v1")
        if header.get("lane") != "gmeet":
            raise ValueError("only participant-channel gmeet tapes are supported")
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            record = json.loads(line)
            if "pcm" not in record or not isinstance(record.get("speakerIndex"), int):
                continue
            if not isinstance(record.get("ts"), (int, float)):
                continue
            frames.append({"offset": offset, "ts": float(record["ts"]), "speaker_index": record["speakerIndex"]})
    if not frames:
        raise ValueError("signal tape contains no audio frames")
    return header, frames


def write_sparse_channels(tape: Path, header: dict[str, Any], frames: list[dict[str, Any]], directory: Path) -> tuple[list[Path], float, float]:
    sample_rate = int(header.get("sample_rate") or 16000)
    origin_ms = min(item["ts"] for item in frames)
    channels = sorted({item["speaker_index"] for item in frames})
    paths = {channel: directory / f"channel-{channel}.f32le" for channel in channels}
    handles = {channel: path.open("w+b") for channel, path in paths.items()}
    max_end_sample = 0
    try:
        with tape.open("rb") as source:
            for metadata in sorted(frames, key=lambda item: (item["ts"], item["speaker_index"], item["offset"])):
                source.seek(metadata["offset"])
                record = json.loads(source.readline())
                raw = base64.b64decode(record["pcm"], validate=True)
                if len(raw) % 4:
                    raise ValueError("float32 PCM frame is not aligned")
                start_sample = round((metadata["ts"] - origin_ms) * sample_rate / 1000.0)
                end_sample = start_sample + len(raw) // 4
                handles[metadata["speaker_index"]].seek(start_sample * 4)
                handles[metadata["speaker_index"]].write(raw)
                max_end_sample = max(max_end_sample, end_sample)
        for handle in handles.values():
            handle.truncate(max_end_sample * 4)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        for handle in handles.values():
            handle.close()
    return [paths[channel] for channel in channels], origin_ms / 1000.0, max_end_sample / sample_rate


def render(tape: Path, output: Path, ffmpeg: str) -> dict[str, Any]:
    header, frames = index_tape(tape)
    sample_rate = int(header.get("sample_rate") or 16000)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="vexa-review-audio-", dir=output.parent) as temp_name:
        temp = Path(temp_name)
        channel_paths, origin, duration = write_sparse_channels(tape, header, frames, temp)
        encoded = temp / "review.opus"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for channel in channel_paths:
            command.extend(["-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", str(channel)])
        inputs = "".join(f"[{index}:a]" for index in range(len(channel_paths)))
        command.extend([
            "-filter_complex",
            f"{inputs}amix=inputs={len(channel_paths)}:duration=longest:normalize=0,alimiter=limit=0.95[out]",
            "-map",
            "[out]",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            str(encoded),
        ])
        completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if completed.returncode:
            raise RuntimeError("ffmpeg could not render review audio")
        os.chmod(encoded, 0o600)
        os.replace(encoded, output)
    os.chmod(output, 0o600)
    result = {
        "version": "vexa-review-audio.v1",
        "source_sha256": sha256_file(tape),
        "output_sha256": sha256_file(output),
        "sample_rate": sample_rate,
        "channels_mixed": len({item["speaker_index"] for item in frames}),
        "frames": len(frames),
        "origin_unix_seconds": origin,
        "duration_seconds": duration,
    }
    metadata = output.with_suffix(output.suffix + ".json")
    fd, temporary = tempfile.mkstemp(prefix=f".{metadata.name}.", dir=metadata.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, metadata)
        os.chmod(metadata, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-tape", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    print(json.dumps(render(args.signal_tape, args.output, args.ffmpeg), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
