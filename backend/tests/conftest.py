import os

# CI and local test runs never call a real LLM and never need real infrastructure.
os.environ.setdefault("TRIAGE_PROVIDER", "simulated")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@postgres:5432/test")
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
