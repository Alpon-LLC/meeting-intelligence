#!/usr/bin/env python3
"""
sparse-detect.py — Canonical sparse-transcript detection.

Standalone script replicating the detection algorithm from
references/sparse-transcript-detection.md. Takes a transcript.json
path and exits 0 (PASS) or 1 (FLAG) with a machine-readable reason.

Usage:
    python3 sparse-detect.py <transcript.json> [--verbose]

Flags:
    --verbose   Print detailed breakdown (speakers, counts, thresholds)

Exit codes:
    0  PASS — transcript is valid
    1  FLAG — transcript is sparse (human review needed)
    2  ERROR — file not found, unparseable, etc.
"""

import json, sys, os

FILLER_WORDS = {'um', 'uh', 'ah', 'er', 'like', 'yeah', 'ok', 'so', 'uhm'}


def should_flag_as_sparse(transcript_path, verbose=False):
    """Returns (bool, str) — True + reason if flagged, False + None if pass."""
    if not os.path.exists(transcript_path):
        return True, "file_not_found"

    with open(transcript_path, 'r') as f:
        raw = f.read()

    # Normalize: strip trailing dots after closing braces (Pitfall #40)
    raw = raw.replace('}.', '}')

    # Split into raw lines for line count
    raw_lines = [line.strip() for line in raw.split('\n') if line.strip()]

    # Parse JSON entries — handle multi-line JSON objects (text fields with \n).
    # Strategy: accumulate lines until json.loads(buffer) succeeds.
    entries = []
    buffer = ''
    for line in raw_lines:
        buffer += line
        try:
            obj = json.loads(buffer)
            entries.append(obj)
            buffer = ''
        except json.JSONDecodeError:
            continue

    if buffer.strip():
        try:
            obj = json.loads(buffer)
            entries.append(obj)
        except json.JSONDecodeError:
            pass

    # Threshold 1: Empty transcript
    if len(entries) == 0:
        return True, "empty_transcript"

    # Step 0: Bot-self utterance pre-filter
    human_entries = [e for e in entries if e.get('speaker', '') != 'You']
    bot_self_count = len(entries) - len(human_entries)
    if verbose:
        print(f"  Raw lines: {len(raw_lines)}")
        print(f"  Parsed entries: {len(entries)}")
        print(f"  Bot-self (You) entries: {bot_self_count}")
        print(f"  Human entries: {len(human_entries)}")

    if len(human_entries) == 0:
        return True, "all_entries_bot_self"
    entries = human_entries

    # Collapse consecutive same-speaker + same-timestamp
    collapsed = []
    for entry in entries:
        ts = entry.get('ts', '')
        speaker = entry.get('speaker', 'Unknown')
        text = entry.get('text', '')
        if collapsed and collapsed[-1]['speaker'] == speaker and collapsed[-1]['time'] == ts:
            collapsed[-1]['text'] += ' ' + text
        else:
            collapsed.append({'time': ts, 'speaker': speaker, 'text': text})

    if verbose:
        print(f"  Collapsed utterances: {len(collapsed)}")
        speakers = {}
        for e in entries:
            s = e.get('speaker', 'Unknown')
            speakers[s] = speakers.get(s, 0) + 1
        print(f"  Speakers: {speakers}")

    # Threshold 2: Very few raw lines AND minimal collapse
    if len(raw_lines) <= 4 and len(collapsed) <= 1:
        if len(collapsed) == 1:
            text = collapsed[0]['text'].lower()
            non_filler_words = [w for w in text.split() if w.strip('.,!?;:') not in FILLER_WORDS]
            non_filler_text = ' '.join(non_filler_words)
            if len(non_filler_text) > 20:
                pass  # Substantive content — let Threshold 3+7 gate it
            else:
                return True, "minimal_collapse_4_to_1"
        else:
            return True, "minimal_collapse_4_to_1"

    # Threshold 3: Collapsed size
    collapsed_text = "\n".join([f"[{e['time']}] {e['speaker']}: {e['text']}" for e in collapsed])
    if len(collapsed_text) < 100:
        return True, "sparse_collapsed_<100chars"

    # Threshold 4: Filler-heavy check
    total_chars = sum(len(e.get('text', '')) for e in entries)
    is_healthy_overall = len(collapsed) > 50 and total_chars > 5000
    if not is_healthy_overall:
        for utterance in collapsed:
            text = utterance['text'].lower()
            words = text.split()
            if len(words) == 0:
                continue
            filler_count = sum(1 for w in words if w.strip('.,!?;:') in FILLER_WORDS)
            non_filler_words = [w for w in words if w.strip('.,!?;:') not in FILLER_WORDS]
            non_filler_text = ' '.join(non_filler_words)
            has_non_filler = len(non_filler_words) > 0
            if (filler_count / len(words)) > 0.5 and has_non_filler and len(non_filler_text) < 20:
                return True, "filler_heavy_utterance"

    # Threshold 5: Short-utterance density
    short_count = sum(1 for e in entries if len(e['text'].strip().split()) <= 3)
    if verbose:
        print(f"  Short utterances (<=3 words): {short_count}/{len(entries)} = {short_count/len(entries)*100:.1f}%")
    if len(entries) > 0 and (short_count / len(entries)) > 0.6:
        if total_chars < 300:
            return True, "short_utterance_dense"

    # Threshold 6: Single-timestamp burst > 80%
    ts_values = [e.get('ts', '') for e in entries]
    if len(ts_values) > 0:
        most_common_ts = max(set(ts_values), key=ts_values.count)
        burst_ratio = ts_values.count(most_common_ts) / len(ts_values)
        if verbose:
            print(f"  Most common ts: {most_common_ts}, burst ratio: {burst_ratio*100:.1f}%")
        if burst_ratio > 0.8:
            is_substantive = False
            for u in collapsed:
                nfw = [w for w in u['text'].lower().split() if w.strip('.,!?;:') not in FILLER_WORDS]
                nft = ' '.join(nfw)
                if len(nft) > 20:
                    is_substantive = True
                    break
            if not is_substantive:
                return True, "single_timestamp_burst"

    # Threshold 7: Global substantial-utterance ratio
    substantial_count = sum(
        1 for u in collapsed
        if len([w for w in u['text'].lower().split() if w.strip('.,!?;:') not in FILLER_WORDS]) >= 5
    )
    if verbose:
        print(f"  Substantial utterances (>=5 non-filler words): {substantial_count}/{len(collapsed)} = {substantial_count/len(collapsed)*100:.1f}%")
    if len(collapsed) > 0 and (substantial_count / len(collapsed)) < 0.3:
        return True, "mostly_filler"

    return False, None


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <transcript.json> [--verbose]", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    verbose = "--verbose" in sys.argv

    if not os.path.exists(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)

    try:
        flagged, reason = should_flag_as_sparse(path, verbose=verbose)
    except json.JSONDecodeError as e:
        print(f"ERROR: unparseable JSON: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if flagged:
        print(f"FLAG: {reason}")
        sys.exit(1)
    else:
        print("PASS: Transcript is valid")
        sys.exit(0)


if __name__ == "__main__":
    main()
