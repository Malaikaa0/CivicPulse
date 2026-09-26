"""Fixtures for tests that need a real PostgreSQL.

Set TEST_DATABASE_URL to run them, e.g.
    postgresql+psycopg://postgres:testpw@127.0.0.1:55432/civicpulse
Without it they are skipped, so a plain `pytest` never needs infrastructure. The schema is built
by the real Alembic migrations, so these tests also prove the migrations work.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.repositories.database import create_db_engine

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads it from here
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))

    command.upgrade(config, "head")
    db_engine = create_db_engine(url)
    yield db_engine

    db_engine.dispose()
    command.downgrade(config, "base")
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session
        db_session.rollback()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE complaints"))
