# ADR-0001: Triage provider interface

- **Status:** Proposed - pending review by M2
- **Owner:** M1 (backend / AI)

## Context
Complaint triage must be replaceable: a keyword rule today, a hosted LLM tomorrow, a
fine-tuned classifier later. The rest of the system must not care which one is active, and
must keep working when the LLM is slow, rate-limited or wrong.

## Decision
A `TriageProvider` Protocol (`name`, `triage(text, location) -> TriageResult`) with four
implementations selected by the `TRIAGE_PROVIDER` environment variable: `llm`, `ollama`,
`rules` and `simulated`.

- `RuleBasedTriage` is deterministic and never fails. It is the fallback.
- `SimulatedTriage` is deterministic, seeded and network-free, with scripted failures. CI runs
  against it, so the test suite is green on every run.
- `LLMTriage` calls a hosted model through an **OpenAI-compatible endpoint**, so one class
  serves both Google Gemini and Groq; `LLM_VENDOR` picks which. Gemini is the vendor in use
  (the assignment allows any free provider if documented), recorded as `triaged_by =
  llm:gemini`, which needed migration `0002`.
- Everything a provider can get wrong is expressed as a small error taxonomy
  (`providers/triage/errors.py`) with a `retryable` flag: timeout, 429 and 5xx are retryable;
  a 400, a bad key or an invalid reply are not. Providers translate their SDK's failures into
  it, so `services/triage.py` (one jittered retry, then fall back to rules) never depends on a
  particular SDK.
- Model output is never trusted: it is validated against a Pydantic `TriageResult` (enum
  category and priority, summary up to 140 characters, confidence 0 to 1) and rejected if it
  does not fit.

## Consequences
- **Good:** swapping the reader touches one class. A provider outage costs a rule-based
  answer, not a 500. Tests need no network. The same class can point at another OpenAI-
  compatible vendor by configuration.
- **Learned the hard way:** a live call found what the fake-client tests could not. Google
  had retired `gemini-2.5-flash-lite` for new keys, and the model *list* endpoint still showed
  it. The system degraded to `rules:fallback` on every request and returned correct
  categories with no errors, which is exactly what the design is for, but it would have done
  so silently. Hence the model is pinned to a named model (never a `-latest` alias, which can
  change behaviour under us) and is configurable through `GEMINI_MODEL`. Fallbacks are
  visible through `/api/meta/providers` and the `triaged_by` column so a permanently failing
  provider is noticed.
- **Cost:** the free tier's limits and terms are outside our control (see ADR-0004 for what
  we send and why).
- **Revisit when:** a second hosted vendor with a non-OpenAI-compatible API is needed.
