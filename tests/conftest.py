"""Shared pytest fixtures."""

import psycopg2
import pytest

from app.database import close_connection_pool, get_db_connection


@pytest.fixture(scope="session")
def require_postgres():
    """Skip database tests when PostgreSQL cannot be reached."""
    try:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except psycopg2.OperationalError as exc:
        first_line = str(exc).strip().splitlines()[0]
        pytest.skip(f"PostgreSQL is not available ({first_line})")

    yield
    close_connection_pool()
