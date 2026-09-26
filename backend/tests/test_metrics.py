"""Prometheus metrics: what is counted, how it is labelled, and the /metrics endpoint."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain import Category, Priority
from app.main import create_app
from app.metrics import CONTENT_TYPE, Metrics
from app.providers.triage.base import TriageResult
from app.providers.triage.errors import TriageTimeout
from app.services.triage import TriageOutcome, TriageService


def _app() -> FastAPI:
    app = create_app()

    @app.get("/api/complaints/{complaint_id}")
    def one_complaint(complaint_id: uuid.UUID) -> dict[str, str]:
        return {"id": str(complaint_id)}

    return app


def _metrics(app: FastAPI) -> Metrics:
    metrics: Metrics = app.state.metrics
    return metrics


def _requests(
    metrics: Metrics, method: str, route: str, status: int, series: str = "http_requests_total"
) -> float | None:
    labels = {"method": method, "route": route, "status": str(status)}
    return metrics.registry.get_sample_value(series, labels)


def _outcome(*, fallback: bool, latency_ms: int = 250, triaged_by: str = "rules") -> TriageOutcome:
    return TriageOutcome(
        result=TriageResult(
            category=Category.ROADS, priority=Priority.NORMAL, summary="s", confidence=0.5
        ),
        triaged_by=triaged_by,
        latency_ms=latency_ms,
        fallback=fallback,
        failed_provider="llm:groq" if fallback else None,
        error_class="TriageTimeout" if fallback else None,
    )


def test_a_request_increments_the_counter_and_the_histogram() -> None:
    app = _app()
    client = TestClient(app)

    client.get("/health")
    client.get("/health")

    metrics = _metrics(app)
    assert _requests(metrics, "GET", "/health", 200) == 2
    assert _requests(metrics, "GET", "/health", 200, "http_request_duration_seconds_count") == 2
    assert (
        _requests(metrics, "GET", "/health", 200, "http_request_duration_seconds_sum") is not None
    )


def test_the_latency_histogram_has_cumulative_buckets() -> None:
    app = _app()
    TestClient(app).get("/health")

    registry = _metrics(app).registry
    labels = {"method": "GET", "route": "/health", "status": "200"}
    name = "http_request_duration_seconds_bucket"
    assert registry.get_sample_value(name, {**labels, "le": "+Inf"}) == 1
    assert registry.get_sample_value(name, {**labels, "le": "10.0"}) == 1


def test_a_path_parameter_is_labelled_by_its_template_not_its_value() -> None:
    app = _app()
    client = TestClient(app)

    client.get(f"/api/complaints/{uuid.uuid4()}")
    client.get(f"/api/complaints/{uuid.uuid4()}")

    metrics = _metrics(app)
    assert _requests(metrics, "GET", "/api/complaints/{complaint_id}", 200) == 2
    # Two different ids, one series: no label explosion.
    series = [
        line
        for line in metrics.render().decode().splitlines()
        if line.startswith("http_requests_total{") and "/api/complaints/" in line
    ]
    assert len(series) == 1


def test_an_unmatched_route_is_labelled_unmatched() -> None:
    app = _app()
    client = TestClient(app)

    client.get("/wp-admin/" + uuid.uuid4().hex)
    client.get("/another/random/path")

    metrics = _metrics(app)
    assert _requests(metrics, "GET", "unmatched", 404) == 2
    assert b"wp-admin" not in metrics.render()


def test_the_status_code_is_a_label() -> None:
    app = _app()
    client = TestClient(app)

    client.get("/api/complaints/not-a-uuid")

    assert _requests(_metrics(app), "GET", "/api/complaints/{complaint_id}", 422) == 1


def test_a_wrong_method_on_a_known_route_keeps_the_route_template() -> None:
    app = _app()

    TestClient(app).post("/health")

    assert _requests(_metrics(app), "POST", "/health", 405) == 1


def test_an_unhandled_error_is_counted_as_500() -> None:
    app = _app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    TestClient(app, raise_server_exceptions=False).get("/boom")

    assert _requests(_metrics(app), "GET", "/boom", 500) == 1


def test_an_arbitrary_method_token_cannot_create_a_new_series() -> None:
    metrics = Metrics()

    metrics.observe_request("BREW", "/health", 405, 0.01)

    assert _requests(metrics, "OTHER", "/health", 405) == 1
    assert _requests(metrics, "BREW", "/health", 405) is None


def test_a_normal_triage_records_latency_but_no_fallback() -> None:
    metrics = Metrics()

    metrics.observe_triage(_outcome(fallback=False, latency_ms=250, triaged_by="llm:groq"))

    registry = metrics.registry
    assert (
        registry.get_sample_value("triage_duration_seconds_count", {"triaged_by": "llm:groq"}) == 1
    )
    assert (
        registry.get_sample_value("triage_duration_seconds_sum", {"triaged_by": "llm:groq"}) == 0.25
    )
    assert (
        registry.get_sample_value(
            "triage_duration_seconds_bucket", {"triaged_by": "llm:groq", "le": "0.25"}
        )
        == 1
    )
    assert b"triage_fallbacks_total{" not in metrics.render()


def test_a_fallback_increments_the_counter_by_provider_and_error_class() -> None:
    metrics = Metrics()
    labels = {"provider": "llm:groq", "error_class": "TriageTimeout"}

    metrics.observe_triage(_outcome(fallback=True, triaged_by="rules:fallback"))
    metrics.observe_triage(_outcome(fallback=True, triaged_by="rules:fallback"))

    assert metrics.registry.get_sample_value("triage_fallbacks_total", labels) == 2
    assert (
        metrics.registry.get_sample_value(
            "triage_duration_seconds_count", {"triaged_by": "rules:fallback"}
        )
        == 2
    )


def test_observe_triage_accepts_a_real_outcome_from_the_triage_service() -> None:
    class Failing:
        name = "llm:groq"

        def triage(self, text: str, location: str) -> TriageResult:
            raise TriageTimeout("slow")

    metrics = Metrics()
    service = TriageService(Failing(), sleep=lambda _s: None, jitter=lambda: 0.0)

    metrics.observe_triage(service.triage("pothole", "Street 1"))

    labels = {"provider": "llm:groq", "error_class": "TriageTimeout"}
    assert metrics.registry.get_sample_value("triage_fallbacks_total", labels) == 1


def test_a_fallback_without_a_provider_is_still_counted() -> None:
    metrics = Metrics()
    outcome = TriageOutcome(
        result=_outcome(fallback=False).result,
        triaged_by="rules:fallback",
        latency_ms=1,
        fallback=True,
    )

    metrics.observe_triage(outcome)

    labels = {"provider": "unknown", "error_class": "unknown"}
    assert metrics.registry.get_sample_value("triage_fallbacks_total", labels) == 1


def test_each_app_has_its_own_registry() -> None:
    first, second = _app(), _app()

    TestClient(first).get("/health")

    assert _metrics(first).registry is not _metrics(second).registry
    assert _requests(_metrics(first), "GET", "/health", 200) == 1
    assert _requests(_metrics(second), "GET", "/health", 200) is None


def test_building_many_apps_does_not_collide_on_metric_names() -> None:
    for _ in range(3):
        create_app()  # a shared global registry would raise "Duplicated timeseries"


def test_the_endpoint_serves_prometheus_text_with_the_expected_content_type() -> None:
    client = TestClient(_app())
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert CONTENT_TYPE == response.headers["content-type"]
    body = response.text
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert "# TYPE triage_duration_seconds histogram" in body
    assert "# TYPE triage_fallbacks_total counter" in body
    assert 'http_requests_total{method="GET",route="/health",status="200"} 1.0' in body


def test_metrics_counts_its_own_scrapes_but_only_after_answering() -> None:
    # Decision: /metrics is a request like any other, so scrape traffic and latency are visible.
    # It is recorded after the response is sent, so a scrape never contains itself.
    client = TestClient(_app())

    first = client.get("/metrics").text
    second = client.get("/metrics").text

    series = 'http_requests_total{method="GET",route="/metrics",status="200"}'
    assert series not in first
    assert f"{series} 1.0" in second


def test_the_endpoint_reports_fallbacks_recorded_through_the_app() -> None:
    app = _app()
    _metrics(app).observe_triage(_outcome(fallback=True, triaged_by="rules:fallback"))

    body = TestClient(app).get("/metrics").text

    assert 'triage_fallbacks_total{error_class="TriageTimeout",provider="llm:groq"} 1.0' in body
