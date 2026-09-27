# Engineering Notes

Answers to spec section 5.2. Four questions (2, 5, 6, 7) need infrastructure that does not exist
yet — Kubernetes, CI/CD, and Docker network segmentation are M2's half of the work — and are
marked **PENDING** rather than guessed at. The other four are answered from what is actually built
and tested today.

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

**Frontend side: PENDING.** ADR-0002 records the decision (nginx proxies `/api`, so the frontend
never bakes in a backend URL) but the frontend does not exist yet, so there is no line in a built
image to cite. To complete once the frontend is built: the `nginx.conf` proxy directive.

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

## 5. HPA lag — **PENDING**

No Kubernetes manifests exist yet (M2's half). This needs a real `kubectl get hpa -w` capture
during a load test, which needs the cluster, the HPA, and `metrics-server` to exist first.

## 6. Why VPA is in Off mode — **PENDING**

Same blocker as question 5: no VPA is deployed yet. The reasoning (VPA raising a pod's CPU request
lowers computed utilisation, which makes the HPA scale in, which raises per-pod load, which makes
VPA raise the request again — a feedback loop between the two autoscalers acting on the same
signal) can be stated in the abstract, but the spec asks for *this system's* failure mode, which
needs the two actually running together at least once to describe honestly.

## 7. `internal: true` and the hosted LLM — **PENDING**

No `compose.yaml` exists yet (M2's half), so there is no `internal: true` network to describe the
consequence of. The constraint is already visible in the code, though: `LLMTriage` needs outbound
internet access to reach `generativelanguage.googleapis.com`
([`backend/app/providers/triage/factory.py:11`](../backend/app/providers/triage/factory.py#L11)),
so whichever network the backend container joins in the final Compose file cannot be `internal:
true` on its own — the backend must bridge an external-facing network as well as the internal one,
or the LLM path silently degrades to `rules:fallback` on every request (which would be a real,
observable symptom via `/api/meta/providers`, not a crash). The actual network topology and which
lines enforce it are for M2 to write once Compose exists.

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
