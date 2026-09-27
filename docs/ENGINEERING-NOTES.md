# Engineering Notes

Answers to spec section 5.2. Question 2 (CI/CD maturity ladder) still needs infrastructure that
does not exist yet — no `.github/workflows/*.yml` — and is marked **PENDING** rather than guessed
at. Question 7 is marked PENDING below for the same reason it originally was, but note that
`compose.yaml` referenced there now exists; that answer needs revisiting rather than left as
written. Questions 5 (HPA lag) and 6 (VPA/HPA conflict) were PENDING for the same kind of reason —
no Kubernetes manifests existed to measure against — until the HPA, VPA, and real load tests below
existed; both are now answered from that measurement. The rest are answered from what is actually
built and tested today.

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
something more specific overrides it first. CI's own workflow must set the same variable at the
job level once `ci.yml` exists (pending, see question 2).

## 2. CI/CD maturity ladder — **PENDING**

No `.github/workflows/*.yml` exists yet (M2's half). This question cannot be answered honestly
until `ci.yml` and `cd.yml` are written; a rung claimed without a workflow to point at is a guess,
which the spec explicitly scores as zero. To complete: identify the rung from Lecture 03 slide 32
that matches whatever `ci.yml`/`cd.yml` actually do (lint+test on PR, or +build+scan, or full
deploy-on-merge), cite the workflow file and job names, and name the next rung.

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
([`backend/app/services/triage.py:41-46`](../backend/app/services/triage.py#L41-L46)), so even the
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
5. **Re-ran the same load test against the updated Deployment and reported what changed about HPA
   behaviour** — this is the interesting part, and it's the feedback loop the spec warns about,
   caught in the act rather than just described:

   | | requests.cpu: 250m (before) | requests.cpu: 600m (after) |
   |---|---|---|
   | Peak reported utilisation | 101%/60% (`k8s/evidence/hpa-watch.txt`) | 84%/60% (`k8s/evidence/hpa-watch-after-vpa-update.txt`) |
   | Peak replicas for the *same* offered load | 4 | 3 |

   The real CPU the backend burned under identical traffic didn't change — only the denominator
   did. A bigger request makes the exact same usage read as a smaller percentage, so the HPA
   scaled less aggressively for equal load. That is precisely the mechanism the spec's warning
   describes: if VPA had been running in **Auto** mode instead of Off, it would have pushed
   `backend.yaml`'s request up right after step 3's measurement, which — as just demonstrated —
   lowers computed utilisation on its own, which tells the HPA to scale *in*, which raises
   per-pod load on the smaller replica count, which pushes VPA's next recommendation back up
   again. Two controllers, one signal (CPU), each reacting to a number the other one just moved.
   Recommender-only mode breaks the loop at the one point that can't fight back: `kubectl describe
   vpa` produces a number, a person reads it and decides whether the trade-off (more headroom per
   pod vs. a less sensitive autoscaler) is worth taking, and only then does a new commit change
   `resources.requests`. The record→test→describe→update→retest loop above *is* that human
   decision, made once, on purpose, instead of continuously and automatically.

## 7. `internal: true` and the hosted LLM

`LLMTriage` needs outbound internet access to reach `generativelanguage.googleapis.com`
([`backend/app/providers/triage/factory.py:11`](../backend/app/providers/triage/factory.py#L11)),
so the backend cannot live on an `internal: true` network alone. `compose.yaml` gives it two
networks instead of one:

```yaml
backend:
  networks: [edge, internal]   # the only service that bridges both
```

(`compose.yaml:95`, mirrored in `compose.prod.yaml`). `edge` is a plain bridge network (reaches
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

**E4 — Redis AOF on a named volume, and why a cache needs persistence at all — PENDING.** This
needs `compose.yaml` to exist (the volume declaration is M2's). The honest answer to "why does a
cache need persistence when the whole point of a cache is that it can be rebuilt" has two sides,
and the actual configuration should state which one this system picked: (a) the stats cache and
the rate-limiter counters truly can be rebuilt from PostgreSQL and from a clean slate respectively,
so AOF buys only a faster warm-up after a restart, or (b) the 24-hour content-hash triage cache
represents real (if reproducible) work — losing it after a Redis restart means the next instance
of every duplicate complaint costs a fresh inference again, which is the exact cost the cache
exists to avoid. Argument (b) is the stronger one given what this Redis instance actually stores,
but it should be confirmed once the volume is configured and can be tested by restarting the
container and checking the cache survives.
