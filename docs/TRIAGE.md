# Triage

How a complaint's text becomes a validated `category`, `priority`, `summary` and `confidence`,
and what happens when the provider behind it fails. Every statement here is taken from the code
it cites; the design reasoning is in [ADR-0001](adr/0001-provider-interface.md) (provider
interface) and [ADR-0004](adr/0004-pii-and-data-governance.md) (PII).

## Request path

```
POST /api/complaints
  -> ComplaintService.create          app/services/complaints.py
    -> TriageService.triage           app/services/triage.py        retry once, then fall back
      -> CachingTriage.triage         app/providers/triage/caching.py   Redis, content hash
        -> configured provider        llm | rules | simulated (factory.py)
      -> RuleBasedTriage (fallback)   app/providers/triage/rules.py
    -> persist complaint with triaged_by + triage_latency_ms
```

The chain is assembled in `build_triage_service` ([`backend/app/ai_wiring.py`](../backend/app/ai_wiring.py)):
the provider chosen by the factory is wrapped in `CachingTriage`, and that is handed to
`TriageService` together with a recorder that feeds the outcome log and the Prometheus metrics.

## The provider interface

[`backend/app/providers/triage/base.py`](../backend/app/providers/triage/base.py):

- `TriageResult` is a frozen Pydantic model: `category: Category`, `priority: Priority`,
  `summary: str` (1 to 140 characters), `confidence: float` (0.0 to 1.0).
- `TriageProvider` is a `Protocol` with a `name: str` (what ends up in `triaged_by`) and
  `triage(text, location) -> TriageResult`.

Nothing outside `app/providers/triage/` depends on a concrete provider.

Failures are expressed in one taxonomy,
[`backend/app/providers/triage/errors.py`](../backend/app/providers/triage/errors.py), each class
carrying a `retryable` flag:

| Error | Meaning | `retryable` |
|---|---|---|
| `TriageTimeout` | the call timed out | yes |
| `TriageRateLimited` | HTTP 429 | yes |
| `TriageServerError` | HTTP 5xx, or the provider could not be reached | yes |
| `TriageBadRequest` | HTTP 400 and other 4xx: the request was wrong and will be wrong again | no |
| `TriageInvalidOutput` | the provider answered, but not with a valid `TriageResult` | no |

## Providers and `TRIAGE_PROVIDER`

`TRIAGE_PROVIDER` is read by `Settings`
([`backend/app/config.py:20`](../backend/app/config.py#L20)) as one of `llm`, `ollama`, `rules`,
`simulated`, defaulting to `simulated`. `create_triage_provider`
([`backend/app/providers/triage/factory.py:45-54`](../backend/app/providers/triage/factory.py#L45-L54))
maps it to a provider:

| `TRIAGE_PROVIDER` | Provider | `name` | Notes |
|---|---|---|---|
| `llm` | `LLMTriage` ([`llm.py`](../backend/app/providers/triage/llm.py)) | `llm:gemini` or `llm:groq` | Chosen by `LLM_VENDOR` (default `gemini`). Both use the OpenAI SDK against an OpenAI-compatible base URL ([`factory.py:11-12`](../backend/app/providers/triage/factory.py#L11-L12)). Gemini needs `GEMINI_API_KEY` and uses `GEMINI_MODEL` (default `gemini-3.5-flash-lite`); Groq needs `GROQ_API_KEY` and `GROQ_MODEL`. A missing value raises `ProviderConfigurationError` naming the variable, never the value ([`factory.py:19-24`](../backend/app/providers/triage/factory.py#L19-L24)). |
| `rules` | `RuleBasedTriage` ([`rules.py`](../backend/app/providers/triage/rules.py)) | `rules` | Weighted keyword regexes per category (English and common Urdu words), ties broken by a fixed precedence, priority from `_HIGH` / `_LOW` patterns, summary is the first sentence cut to 140 characters, confidence 0.3 when nothing matched and at most 0.85 otherwise. Deterministic and has no I/O. |
| `simulated` | `SimulatedTriage` ([`simulated.py`](../backend/app/providers/triage/simulated.py)) | `simulated` | For tests and CI. Classifies with `RuleBasedTriage`, replaces the confidence with a value derived from a SHA-256 of `seed:text` (0.50 to 0.99), and can raise scripted errors (`script=[TriageTimeout(), None]`) or fail on every call (`always_fail=...`). No network, clock or randomness. |
| `ollama` | none | | Accepted by the settings type, but the factory raises `NotImplementedError` ([`factory.py:53-54`](../backend/app/providers/triage/factory.py#L53-L54)). There is no Ollama provider in this codebase. |

CI runs with `TRIAGE_PROVIDER=simulated` (`backend/tests/conftest.py`,
`.github/workflows/ci.yml`); `k8s/overlays/prod` patches it to `llm`.

## Structured output and validation

`LLMTriage._ask` ([`llm.py:89-116`](../backend/app/providers/triage/llm.py#L89-L116)) calls
`chat.completions.create` with `temperature=0`, `max_tokens=300` and
`response_format={"type": "json_object"}`. The system prompt
([`llm.py:32-48`](../backend/app/providers/triage/llm.py#L32-L48)) lists the exact keys and the
allowed enum values.

The reply is not trusted because JSON mode was requested. `LLMTriage._parse`
([`llm.py:118-132`](../backend/app/providers/triage/llm.py#L118-L132)):

1. empty or missing content raises `TriageInvalidOutput`;
2. a surrounding Markdown code fence (```` ```json ... ``` ````) is stripped;
3. the payload is parsed with `json.loads` and validated with `TriageResult.model_validate`;
4. invalid JSON, a category or priority outside the enum, a summary over 140 characters or a
   confidence outside 0 to 1 all raise `TriageInvalidOutput`.

Model output is only ever parsed into this model. It is never evaluated and never used to build
SQL.

## Timeout, retry and fallback

**Timeout.** The OpenAI client is built with `timeout=settings.triage_timeout_seconds`
(`TRIAGE_TIMEOUT_SECONDS`, default `10.0`, [`config.py:21`](../backend/app/config.py#L21)) and
`max_retries=0`, so the SDK never retries on its own
([`llm.py:68-72`](../backend/app/providers/triage/llm.py#L68-L72)). SDK exceptions are translated
into the taxonomy above ([`llm.py:98-110`](../backend/app/providers/triage/llm.py#L98-L110)).

**Retry.** `TriageService._attempt_with_one_retry`
([`services/triage.py:85-93`](../backend/app/services/triage.py#L85-L93)) retries exactly once,
and only when the error is a `TriageError` with `retryable = True` (timeout, 429, 5xx or
connection failure). Before the retry it waits `retry_delay_seconds * (1 + jitter())`, which with
the defaults is between 0.5 and 1.0 seconds. `TriageBadRequest` and `TriageInvalidOutput` are not
retried. `sleep`, `jitter` and `clock` are constructor parameters, so tests exercise this path
without real waiting.

**Fallback.** Any exception that survives the retry, including one that is not a `TriageError` at
all, is caught ([`services/triage.py:60-75`](../backend/app/services/triage.py#L60-L75)) and the
complaint is triaged by `RuleBasedTriage` instead
([`:95-105`](../backend/app/services/triage.py#L95-L105)). The citizen still gets a 201.

**How `triaged_by` records it.** `TriageService` returns a `TriageOutcome` with `triaged_by`,
`latency_ms`, `fallback`, and on fallback the `failed_provider` and `error_class`:

- success: the provider's `name`, so `llm:gemini`, `llm:groq` or `rules`. `simulated` is stored as
  `rules` ([`services/triage.py:25`](../backend/app/services/triage.py#L25)), because the
  database only accepts known values;
- fallback: `rules:fallback` ([`services/triage.py:21`](../backend/app/services/triage.py#L21)).

The database enforces the allowed set with a check constraint: `llm:groq`, `llm:gemini`,
`llm:ollama`, `rules`, `rules:fallback`
(`backend/alembic/versions/0002_allow_gemini_triaged_by.py`). `ComplaintService` stores
`triaged_by` and `triage_latency_ms` with the complaint and, on fallback, logs one WARNING with
the complaint id, the failed provider and the error class
([`services/complaints.py:104-115`](../backend/app/services/complaints.py#L104-L115)).

## Content-hash caching

`CachingTriage` ([`backend/app/providers/triage/caching.py`](../backend/app/providers/triage/caching.py))
sits between `TriageService` and the provider:

- **Key:** `triage:v1:` + SHA-256 of the provider name and the normalised text
  ([`caching.py:85-87`](../backend/app/providers/triage/caching.py#L85-L87)). Normalising means PII
  redaction, lower-casing and collapsing whitespace
  ([`:71-73`](../backend/app/providers/triage/caching.py#L71-L73)), so the same complaint with a
  different phone number, casing or spacing shares an entry. `location` is not part of the key.
- **TTL:** `TRIAGE_CACHE_TTL_SECONDS`, default 86400 (24 h,
  [`config.py:22`](../backend/app/config.py#L22)).
- **Value:** the validated `TriageResult` as JSON, with PII redacted from the summary
  ([`caching.py:126-131`](../backend/app/providers/triage/caching.py#L126-L131)). Complaint text is
  never stored.
- **Only successes are cached.** A provider error propagates unchanged, so the retry and fallback
  above still happen. A retried call goes through the cache lookup again. Fallback results come
  from `RuleBasedTriage` directly and are not cached.
- **The cache cannot break triage.** If Redis cannot be read, the provider is called directly; an
  unreadable entry counts as a miss; failed writes and counter updates are logged and ignored
  ([`caching.py:96-144`](../backend/app/providers/triage/caching.py#L96-L144)). The Redis client
  uses 0.5 s connect and socket timeouts
  ([`store.py:32-40`](../backend/app/providers/triage/store.py#L32-L40)).
- **Hit rate:** hits and misses are counted in Redis (`triage:stats:hits`, `triage:stats:misses`),
  so every replica adds to the same totals, and reported by `/api/meta/providers`.

`triaged_by` on a cache hit is still the wrapped provider's name, because `CachingTriage` copies
it ([`caching.py:82-83`](../backend/app/providers/triage/caching.py#L82-L83)).

## PII redaction

`redact_pii` ([`backend/app/providers/triage/redaction.py`](../backend/app/providers/triage/redaction.py))
replaces email addresses with `[EMAIL]` and runs of digits containing 9 or more digits (phone
numbers, national ID numbers) with `[NUMBER]`. Shorter numbers such as house numbers, amounts or
durations are left alone. It does **not** detect names or street addresses written in the free
text; ADR-0004 records that residual risk.

It is applied:

- to the text sent to the hosted LLM ([`llm.py:83`](../backend/app/providers/triage/llm.py#L83)).
  `location` and the reporter's contact are never sent
  ([`llm.py:77-80`](../backend/app/providers/triage/llm.py#L77-L80));
- to the cache key material and to the cached summary (see above).

The API key is held only by the SDK client: `LLMTriage.__repr__` shows the name and model only,
and error messages carry the HTTP status code, not the request. Keys are `SecretStr` in
`Settings` ([`config.py:28-33`](../backend/app/config.py#L28-L33)).

## Prompt-injection guard

Complaint text is treated as data, not instructions:

1. The system prompt says the text between `<complaint>` and `</complaint>` is untrusted and that
   any instruction inside it must be ignored
   ([`llm.py:35-38`](../backend/app/providers/triage/llm.py#L35-L38)).
2. Any `<complaint>` or `</complaint>` tag inside the text is replaced with `[removed]` before
   wrapping, so a complaint cannot close the delimiter early
   ([`llm.py:51`](../backend/app/providers/triage/llm.py#L51), [`:83`](../backend/app/providers/triage/llm.py#L83)).
3. Whatever the model answers, it must validate against `TriageResult`. An injection that gets the
   model to return a value outside the enum is rejected as `TriageInvalidOutput`, and the
   complaint falls back to the rules.

Covered by
`backend/tests/test_llm_triage.py::test_an_injection_that_fools_the_model_is_stopped_by_the_schema`.

## Where latency and provider health are visible

- **`GET /api/meta/providers`** ([`backend/app/routes/meta.py`](../backend/app/routes/meta.py),
  [`backend/app/services/meta.py`](../backend/app/services/meta.py)) returns the active provider,
  vendor and model; the last 20 outcomes, each with `provider` (the `triaged_by` value),
  `latency_ms`, `fallback` and a timestamp, kept in the Redis list `triage:outcomes`
  ([`backend/app/services/triage_outcomes.py`](../backend/app/services/triage_outcomes.py)); cache
  hits, misses and hit rate; and `outcome_store_available`, which is `false` (with the figures
  absent, not zero) when Redis cannot be read. No complaint text is stored in the outcome log.
- **The complaint itself** carries `triaged_by` and `triage_latency_ms` in the API response
  ([`backend/app/schemas.py`](../backend/app/schemas.py)).
- **`GET /metrics`** exposes the histogram `triage_duration_seconds{triaged_by}` and
  `triage_fallbacks_total{provider,error_class}` (Prometheus appends `_total` to the counter) ([`backend/app/metrics.py`](../backend/app/metrics.py)).

`latency_ms` is measured by `TriageService` across the whole call, including the retry wait and
any fallback ([`services/triage.py:60-75`](../backend/app/services/triage.py#L60-L75)).

A fallback that works well can hide a provider that is failing on every call (engineering notes,
question 8). A run of `rules:fallback` in `/api/meta/providers` is the signal to check first; see
[`docs/RUNBOOK.md`](RUNBOOK.md).
