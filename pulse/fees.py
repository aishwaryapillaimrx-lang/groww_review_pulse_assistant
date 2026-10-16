"""Fee detection: keyword pass (code) + model tag, combined (implementation.md §5.5)."""
import re

from pulse.quotes import mask_pii

WEAK_SIGNAL_BELOW = 3
EXAMPLE_MIN_CHARS = 40
EXAMPLE_MAX_CHARS = 300


def _compile(keywords):
    # Whole-word matching so "cut" doesn't match "shortcut".
    return [re.compile(rf"(?<!\w){re.escape(k)}(?!\w)", re.IGNORECASE) for k in keywords]


def keyword_fee(text, generic, per_fee):
    """Fee key whose own keywords match most; 'other' if only a generic fee word appears; else None."""
    scores = {key: sum(1 for p in pats if p.search(text)) for key, pats in per_fee.items()}
    best = max(scores.values())
    if best > 0:
        return next(key for key, score in scores.items() if score == best)  # ties: registry order
    if any(p.search(text) for p in generic):
        return "other"
    return None


def pick_examples(df, n):
    """Most-liked reviews of readable length (so rants don't crowd out explanations)."""
    length = df["text"].str.len()
    readable = df[(length >= EXAMPLE_MIN_CHARS) & (length <= EXAMPLE_MAX_CHARS)]
    pool = readable if len(readable) >= n else df
    return pool.sort_values(["thumbs_up", "date"], ascending=False).head(n)


def _render(df):
    return [{"review_id": r.review_id, "rating": int(r.rating), "text": mask_pii(r.text)} for r in df.itertuples()]


def detect_fee_issue(df, registry):
    """df needs fee_key (model tag), text, review_id, thumbs_up. Returns the identified fee issue."""
    fees = registry["fees"]
    generic = _compile(registry["generic_fee_keywords"])
    per_fee = {key: _compile(entry["keywords"]) for key, entry in fees.items()}

    df = df.copy()
    df["code_fee"] = df["text"].map(lambda t: keyword_fee(t, generic, per_fee))

    model_counts = {k: int((df["fee_key"] == k).sum()) for k in fees}
    agree_counts = {k: int(((df["fee_key"] == k) & (df["code_fee"] == k)).sum()) for k in fees}
    other_agree = int(((df["fee_key"] == "other") & (df["code_fee"] == "other")).sum())
    fee_tagged = int(df["fee_key"].isin(list(fees) + ["other"]).sum())

    if sum(agree_counts.values()) > 0:
        counts, method = agree_counts, "code and model agree"
    else:
        counts, method = model_counts, "model only (no agreement with keywords)"
    # Ties break on the model-only count, then registry order.
    winner = max(fees, key=lambda k: (counts[k], model_counts[k]))
    mentions = counts[winner]

    if method == "code and model agree":
        matching = df[(df["fee_key"] == winner) & (df["code_fee"] == winner)]
    else:
        matching = df[df["fee_key"] == winner]
    examples = pick_examples(matching, 3)
    other_examples = pick_examples(df[(df["fee_key"] == "other") & (df["code_fee"] == "other")], 5)

    return {
        "fee_key": winner,
        "label": fees[winner]["label"],
        "mentions": mentions,
        "share_of_fee_mentions": round(mentions / max(sum(counts.values()), 1), 4),
        "weak_signal": fee_tagged < WEAK_SIGNAL_BELOW,
        "method": method,
        "counts": counts,
        "model_counts": model_counts,
        "other_fee_mentions": other_agree,
        "other_fee_mentions_model_only": int((df["fee_key"] == "other").sum()),
        "examples": _render(examples),
        "other_examples": _render(other_examples),
    }
