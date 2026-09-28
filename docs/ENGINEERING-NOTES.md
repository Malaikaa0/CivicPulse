# Engineering Notes

Answers to spec section 5.2. All eight questions are now answered from what is actually built,
tested and run in this repository, with file-and-line references. Questions 2 (CI/CD maturity
ladder), 5 (HPA lag), 6 (VPA/HPA conflict) and 7 (`internal: true` and the hosted LLM) were
originally marked PENDING because the workflows, Kubernetes manifests and `compose.yaml` they
depend on did not exist yet; each was answered once that infrastructure existed and could be
pointed at. The supplementary "design justifications" section at the end is complete too; E4
(Redis AOF persistence) was the last item, answered once the volume could be tested.

---

## 1. Three things that differ between your laptop and a CI runner

**a. The Python interpreter and OS.** A developer's laptop might run any Python 3.x on Windows,
macOS or Linux. Frozen by [`backend/Dockerfile:4`](../backend/Dockerfile#L4) and
[`:17`](../backend/Dockerfile#L17): `FROM python:3.12.10-slim-bookworm` in both the builder and
runtime stages. Every environment that runs the built image gets the same interpreter on the same
Debian Bookworm base, regardless of what is installed on the host. (This repo's own development
happened on Windows with a local Python 3.12 venv; the pin is what makes that irrelevant once the
image is built.)

**b. Every Python package version.** `pip install fastapi` today and in three months can resolve
to different versions. Frozen by [`backend/requirements.txt`](../backend/requirements.txt), which
is a full `pip freeze` (34 packages, exact versions, e.g. `fastapi==0.141.1`), installed by
[`backend/Dockerfile:13-14`](../backend/Dockerfile#L13-L14) (`COPY requirements.txt .` before
`RUN pip install -r requirements.txt`, in that order so the layer only rebuilds when dependencies
actually change).

**c. Whether a real LLM is called.** A developer's `.env` may have a live `GEMINI_API_KEY` and
`TRIAGE_PROVIDER=llm`; a CI runner has no key and must never make a real network call (cost,
rate limits, non-determinism). Frozen by
[`backend/tests/conftest.py:4`](../backend/tests/conftest.py#L4):
`os.environ.setdefault("TRIAGE_PROVIDER", "simulated")`, which every test process picks up unless
something more specific overrides it first. CI sets the same variable explicitly at the job level
too: [`.github/workflows/ci.yml:94`](../.github/workflows/ci.yml#L94) (`TRIAGE_PROVIDER:
simulated` for `test-backend`) and `ci.yml:253` (in the `.env` the `integration` job writes).

## 2. CI/CD maturity ladder

The ladder referred to here runs, bottom to top: builds by hand -> continuous integration (every
change built and tested automatically) -> continuous delivery (every merge to the release branch
produces a tested, published, deployable artifact and proves that it deploys) -> continuous
deployment (every such merge goes to a real, long-lived production environment with no human
step), with supply-chain hardening (immutable digests, signed images, provenance) at the top.

**Rung reached: continuous delivery, with the deploy exercised against an ephemeral cluster.** It
does not reach continuous deployment, because there is no long-lived production cluster for
`cd.yml` to deploy to.

**Evidence for continuous integration (the rung below, fully met).**
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every PR to `main` and every
push to `dev` ([`ci.yml:5-10`](../.github/workflows/ci.yml#L5-L10)) and has seven jobs:

- `lint-and-type` ([`:23-62`](../.github/workflows/ci.yml#L23-L62)): ruff and mypy on the
  backend, eslint and `tsc --noEmit` on the frontend.
- `test-backend` ([`:64-115`](../.github/workflows/ci.yml#L64-L115)): pytest against real
  `postgres:16` and `redis:7` service containers, not mocks, with `TRIAGE_PROVIDER: simulated` and
  `--cov-fail-under=65`.
- `test-frontend` ([`:117-134`](../.github/workflows/ci.yml#L117-L134)): Vitest.
- `build` ([`:138-174`](../.github/workflows/ci.yml#L138-L174)): builds both images with
  `push: false` (a PR never publishes anything) and hands them to `scan` as an artifact.
- `scan` ([`:176-213`](../.github/workflows/ci.yml#L176-L213)): Trivy on both images,
  `severity: HIGH,CRITICAL`, `ignore-unfixed: true`, `exit-code: "1"`.
- `manifests` ([`:215-231`](../.github/workflows/ci.yml#L215-L231)):
  `kubectl kustomize k8s/overlays/prod | kubeconform -strict`.
- `integration` ([`:235-308`](../.github/workflows/ci.yml#L235-L308)): a real
  `docker compose up -d --build`, wait for `/ready`, POST a complaint, GET it back and compare the
  category, assert `X-Cache` goes MISS -> HIT, then `docker compose down -v`.

The gate is enforced, not advisory: `main` is protected by a repository ruleset that requires all
seven checks plus one approving review before a merge. PR #78 is the red -> green evidence: a
deliberately failing test turned the checks red and blocked the merge button
([`docs/evidence/cnfirm_blocked.png`](evidence/cnfirm_blocked.png)), and the fix in the same PR
turned them green ([`docs/evidence/ci-green.png`](evidence/ci-green.png)).

**Evidence for continuous delivery (the rung claimed).**
[`.github/workflows/cd.yml`](../.github/workflows/cd.yml) runs on push to `main` only
([`cd.yml:5-7`](../.github/workflows/cd.yml#L5-L7)), which the ruleset means can only happen by
merging a reviewed, green PR. It has three jobs, each gated on the previous one by `needs:`:

1. `test` ([`cd.yml:20-24`](../.github/workflows/cd.yml#L20-L24)) re-runs the whole of `ci.yml`
   on the merged result via `workflow_call` ([`ci.yml:10`](../.github/workflows/ci.yml#L10)), not
   a hand-picked subset.
2. `build-push` (`needs: test`, [`cd.yml:29-93`](../.github/workflows/cd.yml#L29-L93)) pushes
   both images to GHCR tagged `${{ github.sha }}` and `latest`
   ([`:60-62`](../.github/workflows/cd.yml#L60-L62), [`:72-74`](../.github/workflows/cd.yml#L72-L74))
   using `GITHUB_TOKEN` with `packages: write` scoped to this job only
   ([`:32-34`](../.github/workflows/cd.yml#L32-L34)), emits a Syft SBOM per image
   ([`:78-88`](../.github/workflows/cd.yml#L78-L88)), and exposes both image digests as job
   outputs ([`:35-37`](../.github/workflows/cd.yml#L35-L37)).
3. `deploy-k8s` (`needs: build-push`, [`cd.yml:98-206`](../.github/workflows/cd.yml#L98-L206))
   pins `overlays/prod` to this commit's SHA with `kustomize edit set image`
   ([`:115-121`](../.github/workflows/cd.yml#L115-L121)), creates a throwaway k3d cluster
   ([`:127`](../.github/workflows/cd.yml#L127)), applies the overlay, waits on the migrate Job and
   on `kubectl rollout status` for both Deployments
   ([`:165-170`](../.github/workflows/cd.yml#L165-L170)), smoke-tests through the Ingress rather
   than a ClusterIP shortcut ([`:188-198`](../.github/workflows/cd.yml#L188-L198)), prints
   `kubectl get hpa`, and deletes the cluster.

`release.yml` adds semver image tags and a GitHub Release with generated notes on a `v*` tag
([`release.yml:6-8`](../.github/workflows/release.yml#L6-L8),
[`:41-61`](../.github/workflows/release.yml#L41-L61),
[`:95-102`](../.github/workflows/release.yml#L95-L102)).

**It did not work first time.** `cd.yml` failed on its first four runs before succeeding end to
end, for the first time, on run
[36345139912](https://github.com/Malaikaa0/CivicPulse/actions/runs/36345139912). The final
blocker, fixed last, was a secret-ordering race: the committed placeholder Secret was applied first and
the real one patched in afterwards, so Postgres could initialise its data directory with the
placeholder password while `DATABASE_URL` carried the real one; the migrate Job then never
connected and backend's `/ready` never passed. PR #81 fixed it by writing the real Secret from
GitHub Secrets into the runner's checkout *before* `kubectl apply`
([`cd.yml:137-160`](../.github/workflows/cd.yml#L137-L160) explains the ordering in place).
Continuous delivery is claimed on the strength of that successful run, not on the workflow file
alone.

**The next rung, and what it would buy.** Three gaps keep this below continuous deployment and
the hardened top of the ladder:

- **No persistent target.** The deploy proves the release *would* roll out on a clean cluster,
  then throws the cluster away. Continuous deployment means every green merge to `main` actually
  replaces what users are running on a long-lived cluster, with `kubectl rollout undo` (see
  [`docs/RUNBOOK.md`](RUNBOOK.md)) as the one-command way back. That buys a merge-to-production
  lead time measured in minutes, and removes the manual "go and deploy it now" step, which is
  where most deploy mistakes happen.
- **Deploy by digest.** `build-push` already captures the digests
  ([`cd.yml:35-37`](../.github/workflows/cd.yml#L35-L37)), but `deploy-k8s` still deploys the
  SHA *tag* ([`:119-120`](../.github/workflows/cd.yml#L119-L120)). A tag can in principle be
  re-pushed; a `@sha256:` digest cannot, so deploying by digest makes "what is production
  running?" tamper-evident (ADR-0003's stated revisit condition).
- **Signing and provenance.** Images are not signed (for example with cosign), no admission
  policy verifies signatures, and actions are pinned to major-version tags such as `@v4` rather
  than commit SHAs. Signing plus a verifying admission check would mean the cluster refuses any
  image this pipeline did not build, instead of trusting anyone who can push to GHCR.

## 3. The exact line guaranteeing build-once-deploy-many

**Backend side (built):** [`backend/app/config.py:17-18`](../backend/app/config.py#L17-L18):

```python
database_url: str
redis_url: str
```

These have **no default value**. A default would have to be a real-looking connection string,
which would either point nowhere useful or silently point at `localhost` — the exact mistake the
spec calls out as an automatic deduction (`§5.3`, "localhost used for service-to-service
communication"). With no default, the same backend image refuses to start until the environment
supplies real values, so the identical image is what runs in dev, in CI's integration job, and in
Kubernetes — only the environment differs. **What breaks without it:** the image would work by
accident in whichever environment matches the hard-coded default and fail silently or connect to
the wrong database everywhere else — the definition of *not* build-once-deploy-many.

**Frontend side.** ADR-0002 records the decision (nginx proxies `/api`, so the frontend never
bakes in a backend URL); the line that keeps it true is
[`frontend/nginx.conf:25-36`](../frontend/nginx.conf#L25-L36). The upstream is a variable
(`set $backend "http://backend:8000"; proxy_pass $backend;`), resolved lazily per request via
`resolver 127.0.0.11 valid=10s`, not a hard-coded `proxy_pass http://backend:8000` baked in at
build time. The same built image runs against Compose's `backend` service and Kubernetes'
`backend` Service without a rebuild or a `/config.js` — both environments just need a DNS name
called `backend` to resolve, which is exactly what each platform's own service discovery gives it
for free.

## 4. Determinism with a probabilistic LLM

**What "correct" means for `LLMTriage`:** not "always returns the same category for the same
text" — a live model can vary — but "always returns a value from the enum, with a summary within
the length limit, or is rejected and replaced by the deterministic fallback." Correctness for this
component is checked at the boundary (validated schema in, validated schema out), never at the
content level. This is enforced in
[`backend/app/providers/triage/llm.py`](../backend/app/providers/triage/llm.py): every reply is
parsed with `TriageResult.model_validate()` regardless of whether the provider claims success, and
a plausible-but-wrong category, a 400-character "one-line" summary, or a confidence outside 0–1
is rejected as `TriageInvalidOutput` rather than trusted (see
`tests/test_llm_triage.py::test_malformed_or_out_of_schema_output_is_rejected`, 15 cases).

**How CI stays deterministic despite this:** CI never calls the real model. `SimulatedTriage`
([`backend/app/providers/triage/simulated.py`](../backend/app/providers/triage/simulated.py))
delegates classification to the same rule-based logic (so results are meaningful, not random) and
adds a seeded, injectable confidence and *scripted* failures — `script=[TriageTimeout(), None]`
means "fail once, then succeed," `always_fail=...` means "the provider is down" — so every failure
mode the real LLM could exhibit is reproduced without a network call, a real clock, or real
randomness. `TriageService` itself takes `sleep`, `jitter` and `clock` as injectable parameters
([`backend/app/services/triage.py:46-49`](../backend/app/services/triage.py#L46-L49)), so even the
retry-and-backoff path is tested with zero real waiting. The one live check that does exist —
against the real Gemini API — was run manually with three throwaway complaints and is documented
in the PR history (#27), never in the automated suite.

**The spec's must-have test, satisfied at three levels:** a provider that always raises still
yields a result with `triaged_by == "rules:fallback"` — proven at the unit level
(`test_triage_service.py`), at the HTTP level (`test_complaint_routes.py`, parametrised over all
five `TriageError` types), and against a real database (`test_complaints_db.py`).

## 5. HPA lag

Measured against a real k3d cluster with `backend-hpa` (`k8s/base/hpa.yaml`) and a k6 load
test ramping to 40 VUs against `GET /api/complaints`; the full capture is
[`k8s/evidence/hpa-watch.txt`](../k8s/evidence/hpa-watch.txt) and
[`k8s/evidence/k6-load-test-output.txt`](../k8s/evidence/k6-load-test-output.txt).

`hpa-watch.txt` timestamps are `kubectl get hpa`'s own AGE column, i.e. seconds since the HPA
object was created, not seconds since the k6 test started - the two clocks were aligned by
comparing the HPA's reported AGE against k6's own elapsed-time counter in the same terminal check
partway through the run (AGE 4m27s alongside k6's own "running (1m58.0s)"), giving a fixed ~149s
offset used for every "t=" figure below.

Offered load finished ramping to its full 40 VUs at approximately t=30s into the test. CPU
utilization was still at 20% at the previous fifteen-second sample and had jumped to 101% by
t=31s; by the next sample, at t=46s, `kubectl get hpa` already showed 4 replicas. That ~15-second
window is almost entirely the HorizontalPodAutoscaler controller's own sync period (15s by
default, not something this HPA object configures) — `behavior.scaleUp.stabilizationWindowSeconds:
0` in the manifest means Kubernetes adds no deliberate delay of its own on top of that. The new
pods were created in the same window and were already `1/1 Ready` well before the next check
(~87s later), which is mostly container start (the images were already resident on both nodes,
so there was no pull to wait on) plus the `startupProbe`'s 2-second check interval — a small
fraction of the total lag compared to the controller's sync period. Call it roughly 15-20 seconds
from full load to a scaling decision, and a further single-digit number of seconds for that
capacity to actually be Ready and serving. During that whole window the original 2 replicas
absorbed the entire burst alone: p95 latency degraded to 3.49s (`k6-load-test-output.txt`) against
a normal sub-second baseline, but 0 of 3900 requests failed — the system slowed down under
pressure rather than falling over, which is the outcome the readiness probe and the HPA's request-
based denominator are there to produce. Shrinking this lag further would mean lowering the
cluster's HPA sync period (out of scope for a namespaced HPA object) or raising `minReplicas`
above what steady-state traffic needs — which is just capacity planning wearing a different hat,
and exactly the trade-off autoscaling cannot avoid.

**One thing in this capture I can't explain.** Right after the scale-out, from t=46s to t=137s,
the HPA reported CPU at 1-3% even though k6 held a steady 40 VUs the whole time; it then jumped
to 52% and stayed there (visible in `hpa-watch.txt` and as the dip in
`k8s/evidence/hpa-replicas-vs-load.svg`). Plausible causes are metrics-server returning stale or
partial readings while the two new pods had no usage history yet, or the original pods being
restarted under load (the liveness-timeout problem noted in `k8s/base/backend.yaml`), but it was
not diagnosed at the time, so treat the numbers in that window as unreliable rather than as a
real drop in load.

## 6. Why VPA is in Off mode

Ran the full loop against a real k3d cluster with the official Vertical Pod Autoscaler installed
(recommender, updater, admission-controller — `updateMode: "Off"` on `backend-vpa`,
[`k8s/base/vpa.yaml`](../k8s/base/vpa.yaml)):

1. **Recorded the guessed requests**: `cpu: 250m, memory: 256Mi` (the values `backend.yaml` shipped
   with before this exercise).
2. **Ran the load test** (the same k6 ramp used for question 5).
3. **`kubectl describe vpa backend-vpa`** — full output in
   [`k8s/evidence/vpa-recommendation.txt`](../k8s/evidence/vpa-recommendation.txt): `Target: cpu:
   587m, memory: 262144k`. Memory was already almost exactly right; CPU was under-provisioned by
   more than 2x for this load pattern.
4. **Updated `backend.yaml`'s requests to `cpu: 600m`** (limits raised to `1` core to keep headroom
   above the new request) — memory left at `256Mi`.
5. **Re-ran a load test against the updated Deployment and compared HPA behaviour.** The
   honest version of the result, because the two runs were *not* identical:

   | | Before (`requests: 250m`, `limits: 500m`) | After (`requests: 600m`, `limits: 1`) |
   |---|---|---|
   | Load script | `load/hpa-load.js`, 40 VUs, 4 min | a shorter copy, 40 VUs, 2 min 30 s |
   | Throughput actually served | 16.2 req/s (3900 requests) | 30.8 req/s (4615 requests) |
   | Peak reported utilisation | 101%/60% (`k8s/evidence/hpa-watch.txt`) | 84%/60% (`k8s/evidence/hpa-watch-after-vpa-update.txt`) |
   | Approx. CPU per pod at that peak | 101% x 250m = ~253m | 84% x 600m = ~504m |
   | Peak replicas | 4 | 3 |

   What this does show: after the change, each pod did roughly **twice the work** (and about twice
   the CPU) before the HPA reacted, so it scaled to fewer replicas while serving nearly double the
   throughput. What it does *not* cleanly show is "same load, only the denominator changed" -
   an earlier draft of this answer claimed that, and the numbers don't support it. k6's
   constant-VU model is closed-loop: the faster the backend answers, the more requests the same
   40 VUs send. Raising the **limit** from 500m to 1 core stopped the CPU throttling that had
   pushed p95 latency to 3.49 s in the first run, so the same VUs generated far more traffic. The
   request change (the HPA's denominator) and the limit change (the throttling ceiling) happened
   together, so this pair of runs can't separate their effects. A clean comparison would change
   only `requests`, keep the limit fixed, and use an open-loop arrival rate
   (`constant-arrival-rate`) so offered load is the same in both runs.

   **Why VPA stays in Off mode** doesn't depend on that experiment; it follows from the
   arithmetic. The HPA computes utilisation as usage / request. In **Auto** mode, VPA would raise
   the request after a busy period; the same usage then reads as a lower percentage, so the HPA
   scales *in*; fewer pods means more load per pod, so VPA's next recommendation goes up again.
   Two controllers act on the same signal (CPU), each reacting to a number the other just moved.
   Recommender-only mode breaks the loop at the one point that can't fight back:
   `kubectl describe vpa` produces a number, a person decides whether the trade-off (more headroom
   per pod vs. a less sensitive autoscaler) is worth it, and only then does a commit change
   `resources.requests`. The record, test, describe, update, retest loop above *is* that human
   decision, made once and on purpose.

## 7. `internal: true` and the hosted LLM

`LLMTriage` needs outbound internet access to reach `generativelanguage.googleapis.com`
([`backend/app/providers/triage/factory.py:11`](../backend/app/providers/triage/factory.py#L11)),
so the backend cannot live on an `internal: true` network alone. `compose.yaml` gives it two
networks instead of one:

```yaml
backend:
  networks: [edge, internal]   # the only service that bridges both
```

(`compose.yaml:99`, mirrored in `compose.prod.yaml`). `edge` is a plain bridge network (reaches
the outside world, including Gemini's API), `internal` is `internal: true` (no route out at all).
`postgres` and `redis` are `internal`-only — they can talk to `backend`, `backend` can talk out
through `edge`, and `frontend` (which only joins `edge`) can reach neither database directly.
`backend` sitting on both networks is not a shortcut around the segmentation requirement; it is
the segmentation requirement, applied correctly: the one service that legitimately needs both
kinds of access is the one service allowed to have both, and everything that doesn't need outbound
access (postgres, redis) is denied it by construction, not by convention. If `backend` were
`internal`-only instead, the LLM path would not crash — it would silently degrade to
`rules:fallback` on every single request (a real, observable symptom via `GET
/api/meta/providers`, not an exception), which is exactly the kind of quiet, hard-to-notice
failure question 8 below is about.

## 8. The failure

**Symptom.** After wiring `LLMTriage` against a real Gemini API key, every triage request
succeeded with a **correct category** and no exception — `POST /api/complaints` returned 201s that
looked entirely normal. Nothing was obviously broken.

**What we wrongly believed first.** Nothing looked wrong at all, which was the trap: a system
designed to fail safely (spec section 2.5's whole point) degraded to `rules:fallback` on every
single call, and the fallback's output is good enough that the mistake was invisible from the API
response alone. Checking `docs/adr/0001-provider-interface.md`'s Consequences section, the two
signs that would have caught it sooner were `triaged_by` in the response body (`rules:fallback`
instead of `llm:gemini`) and `/api/meta/providers`, both of which existed but were not checked
first.

**The exact log line that told us the truth.** Calling the Gemini endpoint directly (bypassing the
app) surfaced the real HTTP response body:

```
{'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash-lite is no longer
available to new users. Please update your code to use models/gemini-3.5-flash-lite ...',
'status': 'NOT_FOUND'}}
```

The model the provider defaulted to (`gemini-2.5-flash-lite`) had been retired for new API keys.
Worse, Google's model **list** endpoint still returned it as available — the bug could not have
been caught by listing valid models, only by making a real call and reading the actual error.

**Fix and the lesson recorded in ADR-0001:** the default model was changed to a named, pinned
version (`gemini-3.5-flash-lite`, never a `-latest` alias, which can change meaning without
warning) and made configurable via `GEMINI_MODEL`
([`backend/app/config.py:31`](../backend/app/config.py#L31)). The larger lesson, stated in
[`docs/adr/0001-provider-interface.md`](adr/0001-provider-interface.md): a fallback that works
too well is itself a monitoring gap — `triaged_by` and `/api/meta/providers` exist precisely so a
permanently failing provider is *visible*, not just survivable, and checking them needs to be the
first debugging step, not an afterthought.

---

## Design justifications the rubric asks for outside the 8 questions above

**D3 — why these two indexes, and which query each serves.** Both are declared in the migration,
with the query named at the point of definition rather than as an afterthought:
[`backend/alembic/versions/0001_create_complaints.py:75-77`](../backend/alembic/versions/0001_create_complaints.py#L75-L77)
justifies `ix_complaints_status_priority` ("Serves the dashboard list: `WHERE status = ?
[AND priority = ?]`. status leads, so status-only filters use it too") and
[`:79-81`](../backend/alembic/versions/0001_create_complaints.py#L79-L81) justifies
`ix_complaints_created_at` ("Serves `ORDER BY created_at DESC LIMIT n OFFSET m`, the newest-first
pagination of the dashboard"). An index that serves no named query in this system was deliberately
not added — the spec calls an unexplained index "cargo cult," and the two here are the only two
access patterns the dashboard actually has.

**E1/E2 — why the stats cache needs both a TTL and explicit invalidation.** Answered in full in
the module's own docstring,
[`backend/app/services/stats.py:1-9`](../backend/app/services/stats.py#L1-L9): invalidation alone
can fail (Redis unreachable at that instant), be missed by a write path added later, or lose a
race against a concurrent read; a TTL alone means a fresh complaint is invisible in the stats for
up to 30 seconds, which the spec rules out directly. Invalidation handles the common case
immediately; the TTL is the backstop for when it does not fire. `ComplaintService`'s `on_write`
hook (wired in `feat/backend-integration`, PR #39) is what actually calls `StatsService.invalidate()`
after every committed write.

**E3 — why the rate limiter has to live in Redis, not in the process.** Stated at the top of
[`backend/app/services/rate_limit.py:3-4`](../backend/app/services/rate_limit.py#L3-L4): "The
counter lives in Redis, not in this process. With the backend autoscaled to N pods, a per-pod
counter would let one client send N times the limit (each pod sees only its share)." This is the
same reasoning the spec gives directly (§2.4): the moment the HPA scales the backend to four pods,
an in-process limiter would permit four times the intended traffic, because each pod would count
its own quarter of the requests against its own separate limit. The atomic INCR+PTTL+PEXPIRE Lua
script in [`backend/app/providers/cache.py:7-11`](../backend/app/providers/cache.py#L7-L11) exists
for the same distributed reason at a smaller scale: two separate Redis commands (INCR, then
EXPIRE) are not atomic across two pods issuing them concurrently, and a process dying between the
two would leave a counter key with no expiry, permanently blocking that client.

**E4 — Redis AOF on a named volume, and why a cache needs persistence at all.** Configured in
all three places Redis runs: `compose.yaml:54` (`redis-server --appendonly yes`) with the
`redisdata` named volume mounted at `/data` (`compose.yaml:23,56`), the same in
`compose.prod.yaml:44-46`, and on Kubernetes as a Deployment with its own PVC
(`k8s/base/redis.yaml:7,33,37`).

Not everything in this Redis needs it. The stats cache (30 s TTL) is rebuilt from PostgreSQL on
the next miss, and losing the rate-limiter counters on a restart only gives a client a fresh
window. What does need it is the content-hash triage cache
(`backend/app/providers/triage/caching.py:135`, 24 h TTL by default): each entry is the result of
an LLM call. Lose those on a restart and every repeat complaint pays for a fresh inference again,
which is the exact cost the cache exists to avoid.

Tested, not assumed: a key written with `SET triage:demo ... EX 86400` into `redis:7
--appendonly yes` on a named volume was still there after the container was deleted and
recreated, with its TTL still counting down (86393 s left). The same test without the volume lost
the key. AOF alone isn't enough; it has to be on a volume that outlives the container.

The spec's third named volume, `ollama_models`, is intentionally absent: it exists to cache the
Ollama provider's model weights, and that provider was evaluated and not built (issue #45;
`backend/app/config.py:20` doesn't accept `ollama` as a provider). `compose.yaml:18-21` records the same.
