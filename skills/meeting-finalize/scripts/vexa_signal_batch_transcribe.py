#!/usr/bin/env python3
"""Replay a Vexa captured-signal.v1 tape through 9router Nova-3 in bounded batch chunks."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import math
import mimetypes
import os
import random
import struct
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ADAPTER_VERSION = "vexa-signal-batch.v2"
SUPPORTED_LANGUAGES = frozenset({"en", "multi", "vi"})
MAX_CHUNK_SECONDS = 120.0
MAX_COALESCE_GAP_SECONDS = 15.0
VOICE_RMS_FLOOR = 0.0025

NORMALIZER_PATH = Path(__file__).with_name("deepgram_response_normalizer.py")
SPEC = importlib.util.spec_from_file_location("deepgram_response_normalizer", NORMALIZER_PATH)
NORMALIZER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(NORMALIZER)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def index_tape(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frames: list[dict[str, Any]] = []
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
            if record.get("type") == "hint" or "pcm" not in record:
                continue
            if not isinstance(record.get("speakerIndex"), int) or not isinstance(record.get("ts"), (int, float)):
                continue
            frames.append({
                "offset": offset,
                "line_bytes": len(line),
                "seq": int(record.get("seq", len(frames))),
                "ts": float(record["ts"]),
                "speaker_index": record["speakerIndex"],
                "speaker_name": record.get("speakerName"),
                "pcm_len": int(record.get("pcm_len", 0)),
                "rms": float(record.get("rms", 0)),
            })
    if not frames:
        raise ValueError("signal tape contains no audio frames")
    return header, frames


def plan_chunks(header: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sample_rate = int(header.get("sample_rate") or 16000)
    channels: dict[int, list[dict[str, Any]]] = {}
    for frame in frames:
        channels.setdefault(frame["speaker_index"], []).append(frame)

    chunks: list[dict[str, Any]] = []
    for channel, channel_frames in sorted(channels.items()):
        bindings: list[dict[str, Any]] = []
        current_binding: dict[str, Any] | None = None
        for frame in sorted(channel_frames, key=lambda item: (item["ts"], item["seq"])):
            if frame["rms"] < VOICE_RMS_FLOOR:
                continue
            supplied = frame.get("speaker_name")
            supplied = supplied.strip() if isinstance(supplied, str) and supplied.strip() else None
            supplied_known = supplied not in {None, "Speaker", "Unknown"}
            gap = math.inf if current_binding is None else (frame["ts"] - current_binding["frames"][-1]["ts"]) / 1000.0
            identity_changed = bool(
                current_binding and supplied_known and current_binding["speaker"] not in {"Unknown", supplied}
            )
            if current_binding is None or gap > MAX_COALESCE_GAP_SECONDS or identity_changed:
                current_binding = {
                    "speaker_index": channel,
                    "binding_generation": len(bindings) + 1,
                    "speaker": supplied if supplied_known else "Unknown",
                    "frames": [],
                }
                bindings.append(current_binding)
            elif supplied_known and current_binding["speaker"] == "Unknown":
                # Vexa often emits an unnamed frame immediately before the glow/display name binds.
                # Rename that still-open binding rather than splitting one human into two speakers.
                current_binding["speaker"] = supplied
            current_binding["frames"].append(frame)

        for binding in bindings:
            current: list[dict[str, Any]] = []
            current_seconds = 0.0
            for frame in binding["frames"]:
                duration = frame["pcm_len"] / sample_rate
                if current and current_seconds + duration > MAX_CHUNK_SECONDS:
                    chunks.append({**binding, "frames": current})
                    current = []
                    current_seconds = 0.0
                current.append(frame)
                current_seconds += duration
            if current:
                chunks.append({**binding, "frames": current})
    chunks.sort(key=lambda chunk: chunk["frames"][0]["ts"])
    return chunks


def load_frame(tape_handle, metadata: dict[str, Any]) -> dict[str, Any]:
    tape_handle.seek(metadata["offset"])
    return json.loads(tape_handle.readline())


def render_chunk(tape: Path, header: dict[str, Any], chunk: dict[str, Any]) -> tuple[bytes, list[dict[str, float]]]:
    sample_rate = int(header.get("sample_rate") or 16000)
    pcm16 = bytearray()
    offset_map: list[dict[str, float]] = []
    reconstructed = 0.0
    with tape.open("rb") as handle:
        for metadata in chunk["frames"]:
            record = load_frame(handle, metadata)
            raw = base64.b64decode(record["pcm"], validate=True)
            if len(raw) % 4:
                raise ValueError("float32 PCM frame is not aligned")
            sample_count = len(raw) // 4
            frame_duration = sample_count / sample_rate
            for (sample,) in struct.iter_unpack("<f", raw):
                value = max(-1.0, min(1.0, sample))
                pcm16.extend(struct.pack("<h", round(value * (32767 if value >= 0 else 32768))))
            source_start = metadata["ts"] / 1000.0
            offset_map.append({
                "reconstructed_start": reconstructed,
                "reconstructed_end": reconstructed + frame_duration,
                "source_start": source_start,
                "source_end": source_start + frame_duration,
            })
            reconstructed += frame_duration

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(pcm16))
    return buffer.getvalue(), offset_map


def map_reconstructed_time(offset_map: list[dict[str, float]], value: float) -> float:
    if not offset_map:
        return value
    for item in offset_map:
        if value <= item["reconstructed_end"]:
            relative = max(0.0, value - item["reconstructed_start"])
            return item["source_start"] + min(relative, item["source_end"] - item["source_start"])
    return offset_map[-1]["source_end"]


def multipart_body(fields: list[tuple[str, str]], audio: bytes) -> tuple[bytes, str]:
    boundary = f"----exampleorg-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append((
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        ).encode())
    parts.append((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def transcription_fields(language: str, keyterms: list[str]) -> list[tuple[str, str]]:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported transcription language: {language}")
    fields = [("model", os.environ.get("MEETING_STT_MODEL", "whisper-1")), ("language", language), ("response_format", "verbose_json")]
    fields.extend(("keyterm", term) for term in keyterms)
    return fields


def request_transcription(url: str, token: str, audio: bytes, keyterms: list[str], language: str = "en", retries: int = 3) -> dict[str, Any]:
    fields = transcription_fields(language, keyterms)
    body, boundary = multipart_body(fields, audio)
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url.rstrip("/") + "/v1/audio/transcriptions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            retryable = error.code in {429, 502, 503, 504}
            if not retryable or attempt >= retries:
                detail = error.read(2048).decode("utf-8", "replace")
                raise RuntimeError(f"STT HTTP {error.code}: {detail}") from error
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0, 2 ** attempt + random.random())
            time.sleep(delay)
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt >= retries:
                raise RuntimeError("STT network failure after retries") from error
            time.sleep(min(30.0, 2 ** attempt + random.random()))
    raise AssertionError("unreachable")


def iso_epoch(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def router_token() -> str | None:
    value = os.environ.get("MEETING_STT_KEY")
    if value:
        return value.strip() or None
    path = os.environ.get("MEETING_STT_KEY_FILE")
    if not path:
        return None
    value = Path(path).read_text().strip()
    return value or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-tape", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--live-transcript", type=Path)
    parser.add_argument("--lifecycle-metadata", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--router-url", default=os.environ.get("MEETING_STT_URL", "http://127.0.0.1:19083"))
    parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--language", choices=sorted(SUPPORTED_LANGUAGES), default="en")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    context = json.loads(args.context.read_text())
    if context.get("version") != "meeting-stt-context.v1" or context.get("language") != "en":
        raise ValueError("invalid transcription context")
    keyterms = [item["text"] for item in context.get("keyterms", [])]
    context_sha = sha256_file(args.context)
    tape_sha = sha256_file(args.signal_tape)
    header, frames = index_tape(args.signal_tape)
    chunks = plan_chunks(header, frames)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_dir = args.output_dir / "provider-responses"
    raw_dir.mkdir(mode=0o700, exist_ok=True)
    if args.live_transcript:
        atomic_write(args.output_dir / "transcript.live.raw.json", args.live_transcript.read_bytes())

    prepared = []
    for index, chunk in enumerate(chunks):
        audio, offset_map = render_chunk(args.signal_tape, header, chunk)
        chunk_id = sha256_bytes(audio + context_sha.encode() + os.environ.get("MEETING_STT_MODEL", "whisper-1").encode() + args.language.encode() + ADAPTER_VERSION.encode())
        prepared.append({
            **chunk,
            "index": index,
            "chunk_id": chunk_id,
            "audio": audio,
            "audio_sha256": sha256_bytes(audio),
            "duration": sum(item["reconstructed_end"] - item["reconstructed_start"] for item in offset_map),
            "offset_map": offset_map,
            "response_path": raw_dir / f"{chunk_id}.json",
        })

    responses: dict[str, dict[str, Any]] = {}
    if not args.dry_run:
        missing_responses = [item for item in prepared if not item["response_path"].exists()]
        token = router_token()
        if missing_responses and not token:
            raise RuntimeError("MEETING_STT_KEY is required and must not be passed on the command line")

        def process(item):
            if item["response_path"].exists():
                return item["chunk_id"], json.loads(item["response_path"].read_text())
            assert token is not None
            response = request_transcription(args.router_url, token, item["audio"], keyterms, args.language)
            atomic_json(item["response_path"], response)
            return item["chunk_id"], response

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [executor.submit(process, item) for item in prepared]
            for future in as_completed(futures):
                chunk_id, response = future.result()
                responses[chunk_id] = response

    normalized_rows: list[dict[str, Any]] = []
    provider_entries = []
    for item in prepared:
        response = responses.get(item["chunk_id"])
        provider_entries.append({
            "chunk_id": item["chunk_id"],
            "speaker": item["speaker"],
            "speaker_key": f"vexa-channel-{item['speaker_index']}-binding-{item['binding_generation']}",
            "audio_sha256": item["audio_sha256"],
            "duration": item["duration"],
            "offset_map": item["offset_map"],
            "response_file": str(item["response_path"].relative_to(args.output_dir)) if response else None,
            "response_sha256": sha256_file(item["response_path"]) if response else None,
            "provider_request_id": (response or {}).get("provider_metadata", {}).get("request_id"),
            "response": response,
        })
        if response:
            normalized_rows.extend(NORMALIZER.normalize_response(
                response,
                speaker=item["speaker"],
                speaker_key=f"vexa-channel-{item['speaker_index']}-binding-{item['binding_generation']}",
                map_time=lambda value, mapping=item["offset_map"]: map_reconstructed_time(mapping, value),
                iso_time=iso_epoch,
            ))
        item.pop("audio", None)
        item.pop("frames", None)
        item["response_path"] = str(item["response_path"])

    normalized_rows.sort(key=lambda row: (row["start"], row["speaker_key"], row["segment_id"]))
    atomic_json(args.output_dir / "transcript.batch.provider.json", {
        "version": "transcript.batch.provider.v1",
        "chunks": provider_entries,
    })
    atomic_write(
        args.output_dir / "transcript.batch.normalized.jsonl",
        b"".join((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in normalized_rows),
    )
    manifest = {
        "version": "transcript.batch.manifest.v1",
        "adapter_version": ADAPTER_VERSION,
        "status": "planned" if args.dry_run else "completed",
        "model": os.environ.get("MEETING_STT_MODEL", "whisper-1"),
        "language": args.language,
        "signal_tape_sha256": tape_sha,
        "context_sha256": context_sha,
        "lifecycle_metadata_sha256": sha256_file(args.lifecycle_metadata) if args.lifecycle_metadata else None,
        "live_transcript_sha256": sha256_file(args.live_transcript) if args.live_transcript else None,
        "keyterm_count": len(keyterms),
        "frame_count": len(frames),
        "chunk_count": len(prepared),
        "normalized_segment_count": len(normalized_rows),
        "chunks": [{key: value for key, value in item.items() if key not in {"offset_map"}} for item in prepared],
    }
    atomic_json(args.output_dir / "transcript.batch.manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "chunks": len(prepared), "segments": len(normalized_rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
