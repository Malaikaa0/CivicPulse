from collections import Counter
from datetime import UTC, datetime

from app.domain import Category, Priority, Status
from app.seed import build_rows, seed_id
from app.seed_data import SEED_COMPLAINTS

FIXED_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_at_least_thirty_complaints() -> None:
    assert len(SEED_COMPLAINTS) >= 30


def test_every_category_is_represented() -> None:
    assert {c.category for c in SEED_COMPLAINTS} == set(Category)


def test_a_realistic_mix_of_priorities_and_statuses() -> None:
    assert {c.priority for c in SEED_COMPLAINTS} == set(Priority)
    assert {c.status for c in SEED_COMPLAINTS} == set(Status)


def test_keys_are_unique() -> None:
    keys = [c.key for c in SEED_COMPLAINTS]
    assert [k for k, n in Counter(keys).items() if n > 1] == []


def test_rows_respect_the_database_limits() -> None:
    # The same limits the CHECK constraints enforce, caught here before a database is involved.
    for row in build_rows(now=FIXED_NOW):
        assert 10 <= len(row["text"]) <= 2000
        assert 3 <= len(row["location"]) <= 200
        assert len(row["ai_summary"]) <= 140


def test_ids_are_deterministic_so_reseeding_is_a_no_op() -> None:
    first = [row["id"] for row in build_rows(now=FIXED_NOW)]
    second = [row["id"] for row in build_rows(now=FIXED_NOW)]

    assert first == second
    assert len(set(first)) == len(first)
    assert first[0] == seed_id(SEED_COMPLAINTS[0].key)


def test_seed_rows_do_not_claim_to_be_llm_output() -> None:
    assert {row["triaged_by"] for row in build_rows(now=FIXED_NOW)} == {"rules"}
