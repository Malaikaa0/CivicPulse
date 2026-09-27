"""The content-hash triage cache, against an in-memory store (no Redis, no network)."""

import json
import logging

import pytest
from triage_store_fake import FakeStore

from app.domain import Category, Priority
from app.providers.triage.base import TriageResult
from app.providers.triage.caching import (
    HITS_KEY,
    KEY_PREFIX,
    MISSES_KEY,
    CacheCounters,
    CacheStats,
    CachingTriage,
    normalise,
)
from app.providers.triage.errors import (
    TriageBadRequest,
    TriageError,
    TriageInvalidOutput,
    TriageRateLimited,
    TriageServerError,
    TriageTimeout,
)
from app.providers.triage.simulated import SimulatedTriage
from app.services.triage import FALLBACK_NAME, TriageService

TEXT = "Burst water main flooding Street 12, call me on 0300-1234567 or ali.khan@example.com"
TTL = 86400


def _cache(
    store: FakeStore | None = None, provider: SimulatedTriage | None = None, ttl: int = TTL
) -> tuple[CachingTriage, SimulatedTriage, FakeStore]:
    store = store or FakeStore()
    provider = provider or SimulatedTriage()
    return CachingTriage(provider, store, ttl), provider, store


def test_second_identical_complaint_is_served_from_cache() -> None:
    cache, provider, _ = _cache()

    plain = "Burst water main on Street 12"

    first = cache.triage(plain, "Street 12")
    second = cache.triage(plain, "Street 12")

    assert provider.calls == 1
    assert second == first
    assert first.category == Category.WATER


def test_reports_the_same_name_as_the_wrapped_provider() -> None:
    cache, provider, _ = _cache()

    assert cache.name == provider.name == "simulated"


@pytest.mark.parametrize(
    "variant",
    [
        TEXT.upper(),
        TEXT.lower(),
        "  " + TEXT.replace(" ", "   ").replace(",", ",\n"),
        TEXT.replace("0300-1234567", "0321 7654321"),
        TEXT.replace("0300-1234567", "+92 300 1234567"),
        TEXT.replace("ali.khan@example.com", "someone.else@mail.pk"),
    ],
)
def test_near_duplicates_share_one_entry(variant: str) -> None:
    cache, provider, _ = _cache()

    cache.triage(TEXT, "Street 12")
    cache.triage(variant, "Street 12")

    assert provider.calls == 1


def test_a_different_complaint_is_a_different_entry() -> None:
    cache, provider, store = _cache()

    cache.triage(TEXT, "Street 12")
    cache.triage("Streetlight broken outside the school", "Street 12")

    assert provider.calls == 2
    assert len(store.cache_entries()) == 2


def test_short_numbers_still_distinguish_complaints() -> None:
    # Only identifier-length numbers are redacted; "Street 12" and "Street 13" must not collide.
    cache, provider, _ = _cache()

    cache.triage("Pothole on Street 12", "x")
    cache.triage("Pothole on Street 13", "x")

    assert provider.calls == 2


def test_location_is_not_part_of_the_key() -> None:
    cache, provider, _ = _cache()

    cache.triage(TEXT, "Street 12")
    cache.triage(TEXT, "Gulberg, Lahore")

    assert provider.calls == 1


def test_a_different_provider_name_is_a_different_key() -> None:
    class Named(SimulatedTriage):
        name = "llm:gemini"

    cache_a, _, _ = _cache()
    cache_b = CachingTriage(Named(), FakeStore(), TTL)

    assert cache_a.cache_key(TEXT) != cache_b.cache_key(TEXT)


def test_the_key_is_stable_and_prefixed() -> None:
    cache, _, _ = _cache()

    key = cache.cache_key(TEXT)

    assert key == cache.cache_key(TEXT.upper())
    assert key.startswith(KEY_PREFIX)
    assert len(key) == len(KEY_PREFIX) + 64


def test_normalise_redacts_then_folds_case_and_whitespace() -> None:
    assert normalise("  Call  ME on 0300-1234567\n now ") == "call me on [number] now"


def test_no_raw_pii_or_complaint_text_reaches_the_store() -> None:
    cache, _, store = _cache()

    cache.triage(TEXT, "Street 12")
    cache.triage(TEXT, "Street 12")

    assert store.seen_values, "expected a value to have been stored"
    keys = "\n".join(store.seen_keys).lower()
    for word in ("burst", "flooding", "street", "call me"):
        assert word not in keys  # keys are opaque hashes, not text
    everything = "\n".join(store.seen_keys + store.seen_values).lower()
    for identifier in ("0300", "1234567", "ali.khan", "example.com"):
        assert identifier not in everything


def test_a_summary_that_quotes_the_complaint_is_redacted_before_it_is_stored() -> None:
    # The rule-based provider's summary is the complaint text, phone number included. The next
    # near-duplicate is handed the stored entry, so it must not carry the first reporter's number.
    cache, _, _ = _cache()

    first = cache.triage(TEXT, "Street 12")
    second = cache.triage(TEXT.replace("0300-1234567", "0321-7654321"), "Street 12")

    assert "0300" in first.summary  # the first caller still gets the provider's own answer
    assert "[NUMBER]" in second.summary
    assert "0300" not in second.summary
    assert "ali.khan" not in second.summary


def test_a_redacted_summary_is_cut_back_to_the_schema_limit() -> None:
    class Wordy:
        name = "wordy"

        def triage(self, text: str, location: str) -> TriageResult:
            return GOOD.model_copy(update={"summary": "a@b.co " * 20})  # 120 chars

    store = FakeStore()
    cache = CachingTriage(Wordy(), store, TTL)

    cache.triage("wordy", "x")
    stored = TriageResult.model_validate_json(next(iter(store.cache_entries().values())))

    assert len(stored.summary) == 140
    assert "a@b.co" not in stored.summary


def test_the_stored_value_is_the_validated_result_as_json() -> None:
    cache, _, store = _cache()

    result = cache.triage("Burst water main on Street 12", "Street 12")

    [stored] = store.cache_entries().values()
    assert TriageResult.model_validate(json.loads(stored)) == result


def test_entries_are_written_with_the_configured_ttl() -> None:
    cache, _, store = _cache(ttl=1234)

    cache.triage(TEXT, "Street 12")

    assert list(store.ttls.values()) == [1234]


@pytest.mark.parametrize(
    "error",
    [TriageTimeout(), TriageRateLimited(), TriageServerError(), TriageBadRequest()],
)
def test_provider_errors_propagate_and_nothing_is_cached(error: TriageError) -> None:
    cache, provider, store = _cache(provider=SimulatedTriage(always_fail=error))

    with pytest.raises(type(error)):
        cache.triage(TEXT, "Street 12")

    assert store.cache_entries() == {}
    assert provider.calls == 1


def test_invalid_provider_output_is_not_cached() -> None:
    cache, _, store = _cache(provider=SimulatedTriage(always_fail=TriageInvalidOutput()))

    with pytest.raises(TriageInvalidOutput):
        cache.triage(TEXT, "Street 12")

    assert store.cache_entries() == {}


def test_a_failed_call_is_retried_and_only_the_success_is_cached() -> None:
    provider = SimulatedTriage(script=[TriageTimeout(), None])
    cache, _, store = _cache(provider=provider)
    service = TriageService(cache, sleep=lambda _: None, jitter=lambda: 0.0)

    outcome = service.triage(TEXT, "Street 12")

    assert outcome.fallback is False
    assert provider.calls == 2
    assert len(store.cache_entries()) == 1


def test_a_fallback_result_is_never_cached() -> None:
    provider = SimulatedTriage(always_fail=TriageBadRequest())
    cache, _, store = _cache(provider=provider)
    service = TriageService(cache, sleep=lambda _: None, jitter=lambda: 0.0)

    first = service.triage(TEXT, "Street 12")
    second = service.triage(TEXT, "Street 12")

    assert first.triaged_by == second.triaged_by == FALLBACK_NAME
    assert store.cache_entries() == {}
    assert provider.calls == 2  # the provider was asked again: the fallback was not remembered


def test_triage_service_keeps_the_inner_name_for_triaged_by() -> None:
    cache, _, _ = _cache()

    outcome = TriageService(cache).triage(TEXT, "Street 12")

    assert outcome.triaged_by == "rules"  # simulated is stored as rules


# --- damaged entries are misses, never errors --------------------------------------------------

GOOD = TriageResult(
    category=Category.ROADS, priority=Priority.LOW, summary="Cached earlier", confidence=0.5
)


@pytest.mark.parametrize(
    "damaged",
    [
        "{not json",
        "",
        "null",
        "[1, 2, 3]",
        '"just a string"',
        "42",
        '{"category": "water"}',
        '{"category": "invented", "priority": "high", "summary": "x", "confidence": 0.5}',
        '{"category": "water", "priority": "high", "summary": "x", "confidence": 7}',
        '{"category": "water", "priority": "high", "summary": "", "confidence": 0.5}',
        '{"category": 5, "priority": "high", "summary": "x", "confidence": "high"}',
    ],
)
def test_a_damaged_entry_is_a_miss_that_is_overwritten(
    damaged: str, caplog: pytest.LogCaptureFixture
) -> None:
    cache, provider, store = _cache()
    store.values[cache.cache_key(TEXT)] = damaged

    with caplog.at_level(logging.WARNING):
        result = cache.triage(TEXT, "Street 12")

    assert provider.calls == 1
    assert result.category == Category.WATER
    assert store.values[cache.cache_key(TEXT)] != damaged  # replaced by the fresh result
    cache.triage(TEXT, "Street 12")
    assert provider.calls == 1  # ...and now it is served from cache
    assert "unreadable triage cache entry" in caplog.text


def test_a_value_of_the_wrong_type_is_a_miss() -> None:
    cache, provider, store = _cache()
    store.values[cache.cache_key(TEXT)] = 12345  # type: ignore[assignment]

    result = cache.triage(TEXT, "Street 12")

    assert result.category == Category.WATER
    assert provider.calls == 1


def test_a_valid_entry_is_served_without_calling_the_provider() -> None:
    cache, provider, store = _cache()
    store.values[cache.cache_key(TEXT)] = GOOD.model_dump_json()

    assert cache.triage(TEXT, "Street 12") == GOOD
    assert provider.calls == 0


# --- Redis down or slow: the cache must never break triage -------------------------------------


def test_a_failing_lookup_still_triages_and_skips_the_writes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache, provider, store = _cache()
    store.fail_get = True

    with caplog.at_level(logging.WARNING):
        result = cache.triage(TEXT, "Street 12")

    assert result.category == Category.WATER
    assert provider.calls == 1
    assert store.cache_entries() == {}
    assert store.seen_keys.count(HITS_KEY) == store.seen_keys.count(MISSES_KEY) == 0
    assert "triage cache unavailable" in caplog.text


def test_a_failing_write_still_returns_the_result() -> None:
    cache, provider, store = _cache()
    store.fail_set = True

    first = cache.triage(TEXT, "Street 12")
    second = cache.triage(TEXT, "Street 12")

    assert first == second
    assert provider.calls == 2  # nothing was remembered, so the provider ran again


def test_failing_counters_do_not_break_a_hit_or_a_miss() -> None:
    cache, provider, store = _cache()
    store.fail_incr = True

    cache.triage(TEXT, "Street 12")
    cache.triage(TEXT, "Street 12")

    assert provider.calls == 1


def test_provider_errors_still_propagate_when_the_store_is_down() -> None:
    cache, _, store = _cache(provider=SimulatedTriage(always_fail=TriageTimeout()))
    store.fail_get = True

    with pytest.raises(TriageTimeout):
        cache.triage(TEXT, "Street 12")


# --- hit rate ----------------------------------------------------------------------------------


def test_hit_rate_is_none_before_any_lookup() -> None:
    cache, _, _ = _cache()

    assert cache.hit_rate() is None
    assert cache.stats() == CacheStats(hits=0, misses=0)


def test_hit_rate_counts_hits_over_lookups() -> None:
    cache, _, store = _cache()

    for text in (TEXT, TEXT, TEXT, "Streetlight out on Main Road"):
        cache.triage(text, "x")

    assert cache.stats() == CacheStats(hits=2, misses=2)
    assert cache.hit_rate() == 0.5
    assert store.values[HITS_KEY] == "2"
    assert store.values[MISSES_KEY] == "2"


def test_hit_rate_is_zero_when_every_lookup_missed() -> None:
    cache, _, _ = _cache()

    cache.triage("Pothole on Street 1", "x")
    cache.triage("Pothole on Street 2", "x")

    assert cache.hit_rate() == 0.0


def test_a_damaged_counter_reads_as_zero() -> None:
    store = FakeStore()
    store.values[HITS_KEY] = "not a number"
    store.values[MISSES_KEY] = "3"

    assert CacheCounters(store).snapshot() == CacheStats(hits=0, misses=3)


def test_counters_are_shared_through_the_store() -> None:
    store = FakeStore()
    first, _, _ = _cache(store)
    second, _, _ = _cache(store)  # another replica

    first.triage(TEXT, "x")
    second.triage(TEXT, "x")

    assert first.stats() == second.stats() == CacheStats(hits=1, misses=1)


# --- the measured scenario from the brief ------------------------------------------------------

BURST_REPORTS = [
    "Burst water main flooding Street 12, call me on 0300-1234567",
    "burst water main flooding street 12, call me on 0321-7654321",
    "Burst  water main   flooding Street 12, call me on +92 333 5550101",
    "BURST WATER MAIN FLOODING STREET 12, CALL ME ON 0345 1112223",
    "Burst water main flooding Street 12, call me on 03007778889",
    "  burst water main flooding street 12, call me on 0300-1234567  ",
    "Burst water main flooding Street 12, call me on 0311 2223334",
    "Burst water main flooding Street 12, call me on 0333-9998887",
    "burst water main flooding Street 12, call me on 0301 4445556",
]


def test_burst_scenario_costs_two_inferences_for_ten_submissions() -> None:
    provider = SimulatedTriage()
    cache = CachingTriage(provider, FakeStore(), TTL)
    service = TriageService(cache)
    submissions = [*BURST_REPORTS[:4], "Streetlight out near the school gate", *BURST_REPORTS[4:]]
    assert len(submissions) == 10

    outcomes = [service.triage(text, "Street 12") for text in submissions]

    assert provider.calls == 2
    assert cache.stats() == CacheStats(hits=8, misses=2)
    assert cache.hit_rate() == 8 / 10
    assert not any(o.fallback for o in outcomes)
    assert {o.result.category for o in outcomes[:4]} == {Category.WATER}
