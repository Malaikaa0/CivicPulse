"""The committed OpenAPI snapshot the frontend is typed against must match the live schema.

If this fails, the API contract changed: regenerate with
    backend/.venv/Scripts/python.exe scripts/export_openapi.py
and let frontend/tests/apiContract.test.ts tell you which frontend types need updating.
"""

import json
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json"


def test_frontend_openapi_snapshot_matches_the_live_schema() -> None:
    live = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"

    assert SNAPSHOT.read_text(encoding="utf-8") == live, (
        "frontend/src/api/openapi.json is out of date - run scripts/export_openapi.py"
    )
