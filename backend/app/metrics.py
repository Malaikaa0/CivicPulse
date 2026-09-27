"""Prometheus metrics, kept in a per-app registry.

The global registry would make every app built in one process (each test builds its own) collide
on metric names. Label values are all bounded sets (route templates, HTTP methods, status codes,
provider and exception class names), so a client cannot inflate the number of series.
"""

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from app.services.triage import TriageOutcome

# prometheus_client's own CONTENT_TYPE_LATEST advertises a newer exposition version than the
# classic text format that generate_latest() writes.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

UNMATCHED_ROUTE = "unmatched"

_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

_REQUEST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
# Triage is capped at 10 s per call and may retry once, so it needs a longer tail.
_TRIAGE_BUCKETS = (0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._requests = Counter(
            "http_requests",
            "HTTP requests handled.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self._request_latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency.",
            ["method", "route", "status"],
            buckets=_REQUEST_BUCKETS,
            registry=self.registry,
        )
        self._triage_latency = Histogram(
            "triage_duration_seconds",
            "Time spent triaging a complaint, including any retry and fallback.",
            ["triaged_by"],
            buckets=_TRIAGE_BUCKETS,
            registry=self.registry,
        )
        self._triage_fallbacks = Counter(
            "triage_fallbacks",
            "Triage calls that fell back to the rule-based provider.",
            ["provider", "error_class"],
            registry=self.registry,
        )

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        # An arbitrary method token would otherwise be an unbounded label value.
        labels = (method if method in _KNOWN_METHODS else "OTHER", route, str(status))
        self._requests.labels(*labels).inc()
        self._request_latency.labels(*labels).observe(seconds)

    def observe_triage(self, outcome: TriageOutcome) -> None:
        self._triage_latency.labels(outcome.triaged_by).observe(outcome.latency_ms / 1000)
        if outcome.fallback:
            self._triage_fallbacks.labels(
                outcome.failed_provider or "unknown", outcome.error_class or "unknown"
            ).inc()

    def render(self) -> bytes:
        return generate_latest(self.registry)
