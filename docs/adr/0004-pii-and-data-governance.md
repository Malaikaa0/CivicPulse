# ADR-0004: PII and data governance

- **Status:** Proposed
- **Owner:** M1 (backend / AI)

## Context
Complaints can contain names, addresses and phone numbers. Hosted free-tier LLMs may retain
or train on inputs (Google states this for the Gemini free tier). Sending raw complaints to a
third party is a data-governance decision, not an implementation detail.

## Decision
TBD. Options to decide between:
1. Redact contact details and names before sending.
2. Send only the complaint body, never `reporter_contact`.
3. Accept the exposure and document it.

Provider under consideration: Groq (primary), Ollama as the fully offline path where no data
leaves the machine.

## Consequences
TBD - to be written in week 4, after the decision has been lived with.
