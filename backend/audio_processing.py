import os
import subprocess
import tempfile

from stt import transcribe_with_fallback


def convert_to_wav(input_path: str, output_path: str):
    """
    Convert browser WebM/Opus audio into
    a standard mono 16kHz WAV file
    """

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-ar",
            "16000",
            "-ac",
            "1",
            output_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def transcribe_audio(audio_path: str):
    """
    Transcribe the final recording through the provider
    chain (OpenAI first, Ollama fallback).
    Returns (normalized_segments, provider_name).
    """

    return transcribe_with_fallback(audio_path)


def process_audio(audio_bytes: bytes):
    with tempfile.TemporaryDirectory() as temp_dir:
        webm_path = os.path.join(
            temp_dir,
            "meeting.webm"
        )

        wav_path = os.path.join(temp_dir, "meeting.wav")

        with open(webm_path, "wb") as f:
            f.write(audio_bytes)

        convert_to_wav(webm_path, wav_path)

        return transcribe_audio(wav_path)
