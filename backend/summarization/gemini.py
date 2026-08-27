import json
import os

from google import genai
from google.genai import types

FINAL_SUMMARY_MODEL = os.getenv("GEMINI_FINAL_MODEL", "gemini-2.5-flash")

_client = None


def _get_client():
    global _client

    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to backend/.env"
            )

        _client = genai.Client(api_key=api_key)

    return _client


SUMMARY_SCHEMA_KEYS = [
    "executive_summary",
    "customer_requirements",
    "pain_points",
    "objections",
    "decisions",
    "action_items",
    "commitments",
    "next_steps",
    "important_entities",
    "sales_signals",
]


def format_transcript(final_transcript) -> str:
    lines = []

    for entry in final_transcript:
        total_seconds = max(0, int(entry["timestamp"] / 1000))
        minutes = total_seconds // 60
        seconds = total_seconds % 60

        lines.append(
            f"[{minutes:02d}:{seconds:02d}] "
            f"{entry['speaker']}: {entry['text']}"
        )

    return "\n".join(lines)

def build_window_summary_prompt(segments) -> str:
    lines = [
        f"{segment.get('speaker', 'unknown')}: {segment.get('text', '')}"
        for segment in segments
    ]
    
    transcript_text = "\n".join(lines)
    
    return f""" 
    You are summarizing ONE short excert of a live conversation 
    
    Excerpt:
    {transcript_text}
    
    Instructions:
    - Summarize ONLY what is said in this excerpt. 
    - Do not reference any conversation outside of it.
    - Be precise and detailed - this is a self-contaiined record of 
    this specific time window, not a rolling summary. 
    - Do not invent or assume information not present in the excerpt. 
    - Return ONLY the summary text, no preamble. 
    """
    
def generate_window_summary(segments) -> str: 
    """
    Independent, self-contained summary of a single snapshot 
    window's raw segments - no rolling context is carried in, so fidelity does not degrade 
    as the call gets longer. 
    """
    
    if not segments:
        return ""
    
    response = _get_client().models.generate_content(
        model=os.getenv("GEMINI_WINDOW_MODEL", "gemini-2.5-flash-lite"),
        contents = build_window_summary_prompt(segments),
    )
    
    return response.text.strip() if response.text else ""
    



def format_window_snapshots(window_snapshots) -> str:
    if not window_snapshots:
        return "(no windowed snapshots available)"

    lines = []
    for snap in window_snapshots:
        if not snap:
            continue

        start_s = max(0, int(snap.get("window_start", 0) / 1000))
        end_s = max(0, int(snap.get("window_end", 0) / 1000))
        lines.append(
            f"[{start_s // 60:02d}:{start_s % 60:02d}"
            f"-{end_s // 60:02d}:{end_s % 60:02d}] "
            f"{snap.get('summary', '')}"
        )

    return "\n".join(lines) if lines else "(no windowed snapshots available)"


def build_final_summary_prompt(final_transcript, window_snapshots=None) -> str:
    transcript_text = format_transcript(final_transcript)
    snapshots_text = format_window_snapshots(window_snapshots or [])

    return f"""
You are an expert sales-call analyst.

Below is the FINAL timestamped transcript of a completed sales call.
It is the authoritative record of the meeting — always ground your
answer in this transcript.

Transcript:
{transcript_text}

Below are independent per-window summaries generated during the
call. Use these ONLY as secondary context to understand the call's
topic structure and pacing — the transcript above is the source of
truth for facts.

Windowed summaries:
{snapshots_text}

Analyze the call and return ONLY a JSON object with exactly these keys:
"""

def generate_final_summary(final_transcript, window_snapshots=None) -> dict:
    response = _get_client().models.generate_content(
        model=FINAL_SUMMARY_MODEL,
        contents=build_final_summary_prompt(final_transcript, window_snapshots),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response for the final "
            "summary (possibly blocked or truncated)."
        )

    analysis = json.loads(response.text)

    if not isinstance(analysis, dict):
        raise RuntimeError(
            f"Gemini final summary response was not a JSON "
            f"object: {type(analysis)}"
        )

    return {key: analysis.get(key) for key in SUMMARY_SCHEMA_KEYS}