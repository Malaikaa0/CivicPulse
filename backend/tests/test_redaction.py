import pytest

from app.providers.triage.redaction import redact_pii


@pytest.mark.parametrize(
    "phone",
    [
        "0300-1112233",  # mobile with dash
        "03001112233",  # mobile, no separators
        "0300 1112233",  # mobile with space
        "+92 300 1112233",  # international
        "+923001112233",
        "0092-300-1112233",
        "(042) 3567 8901",  # landline with brackets
        "042-35678901",
    ],
)
def test_phone_numbers_are_redacted(phone: str) -> None:
    result = redact_pii(f"Please call me on {phone} as soon as possible")

    assert "[NUMBER]" in result
    assert not any(ch.isdigit() for ch in result)


def test_national_id_number_is_redacted() -> None:
    assert redact_pii("My CNIC is 35202-1234567-1 and I live here") == (
        "My CNIC is [NUMBER] and I live here"
    )


def test_emails_are_redacted() -> None:
    result = redact_pii("Contact ahmed.khan+civic@example.com about the leak")

    assert result == "Contact [EMAIL] about the leak"


@pytest.mark.parametrize(
    "text",
    [
        "Burst water main flooding Street 12 since fajr",
        "House 44, Gulberg Road, Block C",
        "Tanker walay 2000 rupay maang rahay hain",
        "No water for three days and 2 nights",
        "Load shedding 8 to 10 hours daily",
        "Streetlights off on Street 9 for 2 weeks",
        "Flat 8, Al-Noor Apartments, 3rd floor",
        "Since 2024 nothing has been fixed",
    ],
)
def test_numbers_that_matter_for_triage_are_left_alone(text: str) -> None:
    assert redact_pii(text) == text


def test_several_identifiers_in_one_complaint() -> None:
    result = redact_pii("Call 0300-1112233 or mail sara@example.org, CNIC 35202-1234567-1")

    assert result == "Call [NUMBER] or mail [EMAIL], CNIC [NUMBER]"


def test_redaction_is_idempotent() -> None:
    once = redact_pii("Call 0300-1112233 or mail sara@example.org")

    assert redact_pii(once) == once


@pytest.mark.parametrize("text", ["", " ", "!!!", "پانی نہیں آ رہا 0300-1112233", "a" * 10_000])
def test_never_raises_on_odd_input(text: str) -> None:
    assert isinstance(redact_pii(text), str)


def test_the_words_around_an_identifier_survive_so_triage_still_works() -> None:
    result = redact_pii("Burst water main flooding Street 12. Call Bilal on 0300-1112233 urgently")

    assert "Burst water main flooding Street 12" in result
    assert "urgently" in result
