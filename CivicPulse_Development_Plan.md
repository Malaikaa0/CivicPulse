# CivicPulse — Development Plan
CS4032 Software Construction and Design · Assignment 1 · 150 marks · 2 members · 4 weeks

---

## 0. How to use this document

This plan converts the spec into an executable sequence: who does what, in what order, on
which branch, closing which issue, producing which commits. Follow it literally in week 1–2;
after that you'll have enough context to deviate intelligently. Every task below maps to a rubric
line (letter+number in brackets, e.g. `[C1]` = Backend rubric item 1) so nothing is built that
isn't graded, and nothing graded is missed.

**Non-negotiable structural decisions, made now so you stop re-litigating them:**
- Backend: **FastAPI + Pydantic v2** (recommended path — OpenAPI schema doubles as your
  frontend contract and your LLM-output validation model).
- Two members: **M1 = Backend/AI/Data/Cache owner**, **M2 = Frontend/DevOps/K8s owner**.
  This is not a 50/50 split of *effort* (backend is heavier) — it's a split of *ownership* so viva
  answers are clean. You will still each touch the other's code via PR review (required for the
  ≥5-PR rubric line anyway) — that's what makes both of you viva-safe on the whole repo.
- Branching model: `main` (protected) ← `dev` ← feature branches `feat/<area>-<short-desc>`.
  Never commit to `main` directly. Never commit to `dev` directly either, in practice — PR
  everything, it's cheap and it's what's graded `[A2]`.
- Every feature branch closes exactly one GitHub Issue. Every PR references that issue
  (`Closes #N`). This gets you `[A3]` for free if you don't skip it.

---

## 1. Repository & project setup (Day 1, both members, ~2 hours)

1. M1 creates the GitHub repo `civicpulse`, adds M2 as collaborator.
2. Set branch protection on `main`: require PR, require 1 approval, require status checks
   (you'll wire `ci.yml` in week 2, but set the rule now so it's provably in place from day 1) `[A1]`.
3. Create `dev` branch from `main`.
4. Scaffold the full directory tree from §5.7 of the spec **empty but committed** (with
   `.gitkeep` files) — this is commit #1 on `dev`, tagged `chore: scaffold repository structure`.
5. Create the GitHub Issues board (or Projects kanban) with one column per rubric section
   (A–J). Create all Week-1 issues now (list below). Issue numbering here assumes you create
   them in the order listed — adjust references if GitHub assigns different numbers.
6. Agree commit convention: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:` — required
   for `[A4]`.
7. `.gitignore` (node_modules, __pycache__, .venv, .env, dist, *.db) and `.env.example`
   committed together — commit `chore: add gitignore and env example`.

**Deliverable of Day 1:** empty-but-structured repo, protected `main`, issue board seeded,
both members can `git clone` and see the shape of the whole system.

---

## 2. Architecture decisions, locked before code (Day 1–2, both members, ~2 hours)

Write these as **stub ADRs immediately** (even one paragraph) — you will flesh them out in
week 4, but deciding now prevents rework:

| ADR | Decision to make now | Owner |
|---|---|---|
| 0001-provider-interface | `TriageProvider` Protocol shape, 4 implementations, selection via `TRIAGE_PROVIDER` env var | M1 |
| 0002-frontend-runtime-config | `/config.js` generated at container start **vs** nginx `/api` proxy. **Recommendation: nginx proxy** — simpler, one less moving part, frontend never needs an absolute backend URL at all | M2 |
| 0003-deploy-by-sha | Deploy by commit SHA tag (digest for bonus) | M2 |
| 0004-pii-data-governance | Which provider (Groq vs Gemini) and what, if anything, gets redacted before the complaint body is sent | M1 |

Create `docs/adr/0001-....md` … `0004-....md` as skeletons now (Context / Decision / TBD-
Consequences). Commit: `docs: add ADR skeletons`.

**Provider choice recommendation:** Groq as primary (`LLMTriage`), Ollama as the offline
fallback path implemented for marks but not your daily-driver during development (slower,
worse classification — save it for the "measure the trade-off" writeup). Gemini as backup if
Groq rate limits become annoying mid-sprint.

---

## 3. Two-week sprint plan (calendar view)

Assuming a 4-week window, **compress the real engineering into weeks 1–3** and reserve
week 4 for K8s hardening, docs, video, and the viva-prep buffer that always gets skipped and
shouldn't be.

| Week | M1 focus (Backend/AI/Data/Cache) | M2 focus (Frontend/DevOps/K8s) |
|---|---|---|
| 1 | Backend skeleton, DB schema + migrations, state machine, `TriageProvider` interface + `RuleBasedTriage` + `SimulatedTriage` | Frontend scaffold, Compose networks/volumes, Dockerfiles (both images), backend contract stub (routes returning fixtures) so frontend isn't blocked |
| 2 | `LLMTriage` (Groq) with timeout/retry/fallback/cache, rate limiter, `/api/stats` caching, structured logging, SIGTERM handling | Submit + Dashboard + Stats views against real backend, typed API client from OpenAPI, error boundary, component tests |
| 3 | Backend test suite to ≥65% coverage, seed script, `/api/meta/providers`, prompt-injection test, `/metrics` | K8s manifests (Kustomize base+overlays), probes, HPA, VPA, CI workflows (`ci.yml`) |
| 4 | ENGINEERING-NOTES.md, ADRs finalized, load testing + HPA/VPA evidence (joint) | `cd.yml`, `release.yml`, rollback demo, README, RUNBOOK, demo video (joint) |

Both members: daily 15-min sync (async on Slack/Discord is fine), because the backend
contract (§2.2 of spec) is the seam between your workstreams — if M1 changes a response
shape, M2's frontend breaks immediately. **Freeze the OpenAPI contract shape by end of Week
1** even if implementations behind it are still fixtures.

---

## 4. Issue → Branch → PR → Commit breakdown by rubric section

Each row below is one Issue. Branch name and suggested commits are given so you're not
inventing structure mid-sprint. PR titles should match the issue title. Merge target is always
`dev`; `dev` → `main` happens via periodic "release" PRs (at minimum: end of week 2, end of
week 3, end of week 4 — gives you the ≥5 PR minimum trivially since each rubric section alone
produces several).

### A — Collaboration & Version Control (owned jointly, ongoing)
- **Issue #1** `Set up branch protection and CI gate` — M1 — no branch (repo admin action) +
  one PR later wiring `ci.yml` as required check.
- **Issue #2** `Demonstrate one real merge conflict` — schedule this deliberately in week 2:
  both members touch `backend/app/models/complaint.py` (e.g. M1 adds a column, M2's stub
  route references old field name) on two branches created from the same base commit,
  merge both into `dev`, resolve, write the 2–4 sentence note in the PR description. This is
  graded — don't let it happen accidentally and go undocumented; *stage* it.
- Track commit % continuously with `git shortlog -sn`; if either partner drops under 35%,
  rebalance issue assignment immediately, don't wait for week 4.

### B — Frontend (M2 owns, M1 reviews)
- **Issue #10** `feat/frontend-scaffold`: Vite+React+TS init, nginx multi-stage Dockerfile,
  `.dockerignore`. Commits: `chore: vite react ts scaffold`, `build: nginx multi-stage
  dockerfile`. PR → `dev`.
- **Issue #11** `feat/frontend-runtime-config`: implement the ADR-0002 decision (nginx
  `/api` proxy). Commit: `feat: proxy /api through nginx, no baked-in backend URL`.
- **Issue #12** `feat/frontend-submit-view`: form, client-side validation mirroring server
  rules, loading state, renders category/priority/summary/provider `[B1]`. Commits per
  sub-piece: `feat: complaint submit form`, `feat: client validation`, `feat: honest loading
  state`, `feat: render triage result`.
- **Issue #13** `feat/frontend-dashboard-view`: pagination, filters, status transition buttons,
  surface server's 409 message verbatim (not a generic error) `[B2]`.
- **Issue #14** `feat/frontend-stats-view`: aggregate display + `X-Cache` badge `[B3]`.
- **Issue #15** `feat/frontend-api-client`: generate/check typed client against backend
  OpenAPI schema; add error boundary `[B4 supporting, required-engineering]`.
- **Issue #16** `test/frontend-component-tests`: ≥5 Vitest component tests `[B5]`.

### C — Backend (M1 owns, M2 reviews)
- **Issue #20** `feat/backend-scaffold`: FastAPI app factory, four-layer package structure
  (`routes/services/repositories/providers`), `python:3.12-slim` multi-stage Dockerfile,
  non-root user, `HEALTHCHECK` `[G1 partial]`.
- **Issue #21** `feat/backend-health-ready`: `/health` (no DB touch) and `/ready` (checks
  Postgres+Redis, 503 naming failed dep) `[C4]`.
- **Issue #22** `feat/backend-complaint-routes`: all ten endpoints per contract table, wired
  to service-layer stubs first so frontend can integrate before AI layer is real `[C1]`.
- **Issue #23** `feat/backend-state-machine`: explicit transition table for status, 409 on
  invalid transition naming the attempted transition `[C3]`.
- **Issue #24** `feat/backend-structured-logging`: JSON to stdout, `X-Request-ID`
  propagation/generation, one WARNING per triage fallback `[C5]`.
- **Issue #25** `feat/backend-graceful-shutdown`: SIGTERM handler draining in-flight
  requests, closing pool `[C6]`.
- **Issue #26** `test/backend-suite`: ≥14 unit+integration tests, deterministic
  (`TRIAGE_PROVIDER=simulated`), coverage ≥65% `[C7]`.

### D — Data layer (M1 owns)
- **Issue #30** `feat/data-alembic-init`: Alembic wired, first migration creating the
  `complaints` table per schema table in spec (enums, nullable ai_summary,
  triage_latency_ms, timestamptz) `[D1, D2]`.
- **Issue #31** `feat/data-indexes`: indexes on `(status, priority)` and `created_at`; write the
  one-sentence justification per index directly as a migration comment and in
  ENGINEERING-NOTES `[D3]`.
- **Issue #32** `feat/data-seed-script`: idempotent seed, ≥30 Urdu-influenced-English
  complaints across all categories, upsert-or-skip logic so re-running doesn't duplicate `[D4]`.

### E — Cache layer (M1 owns)
- **Issue #40** `feat/cache-stats-readthrough`: `/api/stats` cached 30s TTL, `X-Cache`
  header, invalidate-on-write (not just TTL expiry) `[E1, E2]`.
- **Issue #41** `feat/cache-rate-limiter`: Redis-backed fixed-window or token-bucket limiter
  keyed by client IP on `POST /api/complaints`, 429 + `Retry-After` `[E3]`.
- **Issue #42** `chore/cache-aof-volume`: enable AOF on named volume, write the "why does
  a cache need persistence" justification into ENGINEERING-NOTES `[E4]`.

### F — AI layer (M1 owns — this is the heaviest single section, 25 marks; start it week 1, don't leave it to week 3)
- **Issue #50** `feat/ai-provider-interface`: `TriageProvider` Protocol +
  `RuleBasedTriage` + `SimulatedTriage` first (both are dependency-free, unblock CI
  determinism immediately) `[F1 partial]`.
- **Issue #51** `feat/ai-llm-triage-groq`: `LLMTriage` via Groq (OpenAI-compatible SDK,
  JSON mode), Pydantic validation of the response regardless of JSON-mode success `[F1
  remainder, F2]`.
- **Issue #52** `feat/ai-timeout-retry-fallback`: hard 10s timeout, single jittered retry on
  {timeout,429,5xx} only, fallback to `RuleBasedTriage` on exhaustion, `triaged_by` field
  set correctly at each stage `[F3]`.
- **Issue #53** `feat/ai-content-hash-cache`: Redis cache keyed by hash of complaint text,
  24h TTL; log/report measured hit rate `[F4]`.
- **Issue #54** `feat/ai-prompt-injection-guard`: delimit untrusted text clearly in the
  prompt, constrain output to enum, write the injection-attempt test asserting the schema
  still wins `[F5]`.
- **Issue #55** `feat/ai-observability`: `triage_latency_ms` recorded per call, surfaced via
  `/api/meta/providers` (last 20 outcomes: provider, latency, fallback y/n) `[F6]`.
- **Issue #56** `feat/ai-ollama-provider` (stretch if time-boxed tight — do after #50-55 are
  solid): fourth implementation, container in Compose, no key/network `[F1 full]`.
- **The one test that matters most:** write it inside #52 — a provider double that always
  raises → `POST /api/complaints` still returns 201, `triaged_by == "rules:fallback"`.

### G — Docker & Compose (M2 owns, M1 supplies backend Dockerfile pieces)
- **Issue #60** `feat/compose-networks-volumes`: `edge` + `internal` (internal:true)
  networks wired to correct services; `pgdata`, `redisdata`, `ollama_models` named
  volumes; dev bind mount present in `compose.yaml` only `[G3, G4]`.
- **Issue #61** `feat/compose-healthchecks`: healthcheck + `depends_on: condition:
  service_healthy` on every service `[G5]`.
- **Issue #62** `feat/compose-prod-file`: `compose.prod.yaml` — `image:` with
  `${IMAGE_TAG}`, no `build:`, no published DB/cache ports `[G6]`.
- **Issue #63** `chore/dockerignore-and-sizes`: `.dockerignore` per build context; capture
  before/after context size numbers into README or notes `[G2]`.
- Demonstration to capture on video now (don't wait for week 4): `docker compose exec
  frontend ping database` failing — screenshot/clip it the moment networks land.

### H — Kubernetes (M2 owns, week 3)
- **Issue #70** `feat/k8s-base-manifests`: Namespace, backend+frontend Deployments (≥2
  replicas), `postgres` StatefulSet+PVC, `redis` Deployment+PVC, 4 ClusterIP Services,
  Ingress (`/` → frontend, `/api` → backend) `[H1]`.
- **Issue #71** `feat/k8s-config-secrets`: ConfigMap for non-secret config, Secret for DB
  password + LLM key, placeholders only in committed manifests `[H2]`.
- **Issue #72** `feat/k8s-probes`: `startupProbe` (failureThreshold 30, periodSeconds 2),
  `livenessProbe` on `/health` (no DB), `readinessProbe` on `/ready` (DB-dependent) `[H3]`.
- **Issue #73** `feat/k8s-resources`: `requests`/`limits` on every container — this is the
  prerequisite for HPA working at all, do it before #74 `[H4]`.
- **Issue #74** `feat/k8s-hpa`: HPA v2, CPU target 60%, `scaleDown.stabilizationWindowSeconds:
  300`, `scaleUp.stabilizationWindowSeconds: 0`; capture `kubectl get hpa -w` + load-vs-
  replicas chart from a real k6/hey run `[H5]`.
- **Issue #75** `feat/k8s-vpa`: VPA in `updateMode: Off` on backend; run the record → load-
  test → `kubectl describe vpa` → update-requests → re-test loop; write the HPA/VPA
  conflict explanation `[H6]`.
- **Issue #76** `feat/k8s-kustomize-overlays`: `base/` + `overlays/dev` + `overlays/prod`.
- **Issue #77** `feat/k8s-pdb`: `PodDisruptionBudget minAvailable: 1` on backend.
- **Issue #78** `feat/k8s-rolling-update`: `maxSurge:1, maxUnavailable:0`,
  `terminationGracePeriodSeconds`, `preStop` sleep; capture zero-downtime rollout under
  load generator during `kubectl set image` (bonus-eligible if truly zero failed requests).

### I — CI/CD (M2 owns primary authorship, M1 co-reviews the AI-determinism parts)
- **Issue #80** `feat/ci-lint-typecheck`: ruff+mypy (backend), eslint+tsc --noEmit
  (frontend), as separate CI jobs `[I1 partial]`.
- **Issue #81** `feat/ci-tests`: `test-backend` (pytest, coverage≥65%,
  `TRIAGE_PROVIDER=simulated`), `test-frontend` (Vitest) jobs, marked as required checks
  on `main` `[I1 remainder]`.
- **Issue #82** `feat/ci-build-scan-manifests`: `build` job (no push on PR), Trivy scan
  failing on HIGH/CRITICAL, `kustomize build overlays/prod | kubeconform` `[I3]`.
- **Issue #83** `feat/ci-integration-job`: `docker compose up -d` → wait `/ready` → POST
  complaint → GET it back → assert category → assert `X-Cache` MISS→HIT → `compose
  down -v` `[I2]`.
- **Issue #84** `feat/cd-build-push`: on push to `main`: full suite → build both images →
  push to GHCR tagged `${{ github.sha }}` and `latest` → SBOM via Syft → capture digest
  output `[I4]`.
- **Issue #85** `feat/cd-deploy-k8s`: `needs: build-push` → spin ephemeral kind/k3d in
  runner → apply `overlays/prod` with SHA tag → `kubectl rollout status` → smoke test
  Ingress → `kubectl get hpa` `[I5]`.
- **Issue #86** `chore/ci-least-privilege`: explicit `permissions:` block on every workflow,
  pin actions to `@v4` minimum `[I6]`.
- **Issue #87** `feat/release-workflow`: `release.yml` on `v*` tag — semver tags, release
  notes.
- **Issue #88** `docs/evidence-red-green-pipeline`: deliberately break a test in a PR,
  screenshot the red check + blocked merge, fix in same PR, screenshot green `[I7]`.

### J — Documentation, portfolio, reflection (joint, week 4, but start the skeletons week 1)
- **Issue #90** `docs/readme`: problem statement, badges, Mermaid architecture diagram
  (you already have the source diagram from the spec — redraw it as Mermaid), one-
  command quickstart (test it from a *clean* clone, not your dev machine), API table,
  screenshots `[J1]`.
- **Issue #91** `docs/adrs-finalize`: flesh out all 4 ADR skeletons from §2 with real
  Consequences sections written after the decision was lived with, not before `[J2]`.
- **Issue #92** `docs/runbook`: deploy, rollback, read logs, triage-failing playbook `[J3]`.
- **Issue #93** `docs/engineering-notes`: answer all 8 questions in spec §5.2 with file-and-
  line references — do this incrementally as you build each piece, not from memory in
  week 4 `[J5]`.
- **Issue #94** `docs/ai-usage`: honest tool-attribution log, maintained continuously `[required, non-graded-directly but plagiarism-gated]`.
- **Issue #95** `chore/demo-video`: ≤5 min, both partners speaking, covering: clean clone→
  running, AI triage, fallback, network isolation failing, HPA scaling, rollback `[J4]`.

---

## 5. Suggested commit granularity (so `git shortlog` looks like real engineering, not 3 giant commits)

Rule of thumb: one commit per coherent unit of work you could explain standalone at viva.
Target 35+ commits total per person is the rubric floor `[A4]` — at ~90 issues × 2-4 commits
each across two people you will clear this without trying, *if* you commit as you go rather than
squash-merging giant PRs. **Do not squash-merge.** Use regular merge commits or rebase-and-
merge so individual commits survive into `dev`/`main` history for `git shortlog -sn` to count
correctly.

---

## 6. Determinism & CI-safety checklist (read before writing any AI-layer test)

- CI always runs with `TRIAGE_PROVIDER=simulated`. Never let a real Groq call into CI.
- Test the fallback path with a provider double that unconditionally raises — not with a
  flaky real network call.
- Test the malformed-JSON path with a provider double returning garbage.
- If you ever write `time.sleep()` or "just re-run it" in a test, stop — that's the signal the
  design under test is wrong, per the spec's own words.

---

## 7. Dependency graph — what blocks what (avoid idle time)

```
Repo scaffold (Day 1)
   │
   ├─► Backend routes (fixture responses) ──► Frontend views can start immediately
   │         │
   │         ├─► DB schema/migrations ──► Seed script
   │         │
   │         ├─► TriageProvider interface ──► RuleBased + Simulated (unblocks CI)
   │         │                                        │
   │         │                                        └─► LLMTriage (Groq) ──► timeout/retry/fallback ──► content-hash cache
   │         │
   │         └─► State machine ──► 409 handling ──► Dashboard transition UI (frontend)
   │
   ├─► Dockerfiles (both) ──► Compose networks/volumes ──► Compose healthchecks ──► K8s base manifests
   │                                                                                      │
   │                                                                                      ├─► Probes ──► Resources ──► HPA ──► VPA
   │                                                                                      └─► Kustomize overlays
   │
   └─► CI lint/test jobs ──► CI integration job ──► CD build-push ──► CD deploy-k8s ──► Release workflow
```

**Practical implication:** M2 should not wait for M1's real AI implementation to start frontend
or Compose/K8s work — stub the `/api/complaints` response shape in week 1 (agreed contract,
§2 above) and build against that. M1's real Groq integration slots in underneath without the
frontend needing to change.

---

## 8. Risk register — things that eat a disproportionate amount of time

| Risk | Mitigation |
|---|---|
| Groq free-tier rate limit exhausted by dev testing | Cache aggressively during dev too; keep `SimulatedTriage` as your default local provider, switch to `llm` only when testing that path specifically |
| HPA sits at `<unknown>/60%` forever | This is *always* missing `resources.requests.cpu` — check `[H4]` is done before debugging `[H5]` |
| Frontend baked-in API URL breaks the "one image runs anywhere" requirement | Decided in ADR-0002 week 1; test by running the same built image against two different backend URLs before Docker week ends |
| Merge conflict for `[A5]` never happens naturally, deadline arrives with nothing to show | Schedule it deliberately in week 2 per §4-A above, don't leave it to chance |
| Coverage stalls below 65% because tests were an afterthought | Write the fallback + injection tests *as you build* each AI-layer feature (§4-F), not retroactively |
| K8s manifests never actually get demoed working, only "should work" | Run `k3d`/`kind` locally from week 3 onward continuously, not once at the end |
| README quickstart works on your machine, fails on a clean clone (−5) | Have the *other* partner run the quickstart from a fresh clone before submission, not the author |

---

## 9. Final pre-submission checklist (map directly to §5.3 automatic deductions)

- [ ] `git log --all -- .env` and search full history for keys/tokens — zero hits
- [ ] Every base image tag pinned (`postgres:16`, `redis:7`, `node:22-alpine`, no bare `latest`)
- [ ] `docker compose exec frontend ping database` fails (captured in video)
- [ ] `compose.prod.yaml` has no published DB/cache port, no `build:` key
- [ ] Every publish/deploy CI job has `needs:`
- [ ] Nowhere in K8s manifests or CD is `:latest` deployed (only ever pushed)
- [ ] Postgres is a StatefulSet with PVC, not a bare Deployment
- [ ] No commits directly on `main` (`git log main --not dev` should be empty-ish, only merge
      commits)
- [ ] README quickstart tested from a genuinely clean clone by the non-author partner
- [ ] `python scripts/check_submission.py` run and clean

---

## 10. Viva prep (individual, 10 min each, multiplies the team mark)

Both partners must be able to explain **the whole repo**, not just their half — this is why PR
review is mandatory, not decorative: actually read your partner's diffs, don't rubber-stamp.
In week 4, do a 1-hour cross-brief: M1 walks M2 through the AI layer's retry/fallback/cache
logic and the state machine; M2 walks M1 through the network segmentation, HPA/VPA
interaction, and the CD pipeline gating. Each should be able to modify the other's code live if
asked — that's the 0.75-vs-1.0 line in the viva rubric.
