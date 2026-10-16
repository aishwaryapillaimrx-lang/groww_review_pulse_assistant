"""Rule enforcers for the pulse and fee explainer (implementation.md §5.6, §5.7)."""
import re

PULSE_WORD_LIMIT = 250
MAX_BULLETS = 6
ADVICE_WORDS = ["should", "we recommend", "recommend", "best"]
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class ValidationError(Exception):
    pass


def word_count(text):
    return len(text.split())


def check_pulse(markdown, limit=PULSE_WORD_LIMIT):
    count = word_count(markdown)
    if count > limit:
        raise ValidationError(f"Pulse is {count} words; limit is {limit}")


def numbers_in(text):
    """Numeric values in text, ignoring ₹ , and %. '₹16.50' -> '16.50', '₹27,000' -> '27000'."""
    return {m.replace(",", "") for m in NUMBER.findall(text)}


def check_explainer(bullets, facts):
    if not bullets:
        raise ValidationError("Explainer has no bullets")
    if len(bullets) > MAX_BULLETS:
        raise ValidationError(f"Explainer has {len(bullets)} bullets; limit is {MAX_BULLETS}")

    allowed = numbers_in(" ".join(facts))
    for bullet in bullets:
        unknown = numbers_in(bullet) - allowed
        if unknown:
            raise ValidationError(f"Number(s) {sorted(unknown)} not in registry facts: {bullet!r}")
        lowered = bullet.lower()
        for word in ADVICE_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                raise ValidationError(f"Advice word {word!r} in bullet: {bullet!r}")


def check_links(links, fee_entry):
    expected = [s["url"] for s in fee_entry["sources"]]
    if list(links) != expected:
        raise ValidationError(f"Links {list(links)} do not match registry {expected}")


def check_numbers_grounded(text, facts_text):
    """Every number in model-written text must appear in the computed facts (PRD R2.3)."""
    unknown = numbers_in(text) - numbers_in(facts_text)
    if unknown:
        raise ValidationError(f"Number(s) {sorted(unknown)} are not in the computed analysis")
