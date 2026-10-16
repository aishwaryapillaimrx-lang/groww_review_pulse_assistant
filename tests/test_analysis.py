"""Offline tests for theme assignment and fee detection with a stub LLM. All data is SYNTHETIC."""
import json
from pathlib import Path

import pandas as pd

from pulse.fees import detect_fee_issue
from pulse.llm import StubLLM
from pulse.themes import assign_reviews, rank_themes, top_themes

REGISTRY = json.loads((Path(__file__).parent.parent / "config" / "fee_registry.json").read_text(encoding="utf-8"))
THEMES = [
    {"id": "T1", "name": "Withdrawal delays", "description": "d"},
    {"id": "T2", "name": "Other / praise", "description": "d"},
]


def make_df(texts):
    return pd.DataFrame(
        [{"review_id": f"r{i}", "date": pd.Timestamp("2026-10-01") + pd.Timedelta(days=i), "rating": 1 + i % 5,
          "text": t, "thumbs_up": i} for i, t in enumerate(texts)]
    )


def labeller(drop_ids=(), fee="none"):
    """Stub that labels every review T1, optionally omitting some ids (to test retry/fallback)."""
    def handler(system, user, schema):
        reviews = json.loads(user)["reviews"]
        return {"assignments": [{"id": r["id"], "theme_id": "T1", "fee_key": fee}
                                for r in reviews if r["id"] not in drop_ids]}
    return handler


def test_assign_covers_every_review_once():
    df = make_df([f"Review text number {i} is long enough" for i in range(10)])
    out = assign_reviews(df, THEMES, StubLLM(labeller()), batch_size=4)
    assert len(out) == 10 and out["theme_id"].notna().all()
    assert set(out["theme_id"]) == {"T1"}


def test_missing_ids_go_to_other_after_one_retry():
    df = make_df([f"Review text number {i} is long enough" for i in range(4)])
    llm = StubLLM(labeller(drop_ids={"1"}))
    out = assign_reviews(df, THEMES, llm, batch_size=10)
    assert out.loc[out["review_id"] == "r1", "theme_id"].item() == "T2"  # fell back to Other
    assert out.attrs["unlabelled_to_other"] == 1
    assert llm.calls == 2  # original call + one retry


def test_rank_themes_by_count():
    df = make_df(["a" * 25] * 5)
    df["theme_id"] = ["T1", "T1", "T1", "T2", "T2"]
    ranked = rank_themes(df, THEMES)
    assert [t["id"] for t in ranked] == ["T1", "T2"]
    assert ranked[0]["count"] == 3 and ranked[0]["share"] == 0.6


def fee_df(rows):
    df = make_df([text for text, _ in rows])
    df["fee_key"] = [fee for _, fee in rows]
    return df


def test_fee_requires_code_and_model_agreement():
    df = fee_df([
        ("DP charges were deducted when I sold my shares", "dp_charges"),
        ("Hidden dp charge on selling, very unfair", "dp_charges"),
        ("Exit load deducted on redemption of my fund", "exit_load"),
        ("Lovely app with a clean design overall", "dp_charges"),  # model says fee, keywords disagree
    ])
    issue = detect_fee_issue(df, REGISTRY)
    assert issue["fee_key"] == "dp_charges"
    assert issue["mentions"] == 2  # the 4th review doesn't count: keywords disagree
    assert issue["method"] == "code and model agree"
    assert not issue["weak_signal"]


def test_fee_weak_signal_and_whole_word_match():
    df = fee_df([
        ("Took a shortcut to place my order quickly", "none"),  # "cut" must not match "shortcut"
        ("Nothing to say, just a decent investing app", "none"),
    ])
    issue = detect_fee_issue(df, REGISTRY)
    assert issue["weak_signal"] and issue["mentions"] == 0


def test_top_themes_excludes_other():
    ranked = [
        {"id": "T5", "name": "Other / praise", "count": 800},
        {"id": "T1", "name": "App bugs", "count": 300},
        {"id": "T2", "name": "Support", "count": 100},
        {"id": "T3", "name": "Charges", "count": 90},
        {"id": "T4", "name": "Withdrawals", "count": 60},
    ]
    assert [t["id"] for t in top_themes(ranked)] == ["T1", "T2", "T3"]


def test_fee_keyword_alone_counts_without_generic_word():
    df = fee_df([("Brokerage is too high for small trades", "brokerage")] * 3)
    issue = detect_fee_issue(df, REGISTRY)
    assert issue["fee_key"] == "brokerage" and issue["mentions"] == 3


def test_other_fee_needs_generic_word_and_is_reported():
    df = fee_df([
        ("They keep deducting extra money from my account", "other"),
        ("Brokerage is too high for small trades", "brokerage"),
    ])
    issue = detect_fee_issue(df, REGISTRY)
    assert issue["other_fee_mentions"] == 1
    assert len(issue["other_examples"]) == 1
