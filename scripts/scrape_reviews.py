"""Scrape public Google Play reviews for Groww into the CSV contract in implementation.md §4.

Run on your own machine:
    python scripts/scrape_reviews.py --weeks 12 --out data/reviews.csv

Reviewer names and profile images are never stored.
"""
import argparse
import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from google_play_scraper import Sort, reviews

APP_ID = "com.nextbillion.groww"
PAGE_SIZE = 200
PAUSE_SECONDS = 1
MAX_RETRIES = 4
COLUMNS = ["review_id", "date", "rating", "text", "thumbs_up", "app_version", "source"]


def weeks_arg(value):
    weeks = int(value)
    if not 8 <= weeks <= 12:
        raise argparse.ArgumentTypeError("--weeks must be between 8 and 12")
    return weeks


def fetch_page(token):
    """One page of reviews, retrying with exponential backoff on failure."""
    for attempt in range(MAX_RETRIES):
        try:
            return reviews(
                APP_ID,
                lang="en",
                country="in",
                sort=Sort.NEWEST,
                count=PAGE_SIZE,
                continuation_token=token,
            )
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"Fetch failed ({exc}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)


def to_row(review):
    # userName / userImage are deliberately not read.
    return {
        "review_id": review["reviewId"],
        "date": review["at"].date().isoformat(),
        "rating": review["score"],
        "text": " ".join((review["content"] or "").split()),
        "thumbs_up": review["thumbsUpCount"],
        "app_version": review.get("reviewCreatedVersion") or "",
        "source": "google_play",
    }


def scrape(weeks, max_reviews):
    cutoff = datetime.now() - timedelta(weeks=weeks)
    seen, rows = set(), []
    token = None
    reached_cutoff = False
    while len(rows) < max_reviews:
        batch, token = fetch_page(token)
        if not batch:
            break
        for review in batch:
            if review["at"] < cutoff:
                reached_cutoff = True
                break
            row = to_row(review)
            if not row["text"] or row["review_id"] in seen:
                continue
            seen.add(row["review_id"])
            rows.append(row)
            if len(rows) >= max_reviews:
                break
        print(f"Collected {len(rows)} reviews")
        if reached_cutoff or token is None:
            break
        time.sleep(PAUSE_SECONDS)
    if not reached_cutoff and len(rows) >= max_reviews:
        print(
            f"WARNING: stopped at --max-reviews={max_reviews} before reaching the "
            f"{weeks}-week cutoff; the window is shorter than requested. Raise --max-reviews.",
            file=sys.stderr,
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", type=weeks_arg, default=12, help="review window in weeks (8-12)")
    parser.add_argument("--max-reviews", type=int, default=5000)
    parser.add_argument("--out", default="data/reviews.csv")
    args = parser.parse_args()

    rows = scrape(args.weeks, args.max_reviews)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    if rows:
        dates = [r["date"] for r in rows]
        print(f"Wrote {len(rows)} reviews to {out} ({min(dates)} to {max(dates)})")
    else:
        print(f"Wrote 0 reviews to {out}")


if __name__ == "__main__":
    main()
