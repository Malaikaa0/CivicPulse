"""Guards against the application's enums and the database schema drifting apart."""

import importlib.util
from pathlib import Path

from app.domain import TriagedBy
from app.repositories.models import TRIAGED_BY_CHECK

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load_migration(filename: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("migration", VERSIONS / filename)
    assert spec is not None and spec.loader is not None  # noqa: S101
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_constraint_matches_the_latest_migration() -> None:
    # If someone edits TriagedBy without writing a migration, this fails.
    latest = _load_migration("0002_allow_gemini_triaged_by.py")

    assert TRIAGED_BY_CHECK == latest.NEW


def test_spec_values_are_still_allowed() -> None:
    allowed = {m.value for m in TriagedBy}

    assert {"llm:groq", "llm:ollama", "rules", "rules:fallback"} <= allowed
    assert "llm:gemini" in allowed


def test_migrations_form_a_single_linear_chain() -> None:
    revisions = {}
    for path in sorted(VERSIONS.glob("0*.py")):
        module = _load_migration(path.name)
        revisions[module.revision] = module.down_revision

    assert revisions == {"0001": None, "0002": "0001"}
