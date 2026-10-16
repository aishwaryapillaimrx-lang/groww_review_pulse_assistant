"""Load and clean the reviews CSV (implementation.md §5.1)."""
import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

MIN_CHARS = 20
MIN_LATIN_SHARE = 0.8  # share of letters that must be ASCII for a review to count as English


def latin_share(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isascii() for c in letters) / len(letters)


def load_reviews(path, weeks=None, max_reviews=None):
    """Return (clean reviews newest-first, stats of rows dropped per reason)."""
    weeks = weeks or int(os.getenv("REVIEW_WEEKS", 12))
    max_reviews = max_reviews or int(os.getenv("MAX_REVIEWS", 1500))

    df = pd.read_csv(path, dtype={"review_id": str}, keep_default_na=False)
    df["date"] = pd.to_datetime(df["date"])
    df["text"] = df["text"].astype(str).str.strip()
    stats = {"loaded": len(df)}

    # Window is anchored on the newest review in the file.
    start = df["date"].max() - pd.Timedelta(weeks=weeks)
    in_window = df["date"] >= start
    stats["outside_window"] = int((~in_window).sum())
    df = df[in_window]

    empty = df["text"] == ""
    stats["empty"] = int(empty.sum())
    df = df[~empty]

    short = df["text"].str.len() < MIN_CHARS
    stats["too_short"] = int(short.sum())
    df = df[~short]

    dupes = df.duplicated(subset="review_id") | df.duplicated(subset="text")
    stats["duplicate"] = int(dupes.sum())
    df = df[~dupes]

    non_english = df["text"].map(latin_share) < MIN_LATIN_SHARE
    stats["non_english"] = int(non_english.sum())
    df = df[~non_english]

    df = df.sort_values("date", ascending=False)
    stats["over_cap"] = max(len(df) - max_reviews, 0)
    df = df.head(max_reviews).reset_index(drop=True)
    stats["kept"] = len(df)
    return df, stats
