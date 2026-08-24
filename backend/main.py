import os
import time
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from google import genai

from database import calls

load_dotenv()

app = FastAPI()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


@app.get("/")
def root():
    return {"status": "Sales Call AI Backend Running."}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    await websocket.accept()

    print("Chrome extension connected.")

    meeting_id = str(uuid.uuid4())

    print("Meeting ID:", meeting_id)

    previous_summary = ""

    transcript_buffer = []

    BUFFER_SECONDS = 10
    last_summary_time = time.time()

    # User can later change this from the widget
    snapshot_interval = 5
    meeting_start_time = time.time()
    last_snapshot_time = meeting_start_time

    # Create call document
    calls.update_one(
        {"meeting_id": meeting_id},
        {
            "$setOnInsert": {
                "meeting_id": meeting_id,
                "started_at": time.time(),
                "segments": [],
                "summary_snapshots": [],
                "current_summary": ""
            }
        },
        upsert=True
    )

    try:

        while True:

            message = await websocket.receive_json()

            message_type = message.get("type")

            # --------------------------------
            # Snapshot interval from widget
            # --------------------------------

            if message_type == "snapshot_interval":

                minutes = message.get("minutes", 5)

                if minutes in [1, 5, 10]:
                    snapshot_interval = minutes

                    print(
                        f"Snapshot interval changed to "
                        f"{snapshot_interval} minutes"
                    )

                continue

            # --------------------------------
            # Transcript
            # --------------------------------

            if message_type != "transcript":
                continue

            text = message.get("text", "")
            timestamp = message.get("timestamp")
            speaker = message.get("speaker", "Unknown")

            if not text:
                continue

            segment = {
                "timestamp": timestamp,
                "speaker": speaker,
                "text": text
            }

            # --------------------------------
            # Save RAW transcript
            # --------------------------------

            calls.update_one(
                {"meeting_id": meeting_id},
                {
                    "$push": {
                        "segments": segment
                    }
                }
            )

            # --------------------------------
            # Add to Gemini buffer
            # --------------------------------

            transcript_buffer.append(segment)

            print("\nReceived transcript:")
            print(f"{speaker}: {text}")

            current_time = time.time()

            # --------------------------------
            # LIVE SUMMARY
            # --------------------------------

            if current_time - last_summary_time >= BUFFER_SECONDS:

                buffered_text = "\n".join(
                    f"{item['speaker']}: {item['text']}"
                    for item in transcript_buffer
                )

                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=f"""
You are a real-time meeting summarization assistant.

Update the running summary of the conversation.

Previous summary:
{previous_summary}

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

                previous_summary = response.text.strip()

                print("\nGemini:")
                print(previous_summary)

                # Save current summary
                calls.update_one(
                    {"meeting_id": meeting_id},
                    {
                        "$set": {
                            "current_summary": previous_summary
                        }
                    }
                )

                # Send current summary to widget
                await websocket.send_json({
                    "type": "summary",
                    "summary": previous_summary,
                    "timestamp": int(time.time() * 1000)
                })

                transcript_buffer.clear()

                last_summary_time = current_time

            # --------------------------------
            # SUMMARY SNAPSHOT
            # --------------------------------

            if (
                current_time - last_snapshot_time
                >= snapshot_interval * 60
            ):

                if previous_summary:

                    meeting_timestamp = int(
                        current_time - meeting_start_time
                    )

                    snapshot = {
                        "timestamp": meeting_timestamp,
                        "summary": previous_summary
                    }

                    calls.update_one(
                        {"meeting_id": meeting_id},
                        {
                            "$push": {
                                "summary_snapshots": snapshot
                            }
                        }
                    )

                    # Send snapshot to widget
                    await websocket.send_json({
                        "type": "snapshot",
                        "timestamp": meeting_timestamp,
                        "summary": previous_summary
                    })

                    print(
                        f"[SNAPSHOT] "
                        f"{snapshot_interval} minute snapshot saved"
                    )

                last_snapshot_time = current_time

    except Exception as e:

        print(
            "WebSocket disconnected:",
            e
        )