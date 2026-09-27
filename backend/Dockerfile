# syntax=docker/dockerfile:1

# ---- builder: install dependencies into a virtualenv ----
FROM python:3.12.10-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Requirements before source: this layer is only rebuilt when dependencies change.
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- runtime: no pip cache, no build tools, no dev dependencies ----
FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# The base image tag pins Python; it does not pin (or auto-update) the underlying Debian
# packages, so OS-level CVEs (openssl, perl-base, ...) accumulate against a fixed tag over time
# even though Debian already ships the fix. Caught by ci.yml's Trivy scan job, which fails on
# exactly this: HIGH/CRITICAL with a fixed version available.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app
# Migrations ship in the image so any container can bring its own database to the current
# schema: `alembic upgrade head`.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic

USER 10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Exec form: uvicorn is PID 1 and receives SIGTERM directly. On SIGTERM it stops accepting
# connections and lets in-flight requests finish, for at most 25 s: under Kubernetes' default
# terminationGracePeriodSeconds of 30, so uvicorn exits by itself before SIGKILL arrives.
# The app writes its own JSON access-log line per request, so uvicorn's plain-text one is off.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--timeout-graceful-shutdown", "25"]
