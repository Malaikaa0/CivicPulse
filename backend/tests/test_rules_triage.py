import pytest
from pydantic import ValidationError

from app.domain import Category, Priority
from app.providers.triage.base import TriageProvider, TriageResult
from app.providers.triage.rules import RuleBasedTriage

triage = RuleBasedTriage()


def test_rule_based_triage_satisfies_the_provider_interface() -> None:
    provider: TriageProvider = RuleBasedTriage()  # checked by mypy

    assert provider.name == "rules"


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Burst water main flooding Street 12 since fajr", Category.WATER),
        ("Pani ki supply band hai, tanker bhi nahi aa raha", Category.WATER),
        ("Transformer blew and the whole mohalla has no bijli", Category.ELECTRICITY),
        ("Voltage keeps fluctuating and damaged our fridge", Category.ELECTRICITY),
        ("Sewerage water overflowing in gali number 5", Category.SANITATION),
        ("Garbage not picked up for a week, kachra everywhere", Category.SANITATION),
        ("Huge pothole near the flyover, bikes are slipping", Category.ROADS),
        ("The speed breaker in front of the hospital is too high", Category.ROADS),
        ("All streetlights on our street are off since Monday", Category.STREETLIGHTS),
        ("Stray dogs are chasing the children in our street", Category.OTHER),
        ("Something is wrong but I do not know how to describe it", Category.OTHER),
    ],
)
def test_category_from_keywords(text: str, category: Category) -> None:
    assert triage.triage(text, "Street 1").category == category


def test_a_decisive_word_outweighs_incidental_ones() -> None:
    # Mentions bijli and load shedding, but the complaint is about a streetlight.
    text = "Streetlight is on in daytime wasting bijli while we have load shedding at night"

    assert triage.triage(text, "Main Boulevard").category == Category.STREETLIGHTS


@pytest.mark.parametrize(
    ("text", "priority"),
    [
        ("Live wire fallen on the road and sparking near the school", Priority.HIGH),
        ("Water main burst and flooding the street", Priority.HIGH),
        ("Open manhole at the market, a bike fell inside", Priority.HIGH),
        ("Streetlight near the park is flickering at night", Priority.LOW),
        ("The zebra crossing paint has faded", Priority.LOW),
        ("Garbage is collected late every week", Priority.NORMAL),
    ],
)
def test_priority_from_urgency_words(text: str, priority: Priority) -> None:
    assert triage.triage(text, "Street 1").priority == priority


def test_a_negated_flood_is_not_treated_as_urgent() -> None:
    text = "Small leakage from the pipe, water is wasting but it is not flooding the road"

    assert triage.triage(text, "Street 1").priority == Priority.LOW


def test_same_input_always_gives_the_same_result() -> None:
    text = "Transformer blew and the whole mohalla has no bijli"

    assert {triage.triage(text, "x") for _ in range(20)} == {triage.triage(text, "x")}


def test_summary_is_the_first_sentence_and_within_the_limit() -> None:
    result = triage.triage("Burst pipe on Street 12. Please send a team urgently.", "Street 12")

    assert result.summary == "Burst pipe on Street 12."


def test_long_text_is_cut_on_a_word_boundary_to_fit() -> None:
    text = "pothole " * 250  # 2000 characters, no sentence break

    result = triage.triage(text, "Street 1")

    assert len(result.summary) <= 140
    assert result.summary.endswith("…")


@pytest.mark.parametrize("text", ["", "   ", "!!!???", "پانی نہیں آ رہا", "a" * 5000, "\n\t"])
def test_never_raises_and_always_returns_a_valid_result(text: str) -> None:
    result = triage.triage(text, "Street 1")

    assert isinstance(result, TriageResult)
    assert 1 <= len(result.summary) <= 140
    assert 0.0 <= result.confidence <= 1.0


def test_unmatched_text_is_low_confidence() -> None:
    assert triage.triage("Something odd happened", "x").confidence == 0.3


def test_result_schema_rejects_what_an_llm_might_return() -> None:
    good = {"category": "water", "priority": "high", "summary": "ok", "confidence": 0.9}
    TriageResult.model_validate(good)

    for bad in (
        {**good, "category": "parks"},  # not in the enum
        {**good, "priority": "urgent"},  # not in the enum
        {**good, "summary": "x" * 141},  # too long
        {**good, "summary": ""},  # empty
        {**good, "confidence": 1.5},  # out of range
    ):
        with pytest.raises(ValidationError):
            TriageResult.model_validate(bad)
