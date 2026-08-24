import os
import time
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from google import genai

from database import calls
from audio_processing import process_audio

load_dotenv()

app = FastAPI()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

# Config
BUFFER_SECONDS = 10
ALLOWED_SNAPSHOT_INTERVALS = [1, 5, 10]
DEFAULT_SNAPSHOT_INTERVAL = 5


# Root
@app.get("/")
def root():
    return {"status": "Sales Call AI Backend Running."}


@app.post("/audio/upload")
async def upload_audio(file: UploadFile = File(...), meeting_id: str = Form(...)):
    print(f"[AUDIO] Recieved recording " f"for meeting {meeting_id}")
    
    audio_bytes = await file.read()
    
    if not audio_bytes:
        return {
            "type": "audio_recording_error", 
            "error": "Empty audio file."
        }
        
    calls.update_one(
        {"meeting_id": meeting_id}, 
        {
            "$set": {
                "audio_status": "processing"
            }
        }
    )
    
    try:
        print("[AUDIO] Starting from Ffmpeg + STT...")
        
        transcription = process_audio(audio_bytes)
        
        print("[AUDIO] STT complete")
        print(transcription)
        
        
        calls.update_one(
            {"meeting_id": meeting_id}, 
            {
                "$set": {
                    "audio_status": "transcribed"
                }
            }
        )
        
        return {
            "type": "final_processing_started", 
            "meeting_id": meeting_id, 
            "status": "transcription_complete"
        }
        
        
    except Exception as e:
        print("[AUDIO] Processing failed: ", repr(e))
        calls.update_one({"meeting_id": meeting_id}, {
            "$set": {
                "audio_status": "error",
                "audio_error": str(e),
            }
        })
        
        return {
            "type": 
                "audio_recording_error", 
            "meeting_id": meeting_id, 
            "error": str(e)
        }

# Web Socket
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Chrome extension connected.")

    # meeting state
    meeting_id = str(uuid.uuid4())
    print("Meeting id: ", meeting_id)

    await websocket.send_json({
        "type": "meeting_started", 
        "meeting_id": meeting_id
    })
    
    meeting_start_time = time.time()

    # running summary used by the live assistant
    current_summary = ""

    # new transcript waiting for next gemini api call
    transcript_buffer = []

    # gemini timing
    last_summary_time = time.time()

    # snapshot training
    snapshot_interval = DEFAULT_SNAPSHOT_INTERVAL
    last_snapshot_interval = meeting_start_time

    # create call document
    calls.update_one(
        {"meeting_id": meeting_id},
        {
            "$setOnInsert": {
                "meeting_id": meeting_id,
                "started_at": meeting_start_time,
                "segments": [],
                "summary_snapshots": [],
                "current_summary": "",
            }
        },
        upsert=True,
    )

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            current_time = time.time()

            # snapshot interval
            if message_type == "snapshot_interval":
                minutes = message.get(
                    "minutes",
                    DEFAULT_SNAPSHOT_INTERVAL
                )

                if minutes in ALLOWED_SNAPSHOT_INTERVALS:
                    snapshot_interval = minutes

                    # restart the snapshot timer when the user changes the interval
                    last_snapshot_interval = current_time

                    print(
                        f"[SNAPSHOT] Interval changed to "
                        f"{snapshot_interval} minutes"
                    )

                    continue

            if message_type != "transcript":
                continue

            text = message.get("text", "").strip()
            timestamp = message.get("timestamp")
            speaker = message.get(
                "speaker",
                "Unknown"
            )

            if not text:
                continue

            # create transcript segment
            segment = {
                "timestamp": timestamp,
                "speaker": speaker,
                "text": text,
            }

            # save raw transcript immediately
            calls.update_one(
                {"meeting_id": meeting_id},
                {
                    "$push": {
                        "segments": segment
                    }
                }
            )

            # add segment to gemini buffer
            transcript_buffer.append(segment)

            print("\n[TRANSCRIPT]")
            print(f"{speaker}: {text}")

            # Live Summary
            if (
                current_time - last_summary_time >= BUFFER_SECONDS
                and transcript_buffer
            ):
                buffered_text = "\n".join(
                    f"{item['speaker']}: {item['text']}"
                    for item in transcript_buffer
                )

                print(
                    "\n[LIVE SUMMARY] "
                    "Sending buffered conversation to Gemini..."
                )

                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=f"""
                    You are a real-time meeting summarization assistant.

                    Update the running summary of the conversation.

                    Previous summary:
                    {current_summary}

                    New conversation:
                    {buffered_text}

                    Instructions:
                    - Update the previous summary using the new conversation.
                    - Preserve important context.
                    - Focus on what is actually being discussed.
                    - Capture the overall flow and substance.
                    - Do not invent or assume information.
                    - Remove conversational filler.
                    - Keep it concise for a small live widget.
                    - Return ONLY the updated natural-language summary.
                    """
                )

                new_summary = response.text.strip()

                if new_summary:
                    current_summary = new_summary
                    print("\n[GEMINI CURRENT SUMMARY]")
                    print(current_summary)

                    # persist current summary
                    calls.update_one(
                        {"meeting_id": meeting_id},
                        {
                            "$set": {
                                "current_summary": current_summary
                            }
                        }
                    )

                    # send current summmary to widget
                    await websocket.send_json({
                        "type": "summary",
                        "summary": current_summary,
                        "timestamp": int(
                            current_time * 1000
                        ),
                    })

                    transcript_buffer.clear()

                    last_summary_time = current_time

            # SNAPSHOT
            if (
                current_time - last_snapshot_interval
                >= snapshot_interval * 60
            ):
                if current_summary:
                    # Meeting relative tiimestamp in milliseconds
                    meeting_timestamp = int(
                        (current_time - meeting_start_time) * 1000
                    )

                    snapshot = {
                        "timestamp": meeting_timestamp,
                        "summary": current_summary,
                    }

                    # Persist snapshot
                    calls.update_one(
                        {"meeting_id": meeting_id},
                        {
                            "$push": {
                                "summary_snapshots": snapshot
                            }
                        }
                    )

                    await websocket.send_json({
                        "type": "snapshot",
                        "timestamp": meeting_timestamp,
                        "summary": current_summary,
                    })

                    print(
                        f"[SNAPSHOT] Saved {snapshot_interval}-minute "
                        f"snapshot at {meeting_timestamp}ms"
                    )

                last_snapshot_interval = current_time

    except WebSocketDisconnect as e:
        print(f"[WS] Client disconnected. Code: {e.code}")

    except Exception as e:
        print("[WS] Unexpected error:", repr(e))