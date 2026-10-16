"""Theme discovery, batched assignment and ranking (implementation.md §5.2, §5.3)."""
import json
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

MAX_THEMES = 5
SAMPLE_SIZE = 300
BATCH_SIZE = 80
WORKERS = 4
FEE_KEYS = ["dp_charges", "exit_load", "brokerage", "other", "none"]

DISCOVER_SYSTEM = """You group Google Play reviews of the Groww investing app into themes.
Rules:
- At most 5 themes in total, mutually exclusive, product-level (for example "Withdrawal delays", "App crashes", "Customer support").
- The LAST theme must be an "Other / praise" bucket for general praise and anything that fits nowhere else.
- name: 5 words or fewer. description: one line. ids: T1, T2, ... in order.
The input is a JSON list of reviews with their star ratings."""

ASSIGN_SYSTEM = """You label Google Play reviews of the Groww investing app.
For every review in the input, return exactly one assignment:
- theme_id: the single best-fitting theme from the given themes.
- fee_key: dp_charges (DP/depository charge when selling shares), exit_load (mutual fund exit load or redemption deduction),
  brokerage (brokerage, minimum brokerage, STT, stamp duty), other (any other fee or charge complaint), or none (no fee or charge mentioned).
Return every input id exactly once."""

DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["id", "name", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["themes"],
    "additionalProperties": False,
}


class ThemeError(Exception):
    pass


def assign_schema(theme_ids):
    return {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "theme_id": {"type": "string", "enum": theme_ids},
                        "fee_key": {"type": "string", "enum": FEE_KEYS},
                    },
                    "required": ["id", "theme_id", "fee_key"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["assignments"],
        "additionalProperties": False,
    }


def sample_for_discovery(df, size=SAMPLE_SIZE):
    """Half 1-2 star, half 3-5 star, longer reviews preferred."""
    low = df[df["rating"] <= 2].assign(_len=lambda d: d["text"].str.len()).sort_values("_len", ascending=False)
    high = df[df["rating"] >= 3].assign(_len=lambda d: d["text"].str.len()).sort_values("_len", ascending=False)
    return pd.concat([low.head(size // 2), high.head(size // 2)])


def discover_themes(df, llm):
    sample = sample_for_discovery(df)
    payload = json.dumps([{"rating": int(r.rating), "text": r.text} for r in sample.itertuples()])
    for attempt in range(2):
        themes = llm.call_json(DISCOVER_SYSTEM, payload, DISCOVER_SCHEMA, effort="medium")["themes"]
        ids = [t["id"] for t in themes]
        if 1 <= len(themes) <= MAX_THEMES and len(set(ids)) == len(ids):
            return themes
    raise ThemeError(f"Model proposed an invalid theme set: {ids}")


def fallback_theme_id(themes):
    """The 'Other' bucket gets leftovers; if the model didn't label one, use the last theme."""
    for t in themes:
        if t["name"].lower().startswith("other"):
            return t["id"]
    return themes[-1]["id"]


def _assign_batch(batch, themes, llm):
    """batch: list of (local_id, text). Returns {local_id: (theme_id, fee_key)}; retries missing ids once."""
    theme_ids = [t["id"] for t in themes]
    schema = assign_schema(theme_ids)
    result = {}
    pending = list(batch)
    for attempt in range(2):
        if not pending:
            break
        user = json.dumps(
            {
                "themes": [{"id": t["id"], "name": t["name"], "description": t["description"]} for t in themes],
                "reviews": [{"id": i, "text": text} for i, text in pending],
            }
        )
        returned = llm.call_json(ASSIGN_SYSTEM, user, schema, effort="low")["assignments"]
        wanted = {i for i, _ in pending}
        for a in returned:
            if a["id"] in wanted and a["id"] not in result and a["theme_id"] in theme_ids and a["fee_key"] in FEE_KEYS:
                result[a["id"]] = (a["theme_id"], a["fee_key"])
        pending = [(i, text) for i, text in pending if i not in result]
    return result


def assign_reviews(df, themes, llm, batch_size=BATCH_SIZE, progress=None):
    """Return df with theme_id and fee_key columns. Reviews the model never labels go to 'Other'."""
    fallback = fallback_theme_id(themes)
    rows = list(df.itertuples())
    batches = [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)]

    def run(batch):
        local = [(str(i), r.text) for i, r in enumerate(batch)]
        labels = _assign_batch(local, themes, llm)
        if progress:
            progress()
        return [(r.review_id, *labels.get(str(i), (fallback, "none"))) for i, r in enumerate(batch)], len(batch) - len(labels)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outputs = list(pool.map(run, batches))

    labelled = {rid: (theme_id, fee_key) for out, _ in outputs for rid, theme_id, fee_key in out}
    unlabelled = sum(missing for _, missing in outputs)
    df = df.copy()
    df["theme_id"] = df["review_id"].map(lambda rid: labelled[rid][0])
    df["fee_key"] = df["review_id"].map(lambda rid: labelled[rid][1])
    df.attrs["unlabelled_to_other"] = unlabelled
    return df


def rank_themes(df, themes):
    """Count, share and average rating per theme, sorted by count (code, not the model)."""
    by_id = {t["id"]: t for t in themes}
    total = len(df)
    ranked = []
    for theme_id, group in df.groupby("theme_id"):
        ranked.append(
            {
                "id": theme_id,
                "name": by_id[theme_id]["name"],
                "description": by_id[theme_id]["description"],
                "count": len(group),
                "share": round(len(group) / total, 4),
                "avg_rating": round(float(group["rating"].mean()), 2),
            }
        )
    return sorted(ranked, key=lambda t: t["count"], reverse=True)


def top_themes(ranked, n=3):
    """Top n problem themes by volume. The 'Other / praise' bucket is ranked but never reported as a top theme."""
    return [t for t in ranked if not t["name"].lower().startswith("other")][:n]
