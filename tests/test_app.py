"""Headless smoke tests for app.py using Streamlit's AppTest. Skipped if the cached analysis is absent."""
import json
import uuid
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from pulse import approval

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
OUT = Path("outputs")
pytestmark = pytest.mark.skipif(
    not (OUT / "themes.json").exists() or not Path("data/reviews.csv").exists(),
    reason="needs the cached analysis and the reviews CSV",
)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def text_input(at, label):
    return next(t for t in at.text_input if t.label == label)


def test_app_runs_analysis_from_cache():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    button(at, "Run analysis").click().run()
    assert not at.exception
    assert [s.value for s in at.subheader][:2] == ["Themes", "Quotes (verbatim from the reviews)"]
    assert len(at.dataframe) == 1
    import pandas as pd

    cached = len(pd.read_csv(OUT / "labelled_reviews.csv", keep_default_na=False))
    shown = next(m.value for m in at.metric if m.label == "Reviews analysed")
    assert shown == f"{cached:,}"  # headline count comes from the cached labels, not the ingest cap


def test_reject_in_app_writes_nothing_and_records_rejection():
    run_id = "apptest" + uuid.uuid4().hex[:6]
    payload = {
        "date": "2026-10-16", "top_themes": [{"name": "App bugs", "count": 5, "share": 0.5}],
        "weekly_pulse": "# Pulse\nBody", "identified_fee_issue": {"fee_key": "brokerage", "label": "Brokerage", "mentions": 3},
        "explanation_bullets": ["Brokerage is ₹20."], "source_links": ["https://a.example", "https://b.example"],
    }
    paths = approval.write_run_files(run_id, payload, "Subject", "Body text", OUT)
    try:
        at = AppTest.from_file(APP, default_timeout=120).run()
        button(at, "Run analysis").click().run()
        text_input(at, "Run ID").set_value(run_id).run()
        button(at, "Open").click().run()
        assert not at.exception
        button(at, "Reject").click().run()  # no name yet -> refused
        assert approval.load_record(run_id, OUT)["status"] == "pending"
        text_input(at, "Your name").set_value("Tester").run()
        button(at, "Reject").click().run()
        assert not at.exception
        record = approval.load_record(run_id, OUT)
        assert record["status"] == "rejected" and record["approver"] == "Tester"
        assert "tool_results" not in record  # nothing was published
    finally:
        for p in paths.values():
            Path(p).unlink(missing_ok=True)
