from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_default_to_simulated_provider() -> None:
    assert get_settings().triage_provider == "simulated"
