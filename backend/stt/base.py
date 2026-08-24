from abc import ABC, abstractmethod


class STTProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str) -> list:
        """
        Transcribe an audio file and return normalized segments.

        Each segment is a dict:
            {
                "start": float (seconds from audio start),
                "end": float (seconds from audio start),
                "text": str,
                "speaker": str (provider-specific label),
            }
        """
