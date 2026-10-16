"""Hosting support: password gate and first-start data seeding (used by app.py).

On Render the RENDER env var is set. There the app refuses to run without APP_PASSWORD, so a hosted copy
can never be open by accident. Locally (no RENDER, no APP_PASSWORD) there is no gate.
"""
import hmac
import os
import shutil
from pathlib import Path

SEED_DIR = Path("deploy_seed")
SEED_MAP = {
    "reviews.csv": Path("data/reviews.csv"),
    "labelled_reviews.csv": Path("outputs/labelled_reviews.csv"),
    "themes.json": Path("outputs/themes.json"),
}


def misconfigured():
    """Hosted without a password."""
    return bool(os.getenv("RENDER")) and not os.getenv("APP_PASSWORD")


def password_required():
    return bool(os.getenv("RENDER")) or bool(os.getenv("APP_PASSWORD"))


def password_ok(entered):
    expected = os.getenv("APP_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def seed_if_missing(seed_dir=SEED_DIR, seed_map=None):
    """Copy the committed, PII-masked data into data/ and outputs/ when they are absent (fresh host)."""
    copied = []
    for name, dest in (seed_map or SEED_MAP).items():
        src = Path(seed_dir) / name
        if src.exists() and not Path(dest).exists():
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
            copied.append(str(dest))
    return copied
