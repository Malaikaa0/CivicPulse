"""Write the backend's OpenAPI schema to frontend/src/api/openapi.json.

The frontend's hand-written API types (frontend/src/api/client.ts) are checked against this file
by frontend/tests/apiContract.test.ts, and backend/tests/test_openapi_snapshot.py fails if this
file drifts from the live schema - so a backend contract change can't silently leave the
frontend's types behind.

Run from the repo root with the backend venv whenever the API changes:
    backend/.venv/Scripts/python.exe scripts/export_openapi.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend" / "src" / "api" / "openapi.json"


def render_schema() -> str:
    # Settings require these at import time; exporting the schema never connects to either.
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://export:export@localhost/export")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    TARGET.write_text(render_schema(), encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
