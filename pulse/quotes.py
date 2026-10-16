"""Quote selection, PII masking and verbatim check (implementation.md §5.4). No LLM."""
import re

MIN_QUOTE_CHARS = 60
MAX_QUOTE_CHARS = 300
LOW_RATING_THEME = 3.5  # themes averaging below this are "problem" themes

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b")
LONG_DIGITS = re.compile(r"\d{8,}")


class QuoteError(Exception):
    pass


def mask_pii(text):
    text = EMAIL.sub("[email]", text)
    text = PHONE.sub("[phone]", text)
    return LONG_DIGITS.sub("[number]", text)


def is_verbatim(original, all_texts):
    return any(original in t for t in all_texts)


def select_quotes(df, theme_ids):
    """One quote per theme. df needs review_id, text, rating, thumbs_up, date, theme_id."""
    all_texts = df["text"].tolist()
    used = set()
    quotes = []
    for theme_id in theme_ids:
        theme = df[df["theme_id"] == theme_id]
        length = theme["text"].str.len()
        pool = theme[(length >= MIN_QUOTE_CHARS) & (length <= MAX_QUOTE_CHARS)]
        pool = pool[~pool["review_id"].isin(used)]

        # Problem themes prefer unhappy reviews, praise themes prefer happy ones.
        if theme["rating"].mean() < LOW_RATING_THEME:
            preferred = pool[pool["rating"] <= 3]
        else:
            preferred = pool[pool["rating"] >= 4]
        pool = preferred if len(preferred) else pool
        if pool.empty:
            raise QuoteError(f"No quote candidate for theme {theme_id}")

        row = pool.sort_values(["thumbs_up", "date"], ascending=False).iloc[0]
        original = row["text"]
        if not is_verbatim(original, all_texts):
            raise QuoteError(f"Quote for {theme_id} is not verbatim in the CSV")
        used.add(row["review_id"])
        quotes.append(
            {
                "theme_id": theme_id,
                "review_id": row["review_id"],
                "quote": mask_pii(original),
                "rating": int(row["rating"]),
            }
        )
    return quotes
