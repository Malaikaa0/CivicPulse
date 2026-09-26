"""The triage contract: what every provider must return, and how it is called.

The rest of the system depends on this module only, never on a concrete provider, so the
reader can change (keyword rules today, an LLM tomorrow) without touching anything else.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Category, Priority


class TriageResult(BaseModel):
    """A validated triage decision.

    LLM output is parsed into this model, so a category outside the enum, an empty or overlong
    summary or an out-of-range confidence is rejected instead of being stored.
    """

    model_config = ConfigDict(frozen=True)

    category: Category
    priority: Priority
    summary: str = Field(min_length=1, max_length=140)
    confidence: float = Field(ge=0.0, le=1.0)


class TriageProvider(Protocol):
    # Recorded in the database as triaged_by, e.g. "rules" or "llm:groq".
    name: str

    def triage(self, text: str, location: str) -> TriageResult: ...
