import os 
import subprocess
import tempfile

from openai import OpenAI 

openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

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
    Transcribe and diarize the final recording. 
    Returns the raw openai transaction response 
    """
    
    with open(audio_path, "rb") as audio_file:
        response = (openai_client.audio.transcriptions.create(
            model="gpt-4o-transcribe-diarize", 
            file=audio_file,
            response_format="diarized_json", 
        ))
        
    return response 
    
    
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
        
        transcription = transcribe_audio(wav_path)
        
        return transcription