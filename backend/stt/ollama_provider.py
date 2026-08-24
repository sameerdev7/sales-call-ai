import base64
import os
import subprocess
import tempfile

import httpx

from .base import STTProvider

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:e2b"
DEFAULT_CHUNK_SECONDS = 25
REQUEST_TIMEOUT_SECONDS = 300

# Common LLM hallucinations on silent/music chunks
HALLUCINATION_PHRASES = {
    "thank you.",
    "thanks for watching!",
    "thank you for watching!",
    "[music]",
    "[silence]",
    "...",
}

TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly. "
    "Output ONLY the spoken words verbatim, with no commentary, "
    "no speaker labels and no formatting."
)


def _env_int(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


class OllamaChunkedProvider(STTProvider):
    """
    Local transcription through Ollama using an audio-capable
    model (e.g. gemma4). Ollama accepts short 16kHz mono WAV
    clips via the chat 'images' field, so the recording is
    split into fixed-length chunks and stitched back together.

    There is NO diarization: every segment is emitted with a
    single neutral label and diarized=False. Speaker names are
    recovered later by reconciliation from Meet caption overlap.
    """

    def __init__(
        self,
        host=None,
        model=None,
        chunk_seconds=None,
    ):
        self.host = (
            host
            or os.getenv("OLLAMA_HOST")
            or DEFAULT_OLLAMA_HOST
        ).rstrip("/")

        self.model = (
            model
            or os.getenv("OLLAMA_STT_MODEL")
            or DEFAULT_MODEL
        )

        self.chunk_seconds = (
            chunk_seconds
            or _env_int("OLLAMA_CHUNK_SECONDS", DEFAULT_CHUNK_SECONDS)
        )

    def _split_audio(self, audio_path, work_dir):
        """
        Split the normalized WAV into chunk_seconds pieces.
        Returns ordered [(path, duration_seconds)].
        """
        pattern = os.path.join(work_dir, "chunk_%04d.wav")

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                audio_path,
                "-f",
                "segment",
                "-segment_time",
                str(self.chunk_seconds),
                "-reset_timestamps",
                "1",
                pattern,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        chunk_paths = sorted(
            os.path.join(work_dir, name)
            for name in os.listdir(work_dir)
            if name.startswith("chunk_")
        )

        chunks = []

        for path in chunk_paths:
            duration = self._probe_duration(path)

            if duration > 0:
                chunks.append((path, duration))

        return chunks

    @staticmethod
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

    def _transcribe_chunk(self, wav_path):
        with open(wav_path, "rb") as audio_file:
            audio_b64 = base64.b64encode(audio_file.read()).decode()

        response = httpx.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": TRANSCRIBE_PROMPT,
                        "images": [audio_b64],
                    }
                ],
                "stream": False,
                "think": False,
                "options": {"num_ctx": 8192},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        response.raise_for_status()

        message = response.json().get("message", {})

        return (message.get("content") or "").strip()

    @staticmethod
    def _is_hallucination(text):
        return text.lower() in HALLUCINATION_PHRASES

    def transcribe(self, audio_path: str) -> list:
        segments = []
        offset = 0.0

        with tempfile.TemporaryDirectory() as work_dir:
            chunks = self._split_audio(audio_path, work_dir)

            print(
                f"[STT] Ollama: {len(chunks)} chunk(s) "
                f"via {self.model} at {self.host}"
            )

            for index, (path, duration) in enumerate(chunks):
                text = self._transcribe_chunk(path)

                if not text or self._is_hallucination(text):
                    print(
                        f"[STT] Ollama: chunk {index} empty/"
                        f"hallucinated, skipped"
                    )
                    offset += duration
                    continue

                segments.append({
                    "start": round(offset, 3),
                    "end": round(offset + duration, 3),
                    "text": text,
                    "speaker": "Unknown",
                    "diarized": False,
                })

                offset += duration

        return segments
