#!/usr/bin/env python3
"""Run Vexa batch transcription and canonicalization with atomic terminal state transitions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from group_transcript_turns import atomic_write, group_turns, render


TERMINAL = frozenset({"completed", "failed_nonretryable", "retry_exhausted", "disabled"})
READABILITY_TERMINAL = frozenset({"completed", "completed_degraded", "canonical_fallback", "disabled"})
BATCH_ARTIFACTS = (
    "transcript.batch.normalized.jsonl",
    "transcript.batch.manifest.json",
    "transcript.batch.provider.json",
)
INTERNAL_MEETING_MARKERS = ("standup", "sprint", "retro", "retrospective", "backlog", "internal")
VIETNAMESE_CHARACTERS = frozenset("ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def capture_has_frames(path: Path) -> bool:
    """False for a header-only captured-signal tape: no audio frame was captured.

    Anything that is not a captured-signal tape is left to the existing checks, so
    an unknown file is never silently classified as empty evidence.
    """
    if not path.is_file():
        return False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except ValueError:
            return True
        if not isinstance(header, dict) or header.get("type") != "captured_signal_header":
            return True
        return bool(handle.readline().strip())


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "transcription.state.v1"}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("transcription state must be an object")
    return value


def ready(state: dict[str, Any]) -> bool:
    return (
        all(state.get(field) in TERMINAL for field in ("live_status", "batch_status", "canonical_status"))
        and state.get("readability_status") in READABILITY_TERMINAL
    )


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["finalization_ready"] = ready(state)
    if state["finalization_ready"]:
        state["completed_at"] = state.get("completed_at") or now()
    state["updated_at"] = now()
    atomic_json(path, state)


def transcription_completed_and_valid(state: dict[str, Any]) -> bool:
    for status in ("live_status", "batch_status", "canonical_status"):
        if state.get(status) != "completed":
            return False
    for path_field, hash_field in (
        ("context_path", "context_sha256"),
        ("batch_manifest_path", "batch_manifest_sha256"),
        ("raw_transcript_path", "raw_transcript_sha256"),
        ("canonical_transcript_path", "canonical_transcript_sha256"),
        ("raw_signal_path", "raw_signal_sha256"),
        ("stt_tape_path", "stt_tape_sha256"),
    ):
        path = Path(state.get(path_field, ""))
        if not path.is_file() or digest(path) != state.get(hash_field):
            return False
    return True


def readability_completed_and_valid(state: dict[str, Any]) -> bool:
    if state.get("readability_status") not in READABILITY_TERMINAL:
        return False
    path = Path(state.get("readability_path", ""))
    return path.is_file() and digest(path) == state.get("readability_sha256")


def signal_origin(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as handle:
            header = json.loads(handle.readline())
        started_at = header.get("started_at") if isinstance(header, dict) else None
        if isinstance(started_at, str):
            datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            return started_at
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def batch_quality(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    texts = [str(row.get("text") or "").strip() for row in rows]
    word_counts = [len(text.split()) for text in texts]
    confidences = [float(row["mean_confidence"]) for row in rows if isinstance(row.get("mean_confidence"), (int, float))]
    substantial = sum(count >= 5 for count in word_counts)
    short = sum(count <= 3 for count in word_counts)
    segment_count = len(rows)
    word_count = sum(word_counts)
    words = [word.strip(".,!?;:()[]{}\"'").lower() for text in texts for word in text.split()]
    vietnamese_words = sum(any(character in VIETNAMESE_CHARACTERS for character in word) for word in words)
    substantial_ratio = substantial / segment_count if segment_count else 0.0
    short_ratio = short / segment_count if segment_count else 1.0
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    needs_fallback = (
        segment_count == 0
        or (segment_count >= 8 and substantial_ratio < 0.25)
        or (segment_count >= 8 and mean_confidence > 0 and mean_confidence < 0.65 and short_ratio > 0.5)
    )
    return {
        "segment_count": segment_count,
        "word_count": word_count,
        "substantial_segment_count": substantial,
        "substantial_ratio": round(substantial_ratio, 6),
        "short_segment_ratio": round(short_ratio, 6),
        "mean_confidence": round(mean_confidence, 6),
        "vietnamese_word_count": vietnamese_words,
        "vietnamese_word_ratio": round(vietnamese_words / word_count, 6) if word_count else 0.0,
        "needs_fallback": needs_fallback,
    }


def prefer_vietnamese(english: dict[str, Any], vietnamese: dict[str, Any]) -> bool:
    confidence_safe = vietnamese["mean_confidence"] >= max(0.55, english["mean_confidence"] - 0.08)
    language_evidence = vietnamese["vietnamese_word_count"] >= 5 and vietnamese["vietnamese_word_ratio"] >= 0.02
    if english["needs_fallback"] and not vietnamese["needs_fallback"]:
        return bool(confidence_safe and language_evidence)
    enough_more_words = vietnamese["word_count"] >= max(english["word_count"] + 50, int(english["word_count"] * 1.3))
    enough_more_substance = vietnamese["substantial_segment_count"] >= english["substantial_segment_count"] + 3
    return bool(enough_more_words and enough_more_substance and confidence_safe and language_evidence)


def is_internal_meeting(signal_tape: Path) -> bool:
    event_meta = signal_tape.parent / "event_meta.json"
    try:
        event = json.loads(event_meta.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    title = str(event.get("title") or event.get("summary") or "").casefold()
    return any(marker in title for marker in INTERNAL_MEETING_MARKERS)


def copy_batch_artifacts(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in BATCH_ARTIFACTS:
        shutil.copy2(source / name, destination / name)
    responses = source / "provider-responses"
    if responses.is_dir():
        shutil.copytree(responses, destination / "provider-responses", dirs_exist_ok=True)


def batch_command(args: argparse.Namespace, output_dir: Path, language: str) -> list[str]:
    command = [
        "python3", str(args.batch_script), "--signal-tape", str(args.signal_tape),
        "--context", str(args.context), "--output-dir", str(output_dir),
        "--router-url", args.router_url, "--concurrency", str(args.concurrency),
        "--language", language,
    ]
    if args.live_transcript:
        command.extend(["--live-transcript", str(args.live_transcript)])
    if args.lifecycle_metadata:
        command.extend(["--lifecycle-metadata", str(args.lifecycle_metadata)])
    return command


def canonical_fallback(canonical: Path, output: Path, title: str, origin: str | None = None) -> dict[str, Any]:
    rows = [json.loads(line) for line in canonical.read_text().splitlines() if line.strip()]
    turns = group_turns(rows)
    atomic_write(output, render(turns, title=title, origin=origin))
    return {
        "status": "canonical_fallback",
        "ai_attempted": False,
        "turn_count": len(turns),
        "edited_turn_count": 0,
        "fallback_turn_count": len(turns),
    }


def ensure_readability(
    args: argparse.Namespace,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> dict[str, Any]:
    canonical = args.output_dir / "transcript.canonical.jsonl"
    output = args.output_dir / "transcript-readable.md"
    title = str(getattr(args, "title", None) or "Readable transcript")
    editor_url = os.environ.get("TRANSCRIPT_EDITOR_API_URL", "").strip()
    editor_key_file = os.environ.get("TRANSCRIPT_EDITOR_API_KEY_FILE", "").strip()
    editor_model = os.environ.get("TRANSCRIPT_EDITOR_MODEL", "gemini/gemini-3.5-flash-lite").strip()
    verifier_model = os.environ.get("TRANSCRIPT_VERIFIER_MODEL", "").strip()
    origin = signal_origin(args.signal_tape)
    result: dict[str, Any]

    if editor_url and editor_key_file and editor_model:
        editor_script = Path(getattr(args, "editor_script", SCRIPT_DIR / "edit_transcript_with_ai.py"))
        command = [
            "python3", str(editor_script),
            "--canonical", str(canonical),
            "--context", str(args.context),
            "--output-dir", str(args.output_dir),
            "--title", title,
            "--api-url", editor_url,
            "--api-key-file", editor_key_file,
            "--model", editor_model,
        ]
        if verifier_model:
            command.extend(["--verifier-model", verifier_model])
        if origin:
            command.extend(["--origin", origin])
        edited = runner(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        manifest_path = args.output_dir / "transcript.editor.manifest.json"
        if edited.returncode == 0 and output.is_file() and manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            status = str(manifest.get("status") or "")
            if status in {"completed", "completed_degraded"}:
                result = {
                    "status": status,
                    "ai_attempted": True,
                    "model": str(manifest.get("model") or editor_model),
                    "verifier_model": str(manifest.get("verifier_model") or verifier_model or editor_model),
                    "turn_count": int(manifest.get("turn_count") or 0),
                    "edited_turn_count": int(manifest.get("edited_turn_count") or 0),
                    "fallback_turn_count": int(manifest.get("fallback_turn_count") or 0),
                }
            else:
                result = canonical_fallback(canonical, output, title, origin)
                result.update({"ai_attempted": True, "fallback_reason": "editor_manifest_invalid"})
        else:
            result = canonical_fallback(canonical, output, title, origin)
            result.update({"ai_attempted": True, "fallback_reason": "editor_process_failed"})
    else:
        result = canonical_fallback(canonical, output, title, origin)
        result["fallback_reason"] = "editor_unconfigured"

    result.update({
        "schema": "transcript.readability.v1",
        "source_sha256": digest(canonical),
        "output": output.name,
        "output_sha256": digest(output),
        "created_at": now(),
    })
    atomic_json(args.output_dir / "transcript.readability.json", result)
    return result


def run(
    args: argparse.Namespace,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    state = load_state(args.state)
    existing = args.output_dir / "transcript.canonical.jsonl"
    published = any((args.output_dir / name).exists() for name in
                    ("notion-page.json", ".finalized", "triage-context.json", ".vexa-publication.json"))
    valid = transcription_completed_and_valid(state)
    same_input = args.signal_tape.is_file() and state.get("raw_signal_sha256") == digest(args.signal_tape)
    safe_idempotent = valid and same_input and (not published or readability_completed_and_valid(state))
    if (published or (existing.is_file() and existing.stat().st_size > 0)) and not safe_idempotent:
        # Deliberately do not mutate state or any good artifacts. Recovery must
        # use an isolated output directory and explicit provenance-aware promotion.
        return {"status": "needs_review", "stage": "existing-artifacts", "reason": "replacement-requires-isolation"}
    if valid:
        if not readability_completed_and_valid(state):
            readability = ensure_readability(args, runner)
            state.update({
                "readability_status": readability["status"],
                "readability_path": str(args.output_dir / "transcript-readable.md"),
                "readability_sha256": readability["output_sha256"],
                "readability_ai_attempted": readability["ai_attempted"],
                "readability_edited_turn_count": readability["edited_turn_count"],
                "readability_fallback_turn_count": readability["fallback_turn_count"],
            })
            save_state(args.state, state)
        return {"status": "already_completed", "state": str(args.state)}

    if not capture_has_frames(args.signal_tape):
        # A replacement bot that entered a finished room yields a header-only tape.
        # Never manufacture an empty transcript from it: the post-call worker either
        # promotes the preserved capture or surfaces this for review.
        return {"status": "needs_review", "stage": "capture", "reason": "empty-capture-needs-review"}

    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.update({
        "live_status": "completed",
        "batch_status": "running",
        "canonical_status": "pending",
        "readability_status": "pending",
    })
    if args.meeting_end_at:
        datetime.fromisoformat(args.meeting_end_at.replace("Z", "+00:00"))
        state["meeting_end_at"] = args.meeting_end_at
    save_state(args.state, state)

    batch = runner(batch_command(args, args.output_dir, "en"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if batch.returncode != 0:
        state.update({
            "batch_status": "retry_exhausted",
            "canonical_status": "disabled",
            "readability_status": "disabled",
        })
        save_state(args.state, state)
        atomic_json(args.output_dir / "transcription.error.json", {
            "stage": "batch", "exit_code": batch.returncode, "recorded_at": now(),
        })
        return {"status": "failed", "stage": "batch"}

    english_quality = batch_quality(args.output_dir / "transcript.batch.normalized.jsonl")
    selection: dict[str, Any] = {
        "version": "transcript.language-selection.v1",
        "primary_language": "en",
        "selected_language": "en",
        "fallback_attempted": False,
        "english": english_quality,
        "created_at": now(),
    }
    fallback_enabled = os.environ.get("TRANSCRIPTION_VIETNAMESE_FALLBACK", "1").strip().lower() not in {"0", "false", "no"}
    if fallback_enabled and english_quality["needs_fallback"] and is_internal_meeting(args.signal_tape):
        candidate_root = args.output_dir / "language-candidates"
        english_dir = candidate_root / "en"
        vietnamese_dir = candidate_root / "vi"
        copy_batch_artifacts(args.output_dir, english_dir)
        vietnamese = runner(
            batch_command(args, vietnamese_dir, "vi"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        selection["fallback_attempted"] = True
        selection["fallback_language"] = "vi"
        selection["vietnamese_exit_code"] = vietnamese.returncode
        if vietnamese.returncode == 0:
            vietnamese_quality = batch_quality(vietnamese_dir / "transcript.batch.normalized.jsonl")
            selection["vietnamese"] = vietnamese_quality
            if prefer_vietnamese(english_quality, vietnamese_quality):
                copy_batch_artifacts(vietnamese_dir, args.output_dir)
                selection["selected_language"] = "vi"
        else:
            selection["fallback_error"] = "vietnamese_batch_failed"
    selection["selected_manifest_sha256"] = digest(args.output_dir / "transcript.batch.manifest.json")
    selection["selected_transcript_sha256"] = digest(args.output_dir / "transcript.batch.normalized.jsonl")
    language_selection_path = args.output_dir / "transcript.language-selection.json"
    atomic_json(language_selection_path, selection)

    normalized = args.output_dir / "transcript.batch.normalized.jsonl"
    batch_manifest = args.output_dir / "transcript.batch.manifest.json"
    stt_tape = args.output_dir / "transcript.batch.provider.json"
    state.update({"batch_status": "completed", "canonical_status": "running"})
    save_state(args.state, state)

    canonical_command = [
        "python3", str(args.canonical_script), "--normalized", str(normalized),
        "--context", str(args.context), "--output-dir", str(args.output_dir),
    ]
    if args.aliases:
        canonical_command.extend(["--aliases", str(args.aliases)])
    canonical = runner(canonical_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if canonical.returncode != 0:
        state.update({"canonical_status": "failed_nonretryable", "readability_status": "disabled"})
        save_state(args.state, state)
        atomic_json(args.output_dir / "transcription.error.json", {
            "stage": "canonical", "exit_code": canonical.returncode, "recorded_at": now(),
        })
        return {"status": "failed", "stage": "canonical"}

    canonical_transcript = args.output_dir / "transcript.canonical.jsonl"
    state.update({
        "canonical_status": "completed",
        "context_path": str(args.context),
        "context_sha256": digest(args.context),
        "batch_manifest_path": str(batch_manifest),
        "batch_manifest_sha256": digest(batch_manifest),
        "raw_transcript_path": str(normalized),
        "raw_transcript_sha256": digest(normalized),
        "canonical_transcript_path": str(canonical_transcript),
        "canonical_transcript_sha256": digest(canonical_transcript),
        "raw_signal_path": str(args.signal_tape),
        "raw_signal_sha256": digest(args.signal_tape),
        "stt_tape_path": str(stt_tape),
        "stt_tape_sha256": digest(stt_tape),
        "transcription_language": selection["selected_language"],
        "language_selection_path": str(language_selection_path),
        "language_selection_sha256": digest(language_selection_path),
    })
    readability = ensure_readability(args, runner)
    state.update({
        "readability_status": readability["status"],
        "readability_path": str(args.output_dir / "transcript-readable.md"),
        "readability_sha256": readability["output_sha256"],
        "readability_ai_attempted": readability["ai_attempted"],
        "readability_edited_turn_count": readability["edited_turn_count"],
        "readability_fallback_turn_count": readability["fallback_turn_count"],
    })
    save_state(args.state, state)
    return {"status": "completed", "state": str(args.state), "finalization_ready": state["finalization_ready"]}


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-tape", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--live-transcript", type=Path)
    parser.add_argument("--lifecycle-metadata", type=Path)
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--router-url", default=os.environ.get("MEETING_STT_URL", "http://127.0.0.1:19083"))
    parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--meeting-end-at")
    parser.add_argument("--batch-script", type=Path, default=here / "vexa_signal_batch_transcribe.py")
    parser.add_argument("--canonical-script", type=Path, default=here / "canonicalize_transcript.py")
    parser.add_argument("--editor-script", type=Path, default=here / "edit_transcript_with_ai.py")
    parser.add_argument("--title", default="Readable transcript")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result))
    return 0 if result["status"] in {"completed", "already_completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
