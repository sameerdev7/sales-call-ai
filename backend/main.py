import os
import asyncio
import time
import uuid
import traceback

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from database import calls
from audio_processing import process_audio
from reconciliation.speaker_reconciliation import reconcile_speakers
from summarization.gemini import (
    generate_final_summary,
    generate_window_summary,
    get_client,
)
from reporting.generate import generate_pdf, generate_text, build_filename

load_dotenv()

app = FastAPI()

# live WebSocket connections keyed by meeting_id
active_connections = {}

# Config
BUFFER_SECONDS = 10
ALLOWED_SNAPSHOT_INTERVALS = [1, 5, 10]
DEFAULT_SNAPSHOT_INTERVAL = 5

def get_segments_in_window(meeting_id: str, starts_ms: int, end_ms: int) -> list:
    doc = calls.find_one(
        {"meeting_id": meeting_id}, 
        {"segments": 1}, 
    )
    
    if not doc:
        return []
    
    return [
        segment for segment in doc.get("segments", [])
        if segment.get("timestamp") is not None and starts_ms <= segment["timestamp"] < end_ms
    ]


# Root
@app.get("/")
def root():
    return {"status": "Sales Call AI Backend Running."}


# ==================================================
# Debug endpoints - replay summarization against
# existing/arbitrary data via curl, without needing
# a live call or audio upload. Local testing only;
# gated off unless ENABLE_DEBUG_ENDPOINTS=1.
# ==================================================
if os.getenv("ENABLE_DEBUG_ENDPOINTS") == "1":

    @app.get("/debug/transcript/{meeting_id}")
    def debug_get_transcript(meeting_id: str):
        """
        Pull whatever's already stored for a meeting - the reconciled
        final_transcript, windowed snapshots, and raw live-caption
        segments - so you can curl it straight into
        /debug/final-summary or /debug/window-summary below.
        """
        doc = calls.find_one(
            {"meeting_id": meeting_id},
            {
                "final_transcript": 1,
                "summary_snapshots": 1,
                "segments": 1,
                "final_analysis": 1,
            },
        )

        if not doc:
            raise HTTPException(status_code=404, detail="meeting_id not found")

        return {
            "final_transcript": doc.get("final_transcript", []),
            "window_snapshots": doc.get("summary_snapshots", []),
            "segments": doc.get("segments", []),
            "final_analysis": doc.get("final_analysis"),
        }

    @app.post("/debug/window-summary")
    async def debug_window_summary(payload: dict):
        """
        Body: {"segments": [{"speaker": str, "text": str, "timestamp": int}, ...]}
        Runs the exact same generate_window_summary() used live.
        """
        segments = payload.get("segments", [])

        if not segments:
            raise HTTPException(status_code=400, detail="segments is required and must be non-empty")

        summary = generate_window_summary(segments)

        return {"summary": summary, "segment_count": len(segments)}

    @app.post("/debug/final-summary")
    async def debug_final_summary(payload: dict):
        """
        Body: {
            "final_transcript": [{"speaker": str, "text": str, "timestamp": int}, ...],
            "window_snapshots": [...]   (optional)
        }
        Runs the exact same generate_final_summary() the real
        /audio/upload endpoint calls at the end of a real call.
        """
        final_transcript = payload.get("final_transcript", [])
        window_snapshots = payload.get("window_snapshots", [])

        if not final_transcript:
            raise HTTPException(status_code=400, detail="final_transcript is required and must be non-empty")

        analysis = generate_final_summary(final_transcript, window_snapshots)

        return analysis


@app.get("/report/{meeting_id}/pdf")
def download_pdf(meeting_id: str):
    doc = calls.find_one(
        {"meeting_id": meeting_id},
        {"final_analysis": 1},
    )

    if not doc or not doc.get("final_analysis"):
        raise HTTPException(status_code=404, detail="Report not found.")

    pdf_bytes = generate_pdf(doc["final_analysis"])
    filename = build_filename(meeting_id, "pdf")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@app.get("/report/{meeting_id}/text")
def download_text(meeting_id: str):
    doc = calls.find_one(
        {"meeting_id": meeting_id},
        {"final_analysis": 1},
    )

    if not doc or not doc.get("final_analysis"):
        raise HTTPException(status_code=404, detail="Report not found.")

    text_content = generate_text(doc["final_analysis"])
    filename = build_filename(meeting_id, "txt")

    return Response(
        content=text_content.encode("utf-8"),
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


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
        print("[AUDIO] Starting STT...")

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

        # Final summary - full transcript is the primary source 
        # of truth; windowed snapshots are passed as secondary structural 
        # context (architecture.md)

        call_doc = calls.find_one(
            {"meeting_id": meeting_id}, 
            {"summary_snapshots": 1}, 
        )
        
        window_snapshots = (
            call_doc.get("summary_snapshots", [])
            if call_doc
            else []
        )
        
        print(
            f"[FINAL] Generating final summary from "
            f"{len(final_transcript)} transcript entries and "
            f"{len(window_snapshots)} windowed snapshots..."
        )
        
        analysis = generate_final_summary(final_transcript, window_snapshots)

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
            "download_urls": {
                "pdf": f"/report/{meeting_id}/pdf",
                "text": f"/report/{meeting_id}/text",
            },
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
                "ended_at": time.time(),
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

        # If this meeting_id already has a document, this is a
        # RECONNECT (e.g. MV3 service-worker teardown), not a new
        # meeting. Rehydrate state instead of wiping it — otherwise
        # current_summary and the snapshot timer reset every time the
        # extension's service worker restarts mid-call, which is why
        # snapshots were never firing.
        existing = calls.find_one({"meeting_id": incoming_id})

        if existing:
            state["meeting_id"] = incoming_id
            state["started_at"] = existing.get("started_at", time.time())
            state["current_summary"] = existing.get("current_summary", "")
            state["buffer"] = []
            state["last_summary_time"] = time.time()
            state["snapshot_interval"] = existing.get(
                "snapshot_interval", DEFAULT_SNAPSHOT_INTERVAL
            )
            state["last_snapshot_time"] = existing.get(
                "last_snapshot_wall_time", time.time()
            )
            state["meeting_ended"] = bool(existing.get("ended_at"))
            state["doc_created"] = True

            print(f"[WS] Reconnected to existing meeting: {incoming_id}")
        else:
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

            ensure_call_doc()

            print(f"[WS] Adopted new meeting: {incoming_id}")

        active_connections.pop(previous_id, None)
        active_connections[incoming_id] = websocket

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
                }, 
                "$set": {
                    "last_snapshot_wall_time": current_time
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
        
        try:

            response = get_client().models.generate_content(
                model=os.getenv("GEMINI_LIVE_MODEL", "gemini-3.7-flash"),
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
        except Exception as e:
            print("[GEMINI] Live summary generation failed:", repr(e))
            new_summary = ""

        # Always re-arm the throttle, success or failure. Only
        # advancing it on success meant a failed call (e.g. a
        # transient 503 from an overloaded model) left the throttle
        # disarmed, so the very next transcript segment retried
        # immediately instead of waiting BUFFER_SECONDS - turning a
        # brief outage into a retry storm against an already
        # overloaded model.
        state["last_summary_time"] = current_time

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

            # Only clear the buffer on success - a failed call
            # means this content hasn't been summarized yet, so it
            # stays queued and gets included in the next attempt
            # instead of being silently dropped.
            state["buffer"].clear()

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
                    
                    if (minutes in ALLOWED_SNAPSHOT_INTERVALS and minutes != state["snapshot_interval"]):
                        state["snapshot_interval"] = minutes
                        
                        calls.update_one(
                            {"meeting_id": state["meeting_id"]}, 
                            {"$set": {"snapshot_interval": minutes}}
                        )
                        
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
                    tail_start_ms = int(state["last_snapshot_time"] * 1000)
                    tail_end_ms = int(time.time() * 1000)
                    
                    tail_segments = get_segments_in_window(
                        meeting_id, tail_start_ms, tail_end_ms 
                    )
                    
                    tail_summary = ""
                    
                    if tail_segments:
                        try:
                            tail_summary = generate_window_summary(
                                tail_segments
                            )
                            
                        except Exception as e:
                            print(
                                "[SNAPSHOT] Tail window summary failed:", repr(e)
                            )
                            
                        if tail_summary:
                            tail_timestamp = int(
                                (time.time() - state["started_at"]) * 1000
                            )
                            
                            calls.update_one(
                                {"meeting_id": meeting_id}, 
                                {
                                    "$push": {
                                        "summary_snapshots": {
                                            "timestamp": tail_timestamp, 
                                            "window_start": tail_start_ms, 
                                            "window_end": tail_end_ms, 
                                            "summary": tail_summary, 
                                            "segment_count": len(tail_segments),
                                        }
                                    }
                                }
                            )
                            
                            print(
                                f"[SNAPSHOT] Saved tail window "
                                f"({len(tail_segments)} segments)"
                            )
                    
                    state["meeting_ended"] = True 
                    
                    calls.update_one(
                        {"meeting_id": meeting_id}, 
                        {"$set": {"ended_at": time.time()}},
                    )
                    
                    print(
                        f"[MEET] Backend informed of meeting "
                        f"end: {meeting_id}"
                    )

                if message_type == "transcript":
                    await handle_transcript(message)

            meeting_id = state["meeting_id"]

            # WINDOWED SNAPSHOT (evaluated every tick; stopped
            # once the meeting has ended). Each snapshot is now
            # an independent, fresh Gemini summary of ONLY the
            # segments spoken since the previous snapshot — not
            # a copy of the rolling current_summary, which
            # compounds context loss over a long call
            # (architecture.md Step 4).
            if (
                not state["meeting_ended"]
                and current_time - state["last_snapshot_time"]
                >= state["snapshot_interval"] * 60
            ):
                doc_ended = calls.find_one(
                    {"meeting_id": meeting_id}, 
                    {"ended_at": 1},
                )
                
                if doc_ended and doc_ended.get("ended_at"):
                    state["meeting_ended"] = True 
                    print(
                        f"[WS] Detected meeting end via Mongo "
                        f"(meeting_ended message was likely lost) "
                        f"for {meeting_id}"
                    )
                    
                    state["last_snapshot_time"] = current_time
                    continue 
                
                window_start_ms = int(state["last_snapshot_time"] * 1000)
                window_end_ms = int(current_time * 1000)

                window_segments = get_segments_in_window(
                    meeting_id, window_start_ms, window_end_ms
                )

                meeting_timestamp = int(
                    (current_time - state["started_at"]) * 1000
                )

                has_content = bool(window_segments)
                window_summary = ""

                if has_content:
                    try:
                        window_summary = generate_window_summary(
                            window_segments
                        )
                    except Exception as e:
                        print(
                            "[SNAPSHOT] Window summary failed:",
                            repr(e),
                        )
                        has_content = False

                if not window_summary:
                    window_summary = "No conversation in this window."
                    has_content = False

                snapshot = {
                    "timestamp": meeting_timestamp,
                    "window_start": window_start_ms,
                    "window_end": window_end_ms,
                    "summary": window_summary,
                    "segment_count": len(window_segments),
                }

                # Always persist, even empty windows, so the
                # final record has no gaps.
                calls.update_one(
                    {"meeting_id": meeting_id},
                    {
                        "$push": {
                            "summary_snapshots": snapshot
                        },
                        "$set": {
                            "last_snapshot_wall_time": current_time
                        }
                    }
                )

                # Only notify the live widget when there's real
                # content — no point cluttering HISTORY with
                # empty-window placeholders during the call.
                if has_content:
                    await websocket.send_json({
                        "type": "snapshot",
                        "timestamp": meeting_timestamp,
                        "window_start": window_start_ms,
                        "window_end": window_end_ms,
                        "segment_count": len(window_segments),
                        "summary": window_summary,
                    })

                print(
                    f"[SNAPSHOT] Saved {state['snapshot_interval']}-"
                    f"minute window ({len(window_segments)} "
                    f"segments) at {meeting_timestamp}ms"
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