"""Pipeline stages 1-5 so far: ingest -> themes -> assignment -> ranking -> fee issue.

    python run_pipeline.py [--csv data/reviews.csv] [--refresh]

Labelled reviews and themes are cached in outputs/, so reruns are free unless --refresh is passed.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from pulse.fees import detect_fee_issue
from pulse.generate import generate_run, render_support_snippet
from pulse.ingest import load_reviews
from pulse.quotes import select_quotes
from pulse.themes import assign_reviews, discover_themes, rank_themes, top_themes

OUT = Path("outputs")
REGISTRY = Path("config/fee_registry.json")
PULSE_QUOTE_MAX_CHARS = 120  # keeps the pulse inside its 250-word budget


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
    quotes = select_quotes(labelled, top3, max_chars=PULSE_QUOTE_MAX_CHARS)
    return labelled, ranked, fee_issue, quotes


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/reviews.csv")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--analysis-only", action="store_true", help="skip writing the pulse and explainer")
    args = parser.parse_args()

    labelled, ranked, fee_issue, quotes = analyse(args.csv, args.refresh)
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

    if args.analysis_only:
        return
    from pulse.llm import LLM
    from pulse.validate import word_count

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    llm = LLM()
    run = generate_run(labelled, top_themes(ranked), quotes, names, fee_issue, registry, llm,
                       date.today().isoformat(), int(os.getenv("REVIEW_WEEKS", 12)))
    print(f"\n===== WEEKLY PULSE ({word_count(run['pulse'])} words, limit 250) =====\n{run['pulse']}")
    print(f"\n===== FEE EXPLAINER =====\n{render_support_snippet(run['explainer'])}")
    print(f"\nRun {run['run_id']} | model calls: {llm.calls} | cost: ${llm.cost_usd():.3f}")
    print("Saved:", ", ".join(str(p) for p in run["paths"].values()))
    print("Approval status: pending (nothing has been written to Google Docs or Gmail)")


if __name__ == "__main__":
    main()
