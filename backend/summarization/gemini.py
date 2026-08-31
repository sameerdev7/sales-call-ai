import json
import os

from google import genai
from google.genai import types

FINAL_SUMMARY_MODEL = os.getenv("GEMINI_FINAL_MODEL", "gemini-3.1-pro-preview")

_client = None


def get_client():
    global _client

    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to backend/.env"
            )

        _client = genai.Client(api_key=api_key)

    return _client


# Backwards-compatible alias (internal callers within this module).
_get_client = get_client


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
    if not final_transcript:
        return ""

    # Segment timestamps are absolute epoch ms (Date.now() on the
    # extension side / recording_started_at_ms-anchored STT on the
    # backend side) - NOT meeting-relative. Normalize against the
    # earliest timestamp in this transcript so the [MM:SS] labels
    # actually reflect elapsed meeting time instead of garbage
    # multi-million-minute offsets.
    timestamped = [
        entry for entry in final_transcript
        if entry.get("timestamp") is not None
    ]
    base_ts = min((e["timestamp"] for e in timestamped), default=0)

    lines = []

    for entry in final_transcript:
        ts = entry.get("timestamp")
        elapsed_seconds = max(0, int((ts - base_ts) / 1000)) if ts is not None else 0
        minutes = elapsed_seconds // 60
        seconds = elapsed_seconds % 60

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
        model=os.getenv("GEMINI_WINDOW_MODEL", "gemini-3.1-pro-preview"),
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


def build_final_summary_prompt(caption_transcript, audio_transcript=None, window_snapshots=None) -> str:
    caption_text = format_transcript(caption_transcript)
    audio_text = format_transcript(audio_transcript) if audio_transcript else ""
    snapshots_text = format_window_snapshots(window_snapshots or [])

    if audio_text and caption_text:
        transcript_section = f"""
You have TWO independent transcripts of the same completed sales
call - use them together as two witnesses to the same conversation.

1) AUDIO TRANSCRIPT (speech-to-text from the recorded audio):
{audio_text}

2) LIVE CAPTION TRANSCRIPT (Google Meet's real-time captions, with
the actual speaker names as they appear in the meeting):
{caption_text}

Where the two agree, treat that as confirmed. Where they differ,
prefer whichever phrasing is clearer and more specific - but do NOT
include a specific claim, number, name, price, or commitment that
appears ONLY in the audio transcript if the corresponding moment in
the live captions suggests nothing was said there or says something
substantially different. Speech-to-text on unclear audio can
occasionally fabricate plausible-sounding text, so the live captions
are your cross-check against that."""
    elif audio_text:
        transcript_section = f"""
Below is the transcript of the call (speech-to-text from the
recorded audio). It is the authoritative record - always ground
your answer in it.

Transcript:
{audio_text}"""
    else:
        transcript_section = f"""
Below is the transcript of the call, captured in real time via
Google Meet's live captions. It is the authoritative record -
always ground your answer in it.

Transcript:
{caption_text}"""

    return f"""
You are an expert sales-call analyst producing a DETAILED post-call
report. This report is read by a sales rep and their manager to
decide next steps - vague or generic output is not useful to them.
{transcript_section}

Below are independent per-window summaries generated during the
call. These exist ONLY to help you understand topic structure and
pacing across a long call. Do NOT simply condense or restate these
summaries - that produces a shallow "summary of summaries." Go back
to the transcript(s) above for every specific: names, numbers,
dates, prices, product names, exact commitments, and direct concerns
as the customer phrased them.

Windowed summaries (structural context only):
{snapshots_text}

Analyze the call and return ONLY a JSON object with exactly these
keys (no other keys, no markdown, no preamble):

- "executive_summary": 3-5 sentences covering what the call was
  about, how it went, and the overall outcome/trajectory.
- "customer_requirements": array of strings - specific stated needs,
  must-haves, technical/business requirements the customer mentioned.
- "pain_points": array of strings - specific problems, frustrations,
  or challenges the customer described, in their own terms where
  possible.
- "objections": array of strings - specific pushback, hesitations,
  or concerns the customer raised, including any pricing/timing/
  competitor objections.
- "decisions": array of strings - concrete decisions made or
  confirmed during the call by either side.
- "action_items": array of strings - specific follow-up tasks,
  each naming who owns it if the transcript makes that clear.
- "commitments": array of strings - specific promises or
  commitments made by the rep or the customer (dates, deliverables,
  numbers).
- "next_steps": array of strings - what happens next and when,
  as agreed on the call.
- "important_entities": array of strings - names, company names,
  products, tools, competitors, or dollar figures mentioned that
  matter for follow-up.
- "sales_signals": array of strings - buying signals or red flags
  (budget mentioned, timeline mentioned, decision-maker involvement,
  competitor mentioned, low urgency, etc).

Every array should reflect what's ACTUALLY in the transcript(s). If
a category genuinely has no content in this call, return an empty
array for it rather than inventing filler. Prefer specific,
quotable detail over generic phrasing.
"""


def generate_final_summary(caption_transcript, audio_transcript=None, window_snapshots=None) -> dict:
    response = _get_client().models.generate_content(
        model=FINAL_SUMMARY_MODEL,
        contents=build_final_summary_prompt(
            caption_transcript, audio_transcript, window_snapshots
        ),
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