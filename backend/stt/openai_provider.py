import os

from openai import OpenAI

from .base import STTProvider

STT_MODEL = "gpt-4o-transcribe-diarize"

_client = None


def _get_client():
    global _client

    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to backend/.env"
            )

        _client = OpenAI(api_key=api_key)

    return _client


class OpenAIDiarizedProvider(STTProvider):
    def transcribe(self, audio_path: str) -> list:
        with open(audio_path, "rb") as audio_file:
            response = _get_client().audio.transcriptions.create(
                model=STT_MODEL,
                file=audio_file,
                response_format="diarized_json",
            )

        segments = []

        for segment in getattr(response, "segments", None) or []:
            text = (segment.text or "").strip()

            if not text:
                continue

            segments.append({
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
                "speaker": segment.speaker,
            })

        segments.sort(key=lambda s: s["start"])

        return segments
