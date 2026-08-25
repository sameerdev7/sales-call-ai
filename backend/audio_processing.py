import os 
import tempfile

from stt import transcribe_with_fallback

def transcribe_audio(audio_path: str):
    return transcribe_with_fallback(audio_path)

def process_audio(audio_bytes: bytes):
    with tempfile.TemporaryDirectory() as temp_dir:
        webm_path = os.path.join(temp_dir, "meeting.webm")
        
        
        with open(webm_path, "wb") as f:
            f.write(audio_bytes)
            
        return transcribe_audio(webm_path)
    