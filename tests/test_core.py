"""Offline tests for ingest, quotes and validators. All data here is SYNTHETIC."""
import json
from pathlib import Path

import pandas as pd
import pytest

from pulse.ingest import load_reviews
from pulse.quotes import QuoteError, mask_pii, select_quotes
from pulse.validate import ValidationError, check_explainer, check_links, check_pulse

REGISTRY = json.loads((Path(__file__).parent.parent / "config" / "fee_registry.json").read_text(encoding="utf-8"))


def write_csv(tmp_path, rows):
    path = tmp_path / "reviews.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def review(rid, date, text, rating=1, thumbs_up=0):
    return {"review_id": rid, "date": date, "rating": rating, "text": text,
            "thumbs_up": thumbs_up, "app_version": "1.0", "source": "google_play"}


def test_ingest_filters(tmp_path):
    long_text = "The app keeps crashing when I try to sell"
    path = write_csv(tmp_path, [
        review("a", "2026-10-15", long_text),
        review("b", "2026-10-14", "too short"),                              # under 20 chars
        review("c", "2026-10-13", long_text),                                # duplicate text
        review("d", "2026-10-12", "यह ऐप बहुत धीमा है और काम नहीं करता"),        # non-English
        review("e", "2026-05-01", "An old review outside the window entirely"),  # outside window
        review("f", "2026-10-11", "Charges on selling shares are confusing"),
    ])
    df, stats = load_reviews(path, weeks=8, max_reviews=100)
    assert set(df["review_id"]) == {"a", "f"}
    assert stats["too_short"] == 1
    assert stats["duplicate"] == 1
    assert stats["non_english"] == 1
    assert stats["outside_window"] == 1
    assert stats["kept"] == 2


def test_ingest_cap_keeps_newest(tmp_path):
    path = write_csv(tmp_path, [review(str(i), f"2026-10-{i:02d}", f"Review number {i} with enough length") for i in range(1, 11)])
    df, stats = load_reviews(path, weeks=8, max_reviews=3)
    assert list(df["review_id"]) == ["10", "9", "8"]
    assert stats["over_cap"] == 7


def themed_df():
    rows = [
        {**review("r1", "2026-10-10", "Withdrawal has been stuck for five days and support is not replying at all", 1, 5), "theme_id": "T1"},
        {**review("r2", "2026-10-11", "Money withdrawal delayed again, very frustrating experience for me today", 2, 9), "theme_id": "T1"},
        {**review("r3", "2026-10-12", "Great app for investing, the interface is clean and very easy to use daily", 5, 2), "theme_id": "T2"},
    ]
    return pd.DataFrame(rows)


def test_quotes_verbatim():
    df = themed_df()
    quotes = select_quotes(df, ["T1", "T2"])
    assert [q["review_id"] for q in quotes] == ["r2", "r3"]  # most thumbs_up in T1; happy review for praise theme
    for q in quotes:
        assert any(q["quote"] in t for t in df["text"])


def test_quote_missing_candidate_raises():
    df = themed_df()
    with pytest.raises(QuoteError):
        select_quotes(df, ["T9"])


def test_mask_pii():
    masked = mask_pii("Call me on 9876543210 or mail a.b@x.com, order 123456789012")
    assert "9876543210" not in masked and "a.b@x.com" not in masked and "123456789012" not in masked


def test_pulse_word_limit():
    check_pulse(" ".join(["word"] * 250))
    with pytest.raises(ValidationError):
        check_pulse(" ".join(["word"] * 251))


def test_explainer_numbers():
    facts = REGISTRY["fees"]["dp_charges"]["facts"]
    check_explainer(["Groww's charge is ₹16.50 per sell transaction.", "18% GST is added."], facts)
    with pytest.raises(ValidationError):
        check_explainer(["The charge is ₹25 per sell."], facts)


def test_explainer_limits_and_advice():
    facts = REGISTRY["fees"]["dp_charges"]["facts"]
    with pytest.raises(ValidationError):
        check_explainer(["Fact."] * 7, facts)
    with pytest.raises(ValidationError):
        check_explainer(["You should avoid selling small amounts."], facts)


def test_links_from_registry():
    entry = REGISTRY["fees"]["exit_load"]
    urls = [s["url"] for s in entry["sources"]]
    check_links(urls, entry)
    with pytest.raises(ValidationError):
        check_links(["https://example.com", urls[1]], entry)
