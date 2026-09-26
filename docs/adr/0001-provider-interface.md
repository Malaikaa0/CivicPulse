# ADR-0001: Triage provider interface

- **Status:** Proposed
- **Owner:** M1 (backend / AI)

## Context
Complaint triage must be replaceable: a keyword rule today, a hosted LLM tomorrow, a
fine-tuned classifier later. The rest of the system must not care which one is active, and
must keep working when the LLM is slow, rate-limited or wrong.

## Decision
A `TriageProvider` Protocol (`name`, `triage(text, location) -> TriageResult`) with four
implementations selected by the `TRIAGE_PROVIDER` environment variable: `llm` (Groq),
`ollama`, `rules` and `simulated`. `RuleBasedTriage` is the always-available fallback.
`SimulatedTriage` is deterministic and is what CI runs against.

## Consequences
TBD - to be written in week 4, after the decision has been lived with.
