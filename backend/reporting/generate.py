import io
from datetime import datetime, timezone

from fpdf import FPDF


def build_filename(meeting_id: str, ext: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_id = meeting_id[:8]
    return f"sales-call-{today}-{short_id}.{ext}"


# ── Section ordering and display labels ──────────────

SECTIONS = [
    ("executive_summary", "Executive Summary"),
    ("customer_requirements", "Customer Requirements"),
    ("pain_points", "Pain Points"),
    ("objections", "Objections"),
    ("decisions", "Decisions"),
    ("action_items", "Action Items"),
    ("commitments", "Commitments"),
    ("next_steps", "Next Steps"),
    ("important_entities", "Important Entities"),
    ("sales_signals", "Sales Signals"),
]


def _render_text(analysis: dict) -> str:
    lines = ["SALES CALL REPORT", "=" * 40, ""]

    for key, label in SECTIONS:
        value = analysis.get(key)
        lines.append(label.upper())
        lines.append("-" * len(label))

        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
        elif isinstance(value, list) and value:
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append("  (none)")

        lines.append("")

    return "\n".join(lines)


class _PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, "Sales Call Report", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(120, 120, 120)
        self.cell(
            0,
            6,
            datetime.now(timezone.utc).strftime("%B %d, %Y"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _render_pdf(analysis: dict) -> bytes:
    pdf = _PDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    content_width = pdf.w - pdf.l_margin - pdf.r_margin

    for key, label in SECTIONS:
        value = analysis.get(key)

        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, label, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        pdf.set_font("Helvetica", "", 10)

        if isinstance(value, str) and value.strip():
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(content_width, 5, value.strip())
        elif isinstance(value, list) and value:
            for item in value:
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(content_width, 5, f"  - {item}")
        else:
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 5, "(none)", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)

        pdf.ln(4)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()

def generate_text(analysis: dict) -> str:
    return _render_text(analysis)


def generate_pdf(analysis: dict) -> bytes:
    return _render_pdf(analysis)
