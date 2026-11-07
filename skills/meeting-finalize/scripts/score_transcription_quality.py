#!/usr/bin/env python3
"""Score a normalized transcript against a human-timed JSONL reference."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

WORD = re.compile(r"[\w$'-]+", re.UNICODE)
NUMBER_OR_DATE = re.compile(r"(?:\$\s?\d[\d,]*(?:\.\d+)?|\b\d{1,4}(?:[-/]\d{1,2}){1,2}\b|\b\d[\d,]*(?:\.\d+)?\b)")


def rows(path: Path) -> list[dict[str, Any]]:
    result = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ValueError(f"invalid transcript row {number}")
        result.append(value)
    return result


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


def tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD.finditer(text)]


def edit_counts(reference: list[str], candidate: list[str]) -> tuple[int, int, int]:
    # Each cell is (distance, substitutions, deletions, insertions).
    previous = [(index, 0, 0, index) for index in range(len(candidate) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, observed in enumerate(candidate, 1):
            if expected == observed:
                current.append(previous[j - 1])
                continue
            substitution = previous[j - 1]
            deletion = previous[j]
            insertion = current[j - 1]
            choices = [
                (substitution[0] + 1, substitution[1] + 1, substitution[2], substitution[3]),
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
            ]
            current.append(min(choices))
        previous = current
    _, substitutions, deletions, insertions = previous[-1]
    return substitutions, deletions, insertions


def phrase_recall(reference_text: str, candidate_text: str, phrases: list[str]) -> dict[str, Any]:
    details = []
    expected_total = 0
    matched_total = 0
    for phrase in phrases:
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        expected = len(pattern.findall(reference_text))
        observed = len(pattern.findall(candidate_text))
        matched = min(expected, observed)
        expected_total += expected
        matched_total += matched
        details.append({"text": phrase, "expected": expected, "observed": observed, "matched": matched})
    return {
        "matched": matched_total,
        "expected": expected_total,
        "recall": (matched_total / expected_total) if expected_total else None,
        "details": details,
    }


def speaker_accuracy(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    matched = 0
    expected = 0
    for item in reference:
        if item.get("overlap") is True or not isinstance(item.get("speaker"), str):
            continue
        word_count = len(tokens(item["text"]))
        expected += word_count
        start, end = float(item.get("start", 0)), float(item.get("end", item.get("start", 0)))
        best = None
        best_overlap = 0.0
        for observed in candidate:
            overlap = max(0.0, min(end, float(observed.get("end", 0))) - max(start, float(observed.get("start", 0))))
            if overlap > best_overlap:
                best, best_overlap = observed, overlap
        if best and best.get("speaker") == item["speaker"]:
            matched += word_count
    return {"matched_words": matched, "expected_words": expected, "accuracy": matched / expected if expected else None}


def score(candidate: list[dict[str, Any]], reference: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    candidate_text = "\n".join(item["text"] for item in candidate)
    reference_text = "\n".join(item["text"] for item in reference)
    candidate_tokens = tokens(candidate_text)
    reference_tokens = tokens(reference_text)
    substitutions, deletions, insertions = edit_counts(reference_tokens, candidate_tokens)
    entities = context.get("canonical_entities", {})
    keyterms = [item["text"] for item in context.get("keyterms", []) if isinstance(item, dict) and isinstance(item.get("text"), str)]
    numbers_expected = Counter(NUMBER_OR_DATE.findall(reference_text))
    numbers_observed = Counter(NUMBER_OR_DATE.findall(candidate_text))
    numbers_matched = sum(min(count, numbers_observed[value]) for value, count in numbers_expected.items())
    return {
        "reference_words": len(reference_tokens),
        "candidate_words": len(candidate_tokens),
        "wer": (substitutions + deletions + insertions) / len(reference_tokens) if reference_tokens else None,
        "substitutions": substitutions,
        "omissions": deletions,
        "insertions": insertions,
        "keyterms": phrase_recall(reference_text, candidate_text, keyterms),
        "participant_names": phrase_recall(reference_text, candidate_text, list(entities.get("people", []))),
        "numbers_and_dates": {
            "matched": numbers_matched,
            "expected": sum(numbers_expected.values()),
            "accuracy": numbers_matched / sum(numbers_expected.values()) if numbers_expected else None,
            "expected_values": dict(numbers_expected),
            "observed_values": dict(numbers_observed),
        },
        "speaker_attribution_non_overlap": speaker_accuracy(reference, candidate),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score(rows(args.candidate), rows(args.reference), json.loads(args.context.read_text()))
    if args.state and args.state.exists():
        state = json.loads(args.state.read_text())
        if state.get("meeting_end_at") and state.get("completed_at"):
            start = datetime.fromisoformat(state["meeting_end_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(state["completed_at"].replace("Z", "+00:00"))
            report["meeting_end_to_canonical_seconds"] = (end - start).total_seconds()
    atomic_json(args.output, report)
    print(json.dumps({"output": str(args.output), "wer": report["wer"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
