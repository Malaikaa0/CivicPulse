"""Aggregate queries for the stats endpoint. All SQL about statistics lives in this module."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import Category, Priority, Status
from app.repositories.models import Complaint


@dataclass(frozen=True)
class GroupCount:
    category: Category
    priority: Priority
    status: Status
    count: int


class StatsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def group_counts(self) -> list[GroupCount]:
        """Complaint counts per (category, priority, status). Combinations with no rows are absent.

        One GROUP BY over all three columns, rather than one query per breakdown, so that every
        number in the response comes from a single snapshot: a complaint arriving mid-request
        cannot make the three breakdowns disagree with each other or with the total. The result
        is at most 6 x 3 x 4 = 72 rows however large the table is.

        Indexes: none help. ix_complaints_status_priority lacks category, and
        ix_complaints_created_at does not lead with any grouped column, so this reads the whole
        table and hashes the groups. That is O(rows) on every call, which is the reason the result
        is cached in Redis rather than indexed. Adding an index would speed a query the cache
        already makes rare, at the cost of every INSERT.
        """
        rows = self._session.execute(
            select(
                Complaint.category,
                Complaint.priority,
                Complaint.status,
                func.count(),
            ).group_by(Complaint.category, Complaint.priority, Complaint.status)
        ).all()
        # Unpacked, not row.count: on a Row that name is tuple.count(), not the column.
        return [GroupCount(category, priority, status, n) for category, priority, status, n in rows]
