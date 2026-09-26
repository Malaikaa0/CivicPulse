"""Database connectivity. All SQL in the application lives under repositories/."""

from sqlalchemy import Engine, create_engine, text


def create_db_engine(database_url: str) -> Engine:
    # pool_pre_ping discards dead connections; connect_timeout stops a probe hanging on a
    # database that is unreachable rather than merely refusing connections.
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2},
    )


def ping_database(engine: Engine) -> None:
    """Raise if the database cannot be reached and queried."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
