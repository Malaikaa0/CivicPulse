"""Domain vocabulary shared by every layer: the fixed sets of values from the spec.

These are the single source of truth. The database enums, the Pydantic schemas that validate
LLM output and the API responses are all built from them, so a category the model invents that
is not in this list is rejected rather than stored.
"""

from enum import StrEnum


class Category(StrEnum):
    WATER = "water"
    ELECTRICITY = "electricity"
    SANITATION = "sanitation"
    ROADS = "roads"
    STREETLIGHTS = "streetlights"
    OTHER = "other"


class Priority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class TriagedBy(StrEnum):
    LLM_GROQ = "llm:groq"
    LLM_GEMINI = "llm:gemini"
    LLM_OLLAMA = "llm:ollama"
    RULES = "rules"
    RULES_FALLBACK = "rules:fallback"
