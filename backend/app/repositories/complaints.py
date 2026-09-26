"""Persistence for complaints. All SQL about complaints lives in this module."""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.repositories.models import Complaint


def insert_missing(session: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert rows whose id is not already present; return how many were actually inserted.

    ON CONFLICT DO NOTHING makes this safe to run repeatedly: existing rows are left exactly as
    they are, including any status an operator has changed since.
    """
    if not rows:
        return 0
    statement = (
        insert(Complaint)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(Complaint.id)
    )
    return len(session.execute(statement).all())
