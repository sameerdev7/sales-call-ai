"""
Gemini 3.5 Transcribe provider, via the Interactions API.

Diarization + word-level timestamps together cap a single request at
30 minutes of audio (Google's documented limit). Longer recordings
are split into <=25-minute chunks with a safety margin, each
transcribed independently, then stitched back together with time
offsets applied.

Diarization identity is NOT stable across independent Gemini calls:
"spk_1" in one chunk is not guaranteed to be the same person as
"spk_1" in another. Speaker labels are namespaced per chunk
(e.g. "c0_spk_1", "c1_spk_1") so reconciliation - which resolves each
label to a real Meet speaker independently via caption-timestamp
overlap - never conflates two different people under one label.
"""

import os
import subprocess
import tempfile
import time

from .base import STTProvider

MODEL = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe")

# Stay comfortably under Google's 30-minute cap for
# diarization + word timestamps, in case the last chunk runs long.
MAX_CHUNK_SECONDS = 25 * 60

# Consecutive same-speaker words more than this far apart in time
# are treated as separate utterances rather than merged into one
# segment (e.g. a pause, or the diarizer briefly losing the thread).
MERGE_GAP_SECONDS = 1.5

FILE_POLL_SECONDS = 1
FILE_POLL_MAX_ATTEMPTS = 60


def _get_client():
    # Reuses the same lazily-constructed GEMINI_API_KEY client used
    # for summarization, rather than building a second one here.
    from summarization.gemini import get_client
    return get_client()


def _probe_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _split_audio(audio_path, work_dir, chunk_seconds):
    pattern = os.path.join(work_dir, "gchunk_%04d.wav")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            audio_path,
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            pattern,
        ],
        check=True,
        capture_output=True,
    )

    chunk_paths = sorted(
        os.path.join(work_dir, name)
        for name in os.listdir(work_dir)
        if name.startswith("gchunk_")
    )

    chunks = []

    for path in chunk_paths:
        duration = _probe_duration(path)

        if duration > 0:
            chunks.append((path, duration))

    return chunks


def _parse_offset(offset):
    """Gemini returns offsets like '0.450s'; normalize to a float."""
    if offset is None:
        return 0.0

    try:
        return float(str(offset).rstrip("s"))
    except ValueError:
        return 0.0


def _extract_word_annotations(interaction):
    words = []

    for step in getattr(interaction, "steps", None) or []:
        for content in getattr(step, "content", None) or []:
            for annotation in getattr(content, "annotations", None) or []:
                if getattr(annotation, "type", None) == "word_info":
                    words.append(annotation)

    return words


def _words_to_segments(words, chunk_offset, chunk_label):
    """
    Group consecutive same-speaker words into utterance-level
    segments matching the {start, end, text, speaker} shape
    reconciliation expects.
    """
    segments = []
    current = None

    for word in words:
        raw_speaker = getattr(word, "speaker", None) or "spk_1"
        speaker = f"{chunk_label}_{raw_speaker}" if chunk_label else raw_speaker

        start = chunk_offset + _parse_offset(getattr(word, "start_offset", None))
        end = chunk_offset + _parse_offset(getattr(word, "end_offset", None))
        text = (getattr(word, "text", "") or "").strip()

        if not text:
            continue

        if (
            current
            and current["speaker"] == speaker
            and (start - current["end"]) < MERGE_GAP_SECONDS
        ):
            current["end"] = end
            current["text"] += " " + text
        else:
            if current:
                segments.append(current)

            current = {
                "start": start,
                "end": end,
                "text": text,
                "speaker": speaker,
            }

    if current:
        segments.append(current)

    return segments


class GeminiTranscribeProvider(STTProvider):
    def transcribe(self, audio_path: str) -> list:
        duration = _probe_duration(audio_path)

        with tempfile.TemporaryDirectory() as work_dir:
            if duration > MAX_CHUNK_SECONDS:
                chunks = _split_audio(audio_path, work_dir, MAX_CHUNK_SECONDS)
                print(
                    f"[STT] Gemini: {duration:.0f}s audio, "
                    f"split into {len(chunks)} chunk(s)"
                )
            else:
                chunks = [(audio_path, duration)]

            all_segments = []
            offset = 0.0
            multi_chunk = len(chunks) > 1

            for index, (path, chunk_duration) in enumerate(chunks):
                chunk_label = f"c{index}" if multi_chunk else ""

                words = self._transcribe_chunk(path)
                segments = _words_to_segments(words, offset, chunk_label)

                all_segments.extend(segments)
                offset += chunk_duration

        all_segments.sort(key=lambda s: s["start"])

        return all_segments

    def _transcribe_chunk(self, path):
        client = _get_client()

        audio_file = client.files.upload(file=path)

        # Uploaded files process asynchronously server-side; they
        # must reach an active state before an interaction can
        # reference them.
        attempts = 0
        while (
            getattr(audio_file, "state", None) == "PROCESSING"
            and attempts < FILE_POLL_MAX_ATTEMPTS
        ):
            time.sleep(FILE_POLL_SECONDS)
            audio_file = client.files.get(name=audio_file.name)
            attempts += 1

        interaction = client.interactions.create(
            model=MODEL,
            input=[
                {
                    "type": "audio",
                    "uri": audio_file.uri,
                    "mime_type": audio_file.mime_type,
                }
            ],
            generation_config={
                "transcription_config": {
                    "mode": {
                        "type": "verbatim",
                        "diarization_mode": "speaker",
                        "timestamp_granularities": ["word"],
                    },
                },
            },
        )

        return _extract_word_annotations(interaction)