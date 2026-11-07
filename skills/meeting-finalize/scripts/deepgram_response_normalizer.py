#!/usr/bin/env python3
"""Normalize a 9router/Deepgram verbose response without discarding provider evidence."""
from __future__ import annotations

from typing import Any, Callable


def normalize_response(
    response: dict[str, Any],
    *,
    speaker: str,
    speaker_key: str,
    map_time: Callable[[float], float],
    iso_time: Callable[[float], str],
    source: str = "deepgram_batch_nova3",
) -> list[dict[str, Any]]:
    segments = response.get("segments")
    if not isinstance(segments, list) or not segments:
        text = response.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return []
        segments = [{"id": 0, "start": 0.0, "end": float(response.get("duration") or 0), "text": text, "words": []}]

    normalized: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        text = segment.get("text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        start = map_time(float(segment.get("start") or 0))
        end = map_time(float(segment.get("end") or segment.get("start") or 0))
        words = []
        confidences = []
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            probability = float(word.get("probability", word.get("confidence", 0)) or 0)
            confidences.append(probability)
            words.append({
                "word": str(word.get("word", "")),
                "start": map_time(float(word.get("start") or 0)),
                "end": map_time(float(word.get("end") or word.get("start") or 0)),
                "probability": probability,
            })
        confidence = segment.get("confidence")
        mean_confidence = float(confidence) if confidence is not None else (
            sum(confidences) / len(confidences) if confidences else None
        )
        normalized.append({
            "segment_id": f"{speaker_key}:{segment.get('id', index)}:{start:.3f}",
            "ts": iso_time(start),
            "speaker": speaker,
            "speaker_key": speaker_key,
            "text": text.strip(),
            "start": round(start, 6),
            "end": round(max(start, end), 6),
            "words": words,
            "mean_confidence": mean_confidence,
            "source": source,
        })
    return normalized
