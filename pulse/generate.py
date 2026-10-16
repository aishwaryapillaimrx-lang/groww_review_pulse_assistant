"""Weekly pulse, fee explainer, payload and email (implementation.md §5.6-5.8).

The model writes only short prose. Counts, quotes, links and dates are rendered by code,
and validators decide whether the model's output is accepted.
"""
import json
import uuid
from pathlib import Path

from pulse.approval import write_run_files
from pulse.validate import (
    ValidationError,
    check_explainer,
    check_links,
    check_numbers_grounded,
    check_pulse,
)

MAX_ATTEMPTS = 3
SUBJECT = "Weekly Product Pulse + Customer Clarification — {date}"

PULSE_SYSTEM = """You write short prose for an internal weekly product pulse about the Groww investing app.
You receive computed analysis as JSON. Return:
- themes_summary: exactly 3 strings, one per theme in the order given, each 10 words or fewer, with no numbers.
- observation: one sentence of 30 words or fewer on what is going wrong or trending. Use only numbers present in the input.
- actions: exactly 3 concrete action ideas for the product team, each 10 words or fewer, with no numbers.
Do not invent facts. Do not quote reviews. If 'feedback' is set, your previous answer was rejected: fix that problem and be shorter."""

PULSE_SCHEMA = {
    "type": "object",
    "properties": {
        "themes_summary": {"type": "array", "items": {"type": "string"}},
        "observation": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["themes_summary", "observation", "actions"],
    "additionalProperties": False,
}

EXPLAIN_SYSTEM = """You write a customer-facing fee explainer for support teams at Groww.
Use ONLY the facts provided. Return at most 6 short bullets.
- Neutral, facts-only tone: no advice, no blame, no marketing, and never the words should, recommend or best.
- Keep every number, amount and percentage exactly as written in the facts. Add no number that is not in the facts.
- Choose the facts that best address what reviewers seem confused about (example reviews are given for context only; do not quote them).
If 'feedback' is set, your previous answer was rejected: fix that problem."""

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "items": {"type": "string"}}},
    "required": ["bullets"],
    "additionalProperties": False,
}


def pulse_facts_text(top, n_reviews, weeks, fee_issue):
    """Every number the model is allowed to use, as text, so numbers can be checked against it."""
    parts = [str(n_reviews), str(weeks), "1 2 3", str(fee_issue["mentions"])]
    for t in top:
        pct = t["share"] * 100
        parts += [str(t["count"]), f"{pct:.1f}", str(round(pct)), f"{t['avg_rating']:.2f}", f"{t['avg_rating']:.1f}"]
    return " ".join(parts)


def render_pulse(date, window, n_reviews, top, summaries, quotes, names, observation, actions, fee_issue):
    lines = [f"# Weekly Product Pulse {date}", f"{n_reviews} reviews, {window[0]} to {window[1]}.", "", "## Top themes"]
    for i, (t, summary) in enumerate(zip(top, summaries), 1):
        lines.append(f"{i}. **{t['name']}**: {t['count']} reviews ({t['share'] * 100:.1f}%), avg {t['avg_rating']:.2f}/5. {summary}")
    lines += ["", "## What users said"]
    for q in quotes:
        lines.append(f'> "{q["quote"]}" ({names[q["theme_id"]]}, {q["rating"]}/5)')
    lines += ["", "## Key observation", observation, ""]
    signal = f"Fee signal: {fee_issue['label']}, {fee_issue['mentions']} mentions."
    if fee_issue["weak_signal"]:
        signal += " Weak signal: fewer than 3 reviews mention a fee."
    lines += [signal, "", "## Actions"]
    lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
    return "\n".join(lines)


def write_pulse(date, window, n_reviews, weeks, top, quotes, names, fee_issue, llm):
    facts = pulse_facts_text(top, n_reviews, weeks, fee_issue)
    inputs = {
        "total_reviews": n_reviews,
        "weeks": weeks,
        "themes": [
            {"name": t["name"], "description": t["description"], "count": t["count"],
             "share_percent": round(t["share"] * 100, 1), "avg_rating": t["avg_rating"]}
            for t in top
        ],
        "fee_issue": {"label": fee_issue["label"], "mentions": fee_issue["mentions"], "weak_signal": fee_issue["weak_signal"]},
    }
    feedback = None
    for _ in range(MAX_ATTEMPTS):
        out = llm.call_json(PULSE_SYSTEM, json.dumps({**inputs, "feedback": feedback}), PULSE_SCHEMA, effort="medium")
        try:
            if len(out["themes_summary"]) != 3 or len(out["actions"]) != 3:
                raise ValidationError("Need exactly 3 theme summaries and 3 actions")
            prose = " ".join([*out["themes_summary"], out["observation"], *out["actions"]])
            check_numbers_grounded(prose, facts)
            md = render_pulse(date, window, n_reviews, top, out["themes_summary"], quotes, names,
                              out["observation"], out["actions"], fee_issue)
            check_pulse(md)
            return md
        except ValidationError as err:
            feedback = str(err)
    raise ValidationError(f"Pulse failed validation after {MAX_ATTEMPTS} attempts: {feedback}")


def write_explainer(fee_issue, registry, llm):
    entry = registry["fees"][fee_issue["fee_key"]]
    facts = entry["facts"]
    inputs = {
        "fee": entry["label"],
        "facts": facts,
        "example_reviews": [e["text"] for e in fee_issue["examples"]],
    }
    feedback = None
    for _ in range(MAX_ATTEMPTS):
        out = llm.call_json(EXPLAIN_SYSTEM, json.dumps({**inputs, "feedback": feedback}), EXPLAIN_SCHEMA, effort="medium")
        try:
            check_explainer(out["bullets"], facts)
            links = [s["url"] for s in entry["sources"]]
            check_links(links, entry)
            return {
                "label": entry["label"],
                "bullets": out["bullets"],
                "links": links,
                "sources": entry["sources"],
                "last_checked": registry["last_checked"],
            }
        except ValidationError as err:
            feedback = str(err)
    raise ValidationError(f"Explainer failed validation after {MAX_ATTEMPTS} attempts: {feedback}")


def render_support_snippet(explainer):
    lines = [f"--- Support snippet: {explainer['label']} ---"]
    lines += [f"- {b}" for b in explainer["bullets"]]
    lines += ["Sources:"] + [f"- {s['title']}: {s['url']}" for s in explainer["sources"]]
    lines.append(f"Last checked: {explainer['last_checked']}")
    return "\n".join(lines)


def build_payload(date, top, pulse_md, fee_issue, explainer):
    """Matches the PRD doc payload exactly."""
    return {
        "date": date,
        "top_themes": [{"name": t["name"], "count": t["count"], "share": t["share"]} for t in top],
        "weekly_pulse": pulse_md,
        "identified_fee_issue": {"fee_key": fee_issue["fee_key"], "label": fee_issue["label"], "mentions": fee_issue["mentions"]},
        "explanation_bullets": explainer["bullets"],
        "source_links": explainer["links"],
    }


def generate_run(labelled, top, quotes, names, fee_issue, registry, llm, date, weeks, out_dir="outputs"):
    """Stages 6-8: pulse, explainer, payload, email, pending approval. Returns paths and the artefacts."""
    window = (labelled["date"].min().date().isoformat(), labelled["date"].max().date().isoformat())
    n_reviews = len(labelled)

    pulse_md = write_pulse(date, window, n_reviews, weeks, top, quotes, names, fee_issue, llm)
    explainer = write_explainer(fee_issue, registry, llm)
    payload = build_payload(date, top, pulse_md, fee_issue, explainer)
    subject = SUBJECT.format(date=date)
    body = f"{pulse_md}\n\n{render_support_snippet(explainer)}\n"

    run_id = uuid.uuid4().hex[:8]
    out = Path(out_dir)
    out.mkdir(exist_ok=True)
    paths = {"pulse": out / f"weekly_pulse_{date}.md", "email": out / f"email_draft_{date}.txt"}
    paths["pulse"].write_text(pulse_md + "\n", encoding="utf-8")
    paths["email"].write_text(f"Subject: {subject}\n\n{body}", encoding="utf-8")
    paths.update(write_run_files(run_id, payload, subject, body, out))
    return {"run_id": run_id, "paths": paths, "pulse": pulse_md, "explainer": explainer,
            "payload": payload, "subject": subject, "body": body}
