"""Plain-text rendering for the Doc and email. All data is SYNTHETIC."""
from pulse.render import render_doc_entry, to_plain_text

PULSE_MD = """# Weekly Product Pulse 2026-10-16
3592 reviews, 2026-07-24 to 2026-10-15.

## Top themes
1. **App bugs**: 554 reviews (15.4%), avg 1.95/5. Chart errors

## What users said
> "delay chart, slow chart sometimes stuck." (App bugs, 3/5)

## Actions
1. Fix chart lag"""


def test_markdown_symbols_removed():
    text = to_plain_text(PULSE_MD)
    assert "#" not in text and "**" not in text
    assert not any(line.startswith(">") for line in text.splitlines())
    assert text.splitlines()[0] == "WEEKLY PRODUCT PULSE 2026-10-16"
    assert "Top themes:" in text and "1. App bugs: 554 reviews (15.4%)" in text


def test_quotes_stay_verbatim():
    text = to_plain_text(PULSE_MD)
    assert '- "delay chart, slow chart sometimes stuck." (App bugs, 3/5)' in text


def test_doc_entry_has_plain_pulse_and_stays_markdown_free():
    payload = {
        "date": "2026-10-16",
        "top_themes": [{"name": "App bugs", "count": 554, "share": 0.1542}],
        "weekly_pulse": PULSE_MD,
        "identified_fee_issue": {"fee_key": "brokerage", "label": "Equity brokerage", "mentions": 96},
        "explanation_bullets": ["Brokerage is ₹20 or 0.1% per order."],
        "source_links": ["https://groww.in/pricing/stocks"],
    }
    entry = render_doc_entry("abc12345", payload)
    assert "# " not in entry and "**" not in entry
    assert payload["weekly_pulse"].startswith("# ")  # the payload itself stays markdown (PRD)
