"""Pipeline stages 1-5 so far: ingest -> themes -> assignment -> ranking -> fee issue.

    python run_pipeline.py [--csv data/reviews.csv] [--refresh]

Labelled reviews and themes are cached in outputs/, so reruns are free unless --refresh is passed.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from pulse.fees import detect_fee_issue
from pulse.ingest import load_reviews
from pulse.quotes import select_quotes
from pulse.themes import assign_reviews, discover_themes, rank_themes, top_themes

OUT = Path("outputs")
REGISTRY = Path("config/fee_registry.json")


def analyse(csv_path, refresh=False, llm=None):
    OUT.mkdir(exist_ok=True)
    labelled_path, themes_path = OUT / "labelled_reviews.csv", OUT / "themes.json"

    df, stats = load_reviews(csv_path)
    print("Ingest:", stats)

    if labelled_path.exists() and themes_path.exists() and not refresh:
        print("Using cached labels (pass --refresh to re-run the model)")
        themes = json.loads(themes_path.read_text(encoding="utf-8"))
        labelled = pd.read_csv(labelled_path, dtype={"review_id": str}, keep_default_na=False, parse_dates=["date"])
    else:
        if llm is None:
            from pulse.llm import LLM
            llm = LLM()
        themes = discover_themes(df, llm)
        print(f"Themes: {[t['name'] for t in themes]}")
        total_batches = -(-len(df) // 80)
        done = []

        def progress():
            done.append(1)
            print(f"  batch {len(done)}/{total_batches}", flush=True)

        labelled = assign_reviews(df, themes, llm, progress=progress)
        print(f"Unlabelled -> Other: {labelled.attrs.get('unlabelled_to_other', 0)}")
        print(f"Model calls: {llm.calls} | tokens in/out: {llm.input_tokens}/{llm.output_tokens} | cost: ${llm.cost_usd():.2f}")
        themes_path.write_text(json.dumps(themes, indent=2), encoding="utf-8")
        labelled.to_csv(labelled_path, index=False)

    ranked = rank_themes(labelled, themes)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    fee_issue = detect_fee_issue(labelled, registry)
    top3 = [t["id"] for t in top_themes(ranked)]
    quotes = select_quotes(labelled, top3)
    return labelled, ranked, fee_issue, quotes


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/reviews.csv")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    _, ranked, fee_issue, quotes = analyse(args.csv, args.refresh)
    print("\nAll themes ranked by volume ('Other / praise' is excluded from the top 3):")
    for i, t in enumerate(ranked, 1):
        print(f"  {i}. {t['name']}: {t['count']} reviews ({t['share']:.1%}), avg {t['avg_rating']}/5 - {t['description']}")
    names = {t["id"]: t["name"] for t in ranked}
    print("\nQuotes (one per top-3 problem theme):")
    for q in quotes:
        print(f"  [{names[q['theme_id']]}, rated {q['rating']}/5] {q['quote']}")
    print("\nIdentified fee issue:")
    print(json.dumps({k: v for k, v in fee_issue.items() if not k.endswith("examples")}, indent=2))
    for e in fee_issue["examples"]:
        print(f"  example (rated {e['rating']}/5): {e['text']}")
    print("\nWhat the 'other' fee complaints look like:")
    for e in fee_issue["other_examples"]:
        print(f"  (rated {e['rating']}/5): {e['text']}")


if __name__ == "__main__":
    main()
