"""
Speaker reconciliation.

Two modes depending on STT capabilities:

1. diarized (OpenAI gpt-4o-transcribe-diarize):
   Maps anonymous STT labels ("A", "B") to real Google Meet
   speaker names using timestamp-overlap evidence.

2. unlabeled (Ollama local transcription):
   No diarization available, so each segment is attributed
   directly to the Meet speaker whose caption window overlaps
   it most. Confidence = share of the segment covered by that
   speaker's evidence. Segments without any caption evidence
   stay "Unknown" — never guessed (architecture.md Rule 9).

Meet caption timestamps are epoch milliseconds sent ~1.5s after
the speech occurred (content.js debounce), so each caption is
treated as evidence that its speaker was talking in the window
[timestamp - CAPTION_BEFORE_MS, timestamp + CAPTION_AFTER_MS].

STT timestamps are seconds relative to the recording start,
anchored to wall-clock time via recording_started_at_ms
supplied by the extension when it begins capturing audio.
"""

CAPTION_BEFORE_MS = 4000
CAPTION_AFTER_MS = 1500
MERGE_GAP_MS = 1000

UNLABELED_SPEAKER = "Unknown"


def _is_unlabeled(stt_segments):
    return bool(stt_segments) and all(
        not segment.get("diarized", True)
        for segment in stt_segments
    )


def _resolve_anchor(recording_started_at_ms, meet_segments):
    if recording_started_at_ms:
        return int(recording_started_at_ms)

    if meet_segments:
        return int(
            min(s["timestamp"] for s in meet_segments)
            - CAPTION_BEFORE_MS
        )

    return None


def _meet_intervals(meet_segments):
    return [
        (
            segment["speaker"],
            int(segment["timestamp"]) - CAPTION_BEFORE_MS,
            int(segment["timestamp"]) + CAPTION_AFTER_MS,
        )
        for segment in meet_segments
        if segment.get("timestamp") is not None and segment.get("speaker")
    ]


def _overlap_matrix(meet_intervals, stt_intervals):
    weights = {}

    for label, stt_start, stt_end in stt_intervals:
        for meet_speaker, meet_start, meet_end in meet_intervals:
            overlap = min(stt_end, meet_end) - max(stt_start, meet_start)

            if overlap <= 0:
                continue

            speaker_weights = weights.setdefault(label, {})
            speaker_weights[meet_speaker] = (
                speaker_weights.get(meet_speaker, 0) + overlap
            )

    return weights


def build_speaker_mapping(meet_segments, stt_segments, recording_started_at_ms=None):
    """
    Labeled mode. Returns a report dict:
        {
            "mode": "diarized",
            "<stt label>": {
                "speaker": "<resolved name or None>",
                "confidence": float,
            }
        }
    """
    anchor = _resolve_anchor(recording_started_at_ms, meet_segments)

    meet_intervals = _meet_intervals(meet_segments)

    stt_intervals = []

    if anchor is not None:
        stt_intervals = [
            (
                segment["speaker"],
                anchor + int(segment["start"] * 1000),
                anchor + int(segment["end"] * 1000),
            )
            for segment in stt_segments
        ]

    weights = _overlap_matrix(meet_intervals, stt_intervals)

    mapping = {"mode": "diarized"}

    for segment in stt_segments:
        label = segment["speaker"]

        if label in mapping:
            continue

        speaker_weights = weights.get(label, {})
        total = sum(speaker_weights.values())

        if not speaker_weights or total <= 0:
            mapping[label] = {"speaker": None, "confidence": 0.0}
            continue

        best_speaker = max(
            speaker_weights,
            key=speaker_weights.get,
        )

        mapping[label] = {
            "speaker": best_speaker,
            "confidence": round(speaker_weights[best_speaker] / total, 3),
        }

    return mapping


def _attribute_segment(meet_intervals, start_ms, end_ms):
    """
    Unlabeled mode. Attribute one segment to the Meet speaker
    with the largest overlap share. Returns (speaker|None, confidence).
    """
    duration = max(1, end_ms - start_ms)

    best_speaker = None
    best_overlap = 0

    for meet_speaker, meet_start, meet_end in meet_intervals:
        overlap = min(end_ms, meet_end) - max(start_ms, meet_start)

        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = meet_speaker

    if best_speaker is None or best_overlap <= 0:
        return None, 0.0

    return best_speaker, round(best_overlap / duration, 3)


def _merge_resolved(resolved_entries):
    """
    Merge consecutive same-speaker entries within MERGE_GAP_MS.
    Input/Output items: {timestamp, _end_ms, speaker, text}
    """
    final_transcript = []

    for entry in resolved_entries:
        previous = (
            final_transcript[-1]
            if final_transcript
            else None
        )

        if (
            previous
            and previous["speaker"] == entry["speaker"]
            and entry["timestamp"] - previous["_end_ms"] <= MERGE_GAP_MS
        ):
            previous["text"] += " " + entry["text"]
            previous["_end_ms"] = entry["_end_ms"]
            continue

        final_transcript.append(dict(entry))

    for entry in final_transcript:
        entry.pop("_end_ms", None)

    return final_transcript


def _stt_windows(stt_segments, anchor):
    for segment in stt_segments:
        if anchor is not None:
            yield (
                segment,
                anchor + int(segment["start"] * 1000),
                anchor + int(segment["end"] * 1000),
            )
        else:
            yield (
                segment,
                int(segment["start"] * 1000),
                int(segment["end"] * 1000),
            )


def reconcile_unlabeled(meet_segments, stt_segments, recording_started_at_ms=None):
    """
    No-diarization path: attribute each STT segment to the
    dominant overlapping Meet speaker. Returns
    (final_transcript, mapping_report).
    """
    anchor = _resolve_anchor(recording_started_at_ms, meet_segments)

    meet_intervals = _meet_intervals(meet_segments)

    resolved_entries = []
    attribution_seconds = {}
    unknown_seconds = 0.0

    for segment, start_ms, end_ms in _stt_windows(stt_segments, anchor):
        speaker, confidence = _attribute_segment(
            meet_intervals,
            start_ms,
            end_ms,
        )

        if speaker is None:
            speaker = UNLABELED_SPEAKER
            unknown_seconds += (end_ms - start_ms) / 1000
        else:
            attribution_seconds[speaker] = (
                attribution_seconds.get(speaker, 0)
                + (end_ms - start_ms) / 1000
            )
            confidence = confidence

        resolved_entries.append({
            "timestamp": start_ms,
            "_end_ms": end_ms,
            "speaker": speaker,
            "text": segment["text"],
        })

    final_transcript = _merge_resolved(resolved_entries)

    report = {
        "mode": "unlabeled",
        "attribution_seconds": {
            speaker: round(secs, 1)
            for speaker, secs in attribution_seconds.items()
        },
        "unknown_seconds": round(unknown_seconds, 1),
    }

    return final_transcript, report


def reconcile_speakers(meet_segments, stt_segments, recording_started_at_ms=None):
    """
    Entry point. Dispatches on whether the provider produced
    diarized labels. Returns (final_transcript, mapping_report).
    """
    if _is_unlabeled(stt_segments):
        return reconcile_unlabeled(
            meet_segments,
            stt_segments,
            recording_started_at_ms,
        )

    mapping = build_speaker_mapping(
        meet_segments,
        stt_segments,
        recording_started_at_ms,
    )

    anchor = _resolve_anchor(recording_started_at_ms, meet_segments)

    resolved_entries = []

    for segment, start_ms, end_ms in _stt_windows(stt_segments, anchor):
        entry = mapping.get(segment["speaker"], {})
        resolved = entry.get("speaker")

        speaker = (
            resolved
            if resolved
            else f"Speaker {segment['speaker']}"
        )

        resolved_entries.append({
            "timestamp": start_ms,
            "_end_ms": end_ms,
            "speaker": speaker,
            "text": segment["text"],
        })

    final_transcript = _merge_resolved(resolved_entries)

    return final_transcript, mapping
