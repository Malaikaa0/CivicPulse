# Runbook

Operational playbook: how to deploy, roll back, read logs, and debug a failing triage provider.

## Deploying

**Current (Docker Compose):**

```
docker compose -f compose.prod.yaml up -d
```

with `IMAGE_TAG` in `.env` set to a specific commit SHA (never `latest` - see
`docs/adr/0003-deploy-by-sha.md`). This runs the exact image built and scanned for that commit,
with no source bind-mount and no `--reload`. `migrate` runs `alembic upgrade head` once,
idempotently, before `backend` starts.

**Kubernetes:** not built yet. This section will be filled in with `kubectl apply -k
k8s/overlays/prod` and the rollout-status check once the manifests exist.

## Rolling back

**Current (Compose):** re-run the deploy command with the previous commit SHA in `IMAGE_TAG`:
```
IMAGE_TAG=<previous-sha> docker compose -f compose.prod.yaml up -d
```
This is the declarative, auditable rollback - `git show <previous-sha>` tells you exactly what
is running. Data is unaffected: `pgdata` and `redisdata` are named volumes, untouched by
`docker compose down` (without `-v`) or by which image tag is deployed.

**Kubernetes:** not built yet. Will document both `kubectl rollout undo` (fast, imperative) and
re-applying the previous overlay with the previous SHA (declarative, auditable) once the
manifests and `cd.yml` exist.

## Reading logs

Every log line is one JSON object on stdout (`backend/app/logging_config.py:41`,
`JsonFormatter`) - never written to a file, because a container's filesystem is ephemeral.
```
docker compose logs -f backend
```
Every line carries `request_id`, propagated from the `X-Request-ID` header or generated if
absent. To trace one request end to end:
```
docker compose logs backend | grep '"request_id":"<the-id>"'
```
The `X-Request-ID` response header on any API call gives you the id to search for.

A fallback to the rule-based provider logs exactly one `WARNING` per occurrence, with the
complaint id, the provider that failed, and the error class:
```
docker compose logs backend | grep '"level":"WARNING"'
```

## When triage starts failing

**Symptom:** complaints are being created successfully (still 201s, `/api/complaints` works
fine), but something feels off - categories seem generic, or you suspect the hosted LLM is not
actually being called.

**First check:** `GET /api/meta/providers`. This is the observability surface built exactly for
this situation (`backend/app/services/meta.py`). It reports:
- `active_provider` - what `TRIAGE_PROVIDER` is actually set to right now
- `recent_outcomes` - the last 20 triage calls, each with `provider`, `latency_ms`, `fallback`
- `cache` - hits/misses/hit_rate on the content-hash triage cache

If `recent_outcomes` shows `fallback: true` on every recent call, the hosted provider is down
or misconfigured, and every citizen is silently getting the rule-based classification instead.
This is designed to degrade safely (a citizen never sees a 500), which is exactly why checking
this endpoint has to be the first step - the system will not announce the failure on its own.

**Second check:** the WARNING log lines (see above). The `error_class` field names the exact
failure: `TriageTimeout`, `TriageRateLimited`, `TriageServerError` (all retried once
automatically before falling back), or `TriageBadRequest` / `TriageInvalidOutput` (never
retried - the request or the reply was malformed, retrying will not help).

**Known real failure mode, already hit once:** a hosted model gets retired and the vendor's own
model-list endpoint still claims it is available. The only way to catch this is a real call, not
a list check - see `docs/ENGINEERING-NOTES.md` question 8 for the full story and
`backend/app/config.py`'s `gemini_model` setting for the fix (pin to a named model, override via
`GEMINI_MODEL`, never trust a `-latest` alias).

**Third check, if the provider is Gemini/Groq specifically:** confirm the API key is actually
set and not expired -
```
docker compose exec backend python -c "from app.config import get_settings; s = get_settings(); print(bool(s.gemini_api_key))"
```
This prints `True`/`False` without ever printing the key itself (`SecretStr`).

**Escape hatch:** set `TRIAGE_PROVIDER=rules` in `.env` and redeploy. This never calls a network
service, never fails, and keeps the system fully available while the hosted provider issue is
being fixed.
