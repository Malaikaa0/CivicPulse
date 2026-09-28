# AI Usage

Course policy (spec §5.5): honest attribution carries no penalty; presenting AI-generated work as
one's own original work does. This is written specifically rather than generally, because a vague
disclosure would defeat the point of the policy.

## Tools

- **Claude Code** (Anthropic's CLI coding agent), running **Claude Sonnet 5** and later
  **Claude Opus 5.5**, used interactively throughout both M1's work (backend, data, cache, AI
  layer) and M2's (frontend, Compose, Kubernetes, CI/CD).
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

## M2 (frontend / DevOps / Kubernetes / CI) — written by Claude Code

Drafted from the session record at M2's direction and reviewed by him before submission.

**Same disclosure, same plainness: essentially all code, configuration, tests, evidence runs and
documentation under M2's ownership were written by Claude Code (Claude Sonnet 5, later Claude
Opus 5.5), turn by turn, in response to instructions from the student.** Commits in this area
are authored under M2's name because they were made from his machine and git identity at his
direction; the commit balance in `git shortlog` therefore reflects who directed the work, not
who typed it. This includes:

- The frontend: Vite + React + TypeScript scaffold, `nginx.conf` with the `/api` proxy
  (ADR-0002), the multi-stage Dockerfile, the Submit / Dashboard / Stats views (built by three
  parallel sub-agents in separate worktrees, then integrated and re-verified in the main session),
  the shared API client, the error boundary, the OpenAPI contract check, and all component tests.
- `compose.yaml` and `compose.prod.yaml`, the network segmentation and volumes.
- The Kubernetes manifests (base, dev/prod overlays, HPA, VPA, PDB, Ingress, migrate Job), the
  local k3d clusters, and every evidence run in `k8s/evidence/`: the HPA load test and chart,
  the VPA record-test-update loop, the zero-downtime rollout and the rollback.
- `ci.yml`, `cd.yml` and `release.yml`, and every fix to them after real runs failed.
- README, RUNBOOK, `TRIAGE.md`, `DEMO.md`, the LICENSE, the ADR-0002/0003 consequences, the
  README screenshots (captured with a headless browser against the running app), and fixes to
  `scripts/check_submission.py`.

**What the student did:**
- Directed scope and priority throughout, and made the calls that needed a human: k3d over kind,
  keeping Docker's data on C: after the D: relocation failed, dropping Ollama, and when to merge.
- Did the merge-conflict exercise's own edit in the GitHub UI (PR #53).
- Rotated the leaked Gemini key, created the replacement in AI Studio and set the three GitHub
  Secrets himself (the key never passed through the AI session).
- Configured `main`'s ruleset required checks and auto-merge in the GitHub UI, and took the
  red/green CI-gate and ruleset screenshots.
- Records the demo video with his partner (not yet recorded at the time of writing; script in
  `docs/DEMO.md`).
- Did not hand-edit the generated code.

**What was checked and changed afterwards, and why:**
- Two independent audit sub-agents re-checked the finished work against the spec. They found
  real problems the main session had missed or overstated: `cd.yml`'s deploy job had never
  actually succeeded despite being described as verified (it failed four times before a secret-
  ordering race was found and fixed in PR #81); ADR consequences and several engineering-notes
  answers were stale; a merge-conflict explanation was missing; CI's `tsc --noEmit` step checked
  no files at all; `release.yml` published without a `needs:` gate.
- The first diagnosis of the deploy failure (an unnecessary `rollout restart`) was wrong. It was
  corrected only after adding a diagnostics step and reading the real pod and job state.
- 18 commits were created with a `Co-Authored-By` trailer naming the AI, against the team's
  instruction to keep attribution in this file rather than in commit metadata.
- Trivy's first real run failed on genuine HIGH/CRITICAL CVEs in both base images; fixed by
  patching the base image packages rather than suppressing the findings.

## Why this is written this way

The course policy states plainly that the viva "does not care who wrote a line, only whether you
can defend it." Given how much of this repository was written by an AI assistant, that defence is
the real work remaining for both students: being able to explain, and if asked, modify, any part
of this system live — the ADRs, the retry/fallback logic, the migration's constraints, the layering
rules — regardless of who typed it first.
