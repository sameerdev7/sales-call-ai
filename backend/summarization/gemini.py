import json
import os

from google import genai
from google.genai import types

FINAL_SUMMARY_MODEL = os.getenv("GEMINI_FINAL_MODEL", "gemini-3.5-flash")

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


def build_final_summary_prompt(final_transcript) -> str:
    transcript_text = format_transcript(final_transcript)

    return f"""
You are an expert sales-call analyst.

Below is the FINAL timestamped transcript of a completed sales call.
It is the authoritative record of the meeting.

Transcript:
{transcript_text}

Analyze the call and return ONLY a JSON object with exactly these keys:

- executive_summary: string. A concise paragraph summarizing the call.
- customer_requirements: array of strings. What the customer needs/wants.
- pain_points: array of strings. Problems the customer is facing.
- objections: array of strings. Concerns or pushback raised by the customer.
- decisions: array of strings. Decisions made during the call.
- action_items: array of strings. Tasks anyone committed to doing.
- commitments: array of strings. Promises made by either side.
- next_steps: array of strings. Agreed follow-ups with owners where stated.
- important_entities: array of strings. Names of companies, products, people, tools, budgets, dates worth remembering.
- sales_signals: array of strings. Buying signals, risk signals, or sentiment cues.

Rules:
- Base every statement strictly on the transcript. Do not invent information.
- Use empty arrays when a category has no content in the call.
"""


def generate_final_summary(final_transcript) -> dict:
    response = _get_client().models.generate_content(
        model=FINAL_SUMMARY_MODEL,
        contents=build_final_summary_prompt(final_transcript),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    analysis = json.loads(response.text)

    return {key: analysis.get(key) for key in SUMMARY_SCHEMA_KEYS}
