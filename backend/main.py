import os
import asyncio
import time
import uuid
import traceback

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from google import genai

from database import calls
from audio_processing import process_audio
from reconciliation.speaker_reconciliation import reconcile_speakers
from summarization.gemini import generate_final_summary

load_dotenv()

app = FastAPI()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

# live WebSocket connections keyed by meeting_id
active_connections = {}

# Config
BUFFER_SECONDS = 10
ALLOWED_SNAPSHOT_INTERVALS = [1, 5, 10]
DEFAULT_SNAPSHOT_INTERVAL = 5


# Root
@app.get("/")
def root():
    return {"status": "Sales Call AI Backend Running."}


@app.post("/audio/upload")
async def upload_audio(
    file: UploadFile = File(...),
    meeting_id: str = Form(...),
    recording_started_at: int | None = Form(None),
):
    print(f"[AUDIO] Received recording for meeting {meeting_id}")

    audio_bytes = await file.read()

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    calls.update_one(
        {"meeting_id": meeting_id},
        {
            "$set": {
                "audio_status": "processing",
                "recording_started_at": recording_started_at,
            }
        }
    )

    try:
        print("[AUDIO] Starting FFmpeg + STT...")

        stt_segments, stt_provider = process_audio(audio_bytes)

        print(
            f"[AUDIO] STT complete via '{stt_provider}' "
            f"({len(stt_segments)} segments)"
        )

        calls.update_one(
            {"meeting_id": meeting_id},
            {
                "$set": {
                    "audio_status": "transcribed",
                    "stt_segments": stt_segments,
                    "stt_provider": stt_provider,
                },
                "$unset": {"audio_error": ""},
            }
        )

        # Speaker Reconciliation
        meet_doc = calls.find_one(
            {"meeting_id": meeting_id},
            {"segments": 1},
        )
        meet_segments = (
            meet_doc.get("segments", [])
            if meet_doc
            else []
        )

        final_transcript, speaker_mapping = reconcile_speakers(
            meet_segments,
            stt_segments,
            recording_started_at,
        )

        if speaker_mapping.get("mode") == "unlabeled":
            print("[RECONCILIATION] Mode: unlabeled")
            print(
                "[RECONCILIATION] Attribution (seconds):",
                speaker_mapping.get("attribution_seconds", {}),
                "| unknown:",
                speaker_mapping.get("unknown_seconds", 0),
                "s",
            )
        else:
            print(
                "[RECONCILIATION] Mapping:",
                {
                    label: info.get("speaker")
                    for label, info in speaker_mapping.items()
                    if isinstance(info, dict)
                },
            )

        calls.update_one(
            {"meeting_id": meeting_id},
            {
                "$set": {
                    "audio_status": "reconciled",
                    "final_transcript": final_transcript,
                    "speaker_mapping": speaker_mapping,
                }
            }
        )

        if not final_transcript:
            raise RuntimeError(
                "Final transcript is empty; "
                "recording may contain no speech."
            )

        # Final Summary (from final_transcript only)
        print(
            f"[FINAL] Generating final summary from "
            f"{len(final_transcript)} transcript entries..."
        )

        analysis = generate_final_summary(final_transcript)

        calls.update_one(
            {"meeting_id": meeting_id},
            {
                "$set": {
                    "audio_status": "completed",
                    "ended_at": time.time(),
                    "final_summary": analysis.get("executive_summary", ""),
                    "final_analysis": analysis,
                }
            }
        )

        result = {
            "type": "final_processing_complete",
            "meeting_id": meeting_id,
            "status": "completed",
            "stt_provider": stt_provider,
            "transcript_entries": len(final_transcript),
            "final_summary": analysis.get("executive_summary", ""),
        }

        websocket = active_connections.get(meeting_id)

        if websocket is not None:
            try:
                await websocket.send_json(result)
            except Exception as e:
                print("[WS] Failed to push final results:", repr(e))

        return result

    except Exception as e:
        print("[AUDIO] Processing failed:", repr(e))
        traceback.print_exc()
        calls.update_one({"meeting_id": meeting_id}, {
            "$set": {
                "audio_status": "error",
                "audio_error": str(e),
            }
        })

        raise HTTPException(status_code=500, detail=str(e))

# Web Socket
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[WS] Chrome extension connected.")

    """
    Live-summary state is per-meeting and kept in `state`.
    The extension owns meeting identity (Rule 6) and sends
    `meeting_id` with every message. When a message carries
    an unknown meeting_id the connection adopts it, resets
    live state and creates the call document. A reconnect
    therefore resumes the SAME meeting.
    """

    state = {
        "meeting_id": str(uuid.uuid4()),
        "started_at": time.time(),
        "current_summary": "",
        "buffer": [],
        "last_summary_time": time.time(),
        "snapshot_interval": DEFAULT_SNAPSHOT_INTERVAL,
        "last_snapshot_time": time.time(),
        "doc_created": False,
        "meeting_ended": False,
    }

    print(f"[WS] Provisional meeting id: {state['meeting_id']}")

    await websocket.send_json({
        "type": "meeting_started",
        "meeting_id": state["meeting_id"]
    })

    active_connections[state["meeting_id"]] = websocket

    def ensure_call_doc():
        calls.update_one(
            {"meeting_id": state["meeting_id"]},
            {
                "$setOnInsert": {
                    "meeting_id": state["meeting_id"],
                    "started_at": state["started_at"],
                    "segments": [],
                    "summary_snapshots": [],
                    "current_summary": "",
                }
            },
            upsert=True,
        )
        state["doc_created"] = True

    async def adopt_meeting(incoming_id):
        previous_id = state["meeting_id"]

        state["meeting_id"] = incoming_id
        state["started_at"] = time.time()
        state["current_summary"] = ""
        state["buffer"] = []
        state["last_summary_time"] = time.time()
        state["last_snapshot_time"] = time.time()
        state["meeting_ended"] = False

        # drop the unused provisional document, if any
        calls.delete_one({
            "meeting_id": previous_id,
            "segments": {"$size": 0},
        })

        active_connections.pop(previous_id, None)
        active_connections[incoming_id] = websocket

        ensure_call_doc()

        print(f"[WS] Adopted meeting: {incoming_id}")

        await websocket.send_json({
            "type": "meeting_started",
            "meeting_id": incoming_id,
        })

    async def handle_transcript(message):
        text = message.get("text", "").strip()
        timestamp = message.get("timestamp")
        speaker = message.get(
            "speaker",
            "Unknown"
        )

        if not text:
            return

        if not state["doc_created"]:
            ensure_call_doc()

        meeting_id = state["meeting_id"]
        current_time = time.time()

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
        state["buffer"].append(segment)

        print("\n[TRANSCRIPT]")
        print(f"{speaker}: {text}")

        # Live Summary
        if (
            current_time - state["last_summary_time"]
            < BUFFER_SECONDS
            or not state["buffer"]
            or state["meeting_ended"]
        ):
            return

        buffered_text = "\n".join(
            f"{item['speaker']}: {item['text']}"
            for item in state["buffer"]
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
            {state['current_summary']}

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

        new_summary = (
            response.text.strip()
            if response.text
            else ""
        )

        if new_summary:
            state["current_summary"] = new_summary
            print("\n[GEMINI CURRENT SUMMARY]")
            print(state["current_summary"])

            # persist current summary
            calls.update_one(
                {"meeting_id": meeting_id},
                {
                    "$set": {
                        "current_summary":
                            state["current_summary"]
                    }
                }
            )

            # send current summmary to widget
            await websocket.send_json({
                "type": "summary",
                "summary": state["current_summary"],
                "timestamp": int(
                    current_time * 1000
                ),
            })

            state["buffer"].clear()

            state["last_summary_time"] = current_time

    try:
        while True:
            # Time-driven loop: wake at least once per second so
            # snapshots fire on schedule even during silence.
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                message = None

            current_time = time.time()

            if message is not None:
                message_type = message.get("type")

                # Meeting identity adoption (Rule 6)
                incoming_meeting_id = message.get("meeting_id")

                if (
                    incoming_meeting_id
                    and incoming_meeting_id != state["meeting_id"]
                    and isinstance(incoming_meeting_id, str)
                ):
                    await adopt_meeting(incoming_meeting_id)

                meeting_id = state["meeting_id"]

                # snapshot interval
                if message_type == "snapshot_interval":
                    minutes = message.get(
                        "minutes",
                        DEFAULT_SNAPSHOT_INTERVAL
                    )

                    # Ignore duplicate/no-op interval changes;
                    # do NOT reset the running snapshot timer,
                    # the new threshold simply applies to the
                    # next due snapshot.
                    if (
                        minutes in ALLOWED_SNAPSHOT_INTERVALS
                        and minutes != state["snapshot_interval"]
                    ):
                        state["snapshot_interval"] = minutes

                        print(
                            f"[SNAPSHOT] Interval changed to "
                            f"{state['snapshot_interval']} minutes"
                        )

                    continue

                # Meeting end marker from the extension.
                # Stops live summaries and snapshots even
                # though the WebSocket stays open while the
                # recording uploads.
                if message_type == "meeting_ended":
                    state["meeting_ended"] = True

                    calls.update_one(
                        {"meeting_id": meeting_id},
                        {"$set": {"ended_at": time.time()}},
                    )

                    print(
                        f"[MEET] Backend informed of meeting "
                        f"end: {meeting_id}"
                    )
                    continue

                if message_type == "transcript":
                    await handle_transcript(message)

            meeting_id = state["meeting_id"]

            # SNAPSHOT (evaluated every tick; stopped once
            # the meeting has ended so stale summaries are
            # never re-saved)
            if (
                not state["meeting_ended"]
                and current_time - state["last_snapshot_time"]
                >= state["snapshot_interval"] * 60
            ):
                if state["current_summary"]:
                    # Meeting relative timestamp in milliseconds
                    meeting_timestamp = int(
                        (current_time - state["started_at"]) * 1000
                    )

                    snapshot = {
                        "timestamp": meeting_timestamp,
                        "summary": state["current_summary"],
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
                        "summary": state["current_summary"],
                    })

                    print(
                        f"[SNAPSHOT] Saved "
                        f"{state['snapshot_interval']}-minute "
                        f"snapshot at {meeting_timestamp}ms"
                    )

                state["last_snapshot_time"] = current_time

    except WebSocketDisconnect as e:
        print(f"[WS] Client disconnected. Code: {e.code}")

    except Exception as e:
        print("[WS] Unexpected error:", repr(e))

    finally:
        for mid, ws in list(active_connections.items()):
            if ws is websocket:
                del active_connections[mid]