"""Build deploy_seed/ for hosting: the reviews CSV and cached analysis with personal details masked.

    python scripts/build_deploy_seed.py

Reads data/reviews.csv and outputs/{labelled_reviews.csv,themes.json}; writes masked copies to
deploy_seed/. Local files are not modified. The hosted app copies deploy_seed/ into data/ and outputs/
on first start (see app.py).
"""
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pulse.quotes import mask_pii  # noqa: E402

SEED = Path("deploy_seed")
FILES = {"data/reviews.csv": "reviews.csv", "outputs/labelled_reviews.csv": "labelled_reviews.csv"}


def main():
    SEED.mkdir(exist_ok=True)
    for src, name in FILES.items():
        df = pd.read_csv(src, dtype={"review_id": str}, keep_default_na=False)
        masked = df["text"].map(mask_pii)
        print(f"{name}: {len(df)} rows, {int((masked != df['text']).sum())} reviews masked")
        df["text"] = masked
        df.to_csv(SEED / name, index=False)
    shutil.copy("outputs/themes.json", SEED / "themes.json")
    print(f"Wrote {SEED}/")


if __name__ == "__main__":
    main()
