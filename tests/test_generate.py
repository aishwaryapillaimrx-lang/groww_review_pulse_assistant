"""Offline tests for pulse/explainer generation with a stub LLM. All data is SYNTHETIC."""
import json
from pathlib import Path

import pandas as pd
import pytest

from pulse.approval import payload_hash
from pulse.generate import generate_run, write_explainer, write_pulse
from pulse.llm import StubLLM
from pulse.validate import ValidationError, word_count

REGISTRY = json.loads((Path(__file__).parent.parent / "config" / "fee_registry.json").read_text(encoding="utf-8"))
TOP = [
    {"id": "T1", "name": "App bugs", "description": "d", "count": 554, "share": 0.1542, "avg_rating": 1.95},
    {"id": "T2", "name": "High charges", "description": "d", "count": 294, "share": 0.0818, "avg_rating": 2.34},
    {"id": "T3", "name": "Support", "description": "d", "count": 268, "share": 0.0746, "avg_rating": 1.37},
]
NAMES = {t["id"]: t["name"] for t in TOP}
QUOTES = [{"theme_id": t["id"], "review_id": f"r{i}", "quote": f"Synthetic quote number {i} about the app", "rating": 1}
          for i, t in enumerate(TOP)]
FEE = {"fee_key": "brokerage", "label": "Equity brokerage and minimum brokerage", "mentions": 96, "weak_signal": False,
       "examples": [{"review_id": "r1", "rating": 1, "text": "Brokerage is too high for small trades"}]}
WINDOW = ("2026-07-24", "2026-10-15")


def good_pulse(system, user, schema):
    return {"themes_summary": ["Charts lag during volatility", "Brokerage feels high", "Support replies slowly"],
            "observation": "Bugs lead the complaints while charges and support follow closely behind.",
            "actions": ["Fix chart lag", "Explain brokerage clearly", "Speed up support replies"]}


def run_pulse(llm):
    return write_pulse("2026-10-16", WINDOW, 3592, 12, TOP, QUOTES, NAMES, FEE, llm)


def test_pulse_valid_and_within_limit():
    md = run_pulse(StubLLM(good_pulse))
    assert word_count(md) <= 250
    assert "554 reviews (15.4%)" in md and "Synthetic quote number 0" in md  # rendered by code


def test_pulse_retries_with_feedback_when_too_long():
    seen = []

    def handler(system, user, schema):
        seen.append(json.loads(user)["feedback"])
        out = good_pulse(system, user, schema)
        if len(seen) == 1:
            out["observation"] = " ".join(["word"] * 300)
        return out

    llm = StubLLM(handler)
    md = run_pulse(llm)
    assert llm.calls == 2 and seen[0] is None and "words" in seen[1]
    assert word_count(md) <= 250


def test_pulse_rejects_ungrounded_number_after_three_attempts():
    def handler(system, user, schema):
        out = good_pulse(system, user, schema)
        out["observation"] = "About 999 reviews mention crashes."
        return out

    llm = StubLLM(handler)
    with pytest.raises(ValidationError):
        run_pulse(llm)
    assert llm.calls == 3


def test_explainer_retries_on_unknown_number_and_uses_registry_links():
    answers = iter([
        {"bullets": ["Brokerage is ₹25 per order."]},  # ₹25 is not in the registry
        {"bullets": ["Equity brokerage is ₹20 or 0.1% per executed order, whichever is lower.", "18% GST applies on brokerage."]},
    ])
    llm = StubLLM(lambda s, u, sc: next(answers))
    out = write_explainer(FEE, REGISTRY, llm)
    assert llm.calls == 2
    assert out["links"] == [s["url"] for s in REGISTRY["fees"]["brokerage"]["sources"]]
    assert out["last_checked"] == REGISTRY["last_checked"]


def stub_both(system, user, schema):
    if "themes_summary" in schema["properties"]:
        return good_pulse(system, user, schema)
    return {"bullets": ["Equity brokerage is ₹20 or 0.1% per executed order, whichever is lower.", "18% GST applies on brokerage."]}


def test_generate_run_payload_files_and_pending_approval(tmp_path):
    labelled = pd.DataFrame({"date": pd.to_datetime(["2026-07-24", "2026-10-15"])})
    run = generate_run(labelled, TOP, QUOTES, NAMES, FEE, REGISTRY, StubLLM(stub_both), "2026-10-16", 12, out_dir=tmp_path)

    assert set(run["payload"]) == {"date", "top_themes", "weekly_pulse", "identified_fee_issue", "explanation_bullets", "source_links"}
    assert len(run["payload"]["source_links"]) == 2
    assert run["subject"] == "Weekly Product Pulse + Customer Clarification — 2026-10-16"
    assert "--- Support snippet:" in run["body"] and "Last checked: 2026-10-16" in run["body"]
    assert "#" not in run["body"] and "**" not in run["body"]  # email body is plain text

    approval = json.loads(run["paths"]["approval"].read_text(encoding="utf-8"))
    assert approval["status"] == "pending"
    assert approval["payload_sha256"] == payload_hash(run["payload"], run["subject"], run["body"])
    for key in ("pulse", "email", "payload", "approval"):
        assert run["paths"][key].exists()
