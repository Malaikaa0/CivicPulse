"""Idempotent seed command:  python -m app.seed

Needs only DATABASE_URL. Safe to run any number of times: each seed complaint has a
deterministic id, so a second run inserts nothing.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.complaints import insert_missing
from app.repositories.database import create_db_engine
from app.seed_data import SEED_COMPLAINTS, SeedComplaint

# Fixed namespace: changing it would change every seed id and duplicate the seeded rows.
SEED_NAMESPACE = uuid.UUID("6f1d0b0e-3c1a-4f57-9d0a-2b9a5c6e7f11")


def seed_id(key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, key)


def build_rows(
    complaints: tuple[SeedComplaint, ...] = SEED_COMPLAINTS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    rows = []
    for index, item in enumerate(complaints):
        # Spread creation times over the past weeks so the dashboard looks lived in.
        created_at = now - timedelta(days=item.days_ago, hours=(index * 7) % 24)
        rows.append(
            {
                "id": seed_id(item.key),
                "text": item.text,
                "location": item.location,
                "reporter_contact": item.contact,
                "category": item.category,
                "priority": item.priority,
                "status": item.status,
                "ai_summary": item.summary,
                # Honest provenance: these rows were classified by hand, not by a model.
                "triaged_by": "rules",
                "triage_latency_ms": 1,
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
    return rows


def main() -> None:
    engine = create_db_engine(os.environ["DATABASE_URL"])
    rows = build_rows()
    with Session(engine) as session, session.begin():
        inserted = insert_missing(session, rows)
    print(f"Seed complete: {inserted} inserted, {len(rows) - inserted} already present.")


if __name__ == "__main__":
    main()
