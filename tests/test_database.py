"""Integration tests for the PostgreSQL database connection."""

import pytest

from app.database import close_connection_pool, get_db_connection

pytestmark = pytest.mark.usefixtures("require_postgres")


def test_database_connection_and_required_tables():
    """Verify PostgreSQL is reachable and the required tables exist."""
    try:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone()[0] == "logistics"

            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """)
            actual_tables = {row[0] for row in cursor.fetchall()}

        assert {
            "customers",
            "shipments",
            "packages",
            "locations",
            "tracking_events",
        }.issubset(actual_tables)
    finally:
        close_connection_pool()
