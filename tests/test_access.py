"""Password gate and data seeding for the hosted app. SYNTHETIC data, no network."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from pulse import access

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def clear(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)


def test_gate_rules(monkeypatch):
    clear(monkeypatch)
    assert not access.password_required() and not access.misconfigured()  # local dev: open
    monkeypatch.setenv("RENDER", "true")
    assert access.misconfigured()  # hosted without a password
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert not access.misconfigured() and access.password_required()
    assert access.password_ok("s3cret") and not access.password_ok("wrong") and not access.password_ok("")


def test_hosted_without_password_refuses_to_start(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("RENDER", "true")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.error and "refuses to start" in at.error[0].value
    assert not at.button  # nothing usable is rendered


def test_password_gate_blocks_until_correct(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.button  # locked: no Run analysis button
    at.text_input[0].set_value("wrong").run()
    assert at.error and not at.button
    at.text_input[0].set_value("s3cret").run()
    assert any(b.label == "Run analysis" for b in at.button)


def test_seed_copies_only_missing_files(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.csv").write_text("seed-a", encoding="utf-8")
    (seed / "b.csv").write_text("seed-b", encoding="utf-8")
    dest_a, dest_b = tmp_path / "data" / "a.csv", tmp_path / "out" / "b.csv"
    dest_b.parent.mkdir()
    dest_b.write_text("existing", encoding="utf-8")
    copied = access.seed_if_missing(seed, {"a.csv": dest_a, "b.csv": dest_b})
    assert copied == [str(dest_a)]
    assert dest_a.read_text(encoding="utf-8") == "seed-a"
    assert dest_b.read_text(encoding="utf-8") == "existing"  # never overwrites
