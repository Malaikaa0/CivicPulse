# AI Usage

Course policy (spec §5.5): honest attribution carries no penalty; presenting AI-generated work as
one's own original work does. This is written specifically rather than generally, because a vague
disclosure would defeat the point of the policy.

## Tools

- **Claude Code** (Anthropic's CLI coding agent), running **Claude Sonnet 5**, used interactively
  throughout M1's work (backend, data, cache, AI layer).
- **Four background sub-agents**, also Claude Sonnet 5, each in its own isolated `git worktree`,
  run in parallel on 2026-09-27 to build the complaint HTTP routes, the stats cache and rate
  limiter, the content-hash triage cache, and structured logging/metrics/graceful shutdown
  simultaneously. Their output was then independently re-verified and integrated by the same
  Claude Code session (see "Verification" below) — the parallel run was not trusted on its own
  report.

## M1 (backend / AI / data / cache) — written by Claude Code

**Specifically and without softening: essentially all code, tests, migrations, Dockerfile, ADRs
and documentation under M1's ownership were written by Claude Code, turn by turn, in response to
instructions from the student.** This includes: the FastAPI app skeleton and layering; `/health`
and `/ready`; the Alembic migrations and `complaints` schema; the idempotent seed data (32
Urdu-influenced complaints); the status state machine; the `TriageProvider` interface and its
`rules`, `simulated` and `llm` (Gemini) implementations; the PII redaction module; the triage
retry/fallback service; the complaint repository, service and all HTTP routes; the Redis-backed
stats cache, rate limiter, content-hash triage cache and outcome log; structured JSON logging,
Prometheus metrics, and graceful-shutdown handling; and the accompanying test suite (655 tests
against real PostgreSQL and Redis at commit time).

**What the student did:**
- Set the team split (M1/M2), created and administered the GitHub repository, added the
  collaborator, and set branch protection on `main`.
- Directed scope and priority at each step in conversation — what to build next, when to proceed,
  when to stop and ask the partner first (branch protection, merging, PR review) rather than
  proceed autonomously.
- Made the decisions that needed a human: approved building the AI layer against Google Gemini
  (having obtained a real API key from AI Studio) rather than Groq; approved the redaction and
  data-governance approach recorded in ADR-0004; approved parallelising the remaining backend
  work across sub-agents when time was short.
- Supplied the real Gemini API key (used only for the one documented live smoke test in PR #27,
  and for local manual checks; never used in the automated test suite, which runs on
  `TRIAGE_PROVIDER=simulated`).
- Reviewed and approved feature summaries and verification results as they were reported in
  conversation before each PR was opened. Did not hand-edit the generated code.

**What was checked and changed afterwards, and why:**
- Every change was run through `ruff`, `ruff format`, strict `mypy`, and the test suite —
  including against real PostgreSQL 16 and Redis 7 containers, not only mocks — before being
  committed, by the same AI session that wrote it.
- Bugs found through that verification were fixed in the same pass: a wrong default Gemini model
  that had been silently retired (found only by making a real API call, not by trusting the
  provider's model-list endpoint — see `docs/ENGINEERING-NOTES.md` question 8); a missing
  `alembic/` directory in the Docker image that would have prevented migrations from running in a
  container; a `.dockerignore` pattern that let host bytecode leak into the image; a Windows
  line-ending/BOM issue introduced while merging branches that crashed the linter; four
  observability tests whose fake route collided with the real API once merged; and roughly 30
  test fixtures left stale by the same merge, all found by re-running the full suite on the
  combined branches rather than trusting each branch's tests in isolation.
- Where the four parallel sub-agents' self-reported results could be independently verified (test
  counts, coverage, absence of secrets or AI mentions in commits), they were — by checking out
  each branch separately and re-running every check from a clean state, not by reading their
  reports at face value.

## M2 (frontend / DevOps / Kubernetes / CI) — pending

No code exists yet under M2's ownership at the time of writing. This section will be completed
honestly by M2 once that work starts, in the same specific style as above, and must not be filled
in on his behalf by anyone else.

## Why this is written this way

The course policy states plainly that the viva "does not care who wrote a line, only whether you
can defend it." Given how much of this repository was written by an AI assistant, that defence is
the real work remaining for both students: being able to explain, and if asked, modify, any part
of this system live — the ADRs, the retry/fallback logic, the migration's constraints, the layering
rules — regardless of who typed it first.
