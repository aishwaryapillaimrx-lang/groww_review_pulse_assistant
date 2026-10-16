"""Plain-text rendering of what is written to the Google Doc and the email (PRD R4.3, R4.4).

The pulse is authored as markdown (saved as outputs/weekly_pulse_<date>.md). Docs and Gmail receive
plain text, so markdown symbols are converted to readable plain text here. Rich HTML is a later item.
"""
import re

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BOLD = re.compile(r"\*\*(.+?)\*\*")


def to_plain_text(markdown):
    lines = []
    for line in markdown.splitlines():
        heading = HEADING.match(line)
        if heading:
            text = BOLD.sub(r"\1", heading.group(2))
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(text.upper() if len(heading.group(1)) == 1 else text + ":")
            continue
        if line.startswith("> "):
            line = "- " + line[2:]  # quotes become bullets; the quoted text itself is untouched
        lines.append(BOLD.sub(r"\1", line).replace("`", ""))
    return "\n".join(lines)


def render_doc_entry(run_id, payload):
    """Plain-text block appended to the end of the Google Doc (implementation.md §7)."""
    fee = payload["identified_fee_issue"]
    themes = "; ".join(f"{t['name']} ({t['count']}, {t['share'] * 100:.1f}%)" for t in payload["top_themes"])
    bullets = "\n".join(f"• {b}" for b in payload["explanation_bullets"])
    links = "\n".join(f"- {u}" for u in payload["source_links"])
    return (
        f"\n\n==== Weekly Pulse — {payload['date']} (run {run_id}) ====\n"
        f"date: {payload['date']}\n"
        f"top_themes: {themes}\n"
        f"identified_fee_issue: {fee['label']} ({fee['fee_key']}), {fee['mentions']} mentions\n"
        f"weekly_pulse:\n{to_plain_text(payload['weekly_pulse'])}\n"
        f"explanation_bullets:\n{bullets}\n"
        f"source_links:\n{links}\n"
    )
