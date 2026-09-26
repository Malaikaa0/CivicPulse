from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_readiness_service
from app.main import create_app
from app.services.readiness import ReadinessService


def _ok() -> None:
    return None


def _down() -> None:
    raise ConnectionError("unreachable")


def _client(checks: dict[str, Callable[[], None]]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_readiness_service] = lambda: ReadinessService(checks)
    return TestClient(app)


def test_ready_returns_200_when_all_dependencies_are_up() -> None:
    response = _client({"postgres": _ok, "redis": _ok}).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.parametrize("broken", ["postgres", "redis"])
def test_ready_returns_503_naming_the_failed_dependency(broken: str) -> None:
    checks = {"postgres": _ok, "redis": _ok}
    checks[broken] = _down

    response = _client(checks).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "failed": [broken]}


def test_ready_names_every_failed_dependency() -> None:
    response = _client({"postgres": _down, "redis": _down}).get("/ready")

    assert response.status_code == 503
    assert response.json()["failed"] == ["postgres", "redis"]


def test_health_stays_up_when_dependencies_are_down() -> None:
    # Liveness must never depend on Postgres or Redis, or a database outage would restart
    # every pod instead of just removing them from the Service.
    response = _client({"postgres": _down, "redis": _down}).get("/health")

    assert response.status_code == 200
