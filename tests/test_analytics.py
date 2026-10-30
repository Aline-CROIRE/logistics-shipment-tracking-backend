"""Integration tests for the analytics queries in sql/analytics.sql."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.database import get_db_connection

ANALYTICS_PATH = Path(__file__).resolve().parents[1] / "sql" / "analytics.sql"
BASE_TIME = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)


def load_analytics_queries() -> list[str]:
    """Split analytics.sql into its executable statements."""
    sql_text = ANALYTICS_PATH.read_text(encoding="utf-8")
    statements = [
        statement.strip()
        for statement in sql_text.split(";")
        if any(
            line.strip() and not line.strip().startswith("--")
            for line in statement.splitlines()
        )
    ]
    assert len(statements) == 3, "analytics.sql should contain three queries"
    return statements


def run_query(cursor, query: str) -> list[dict]:
    """Execute a query and return its rows as dictionaries."""
    cursor.execute(query)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def insert_event(cursor, package_id, location_id, status, minutes_after_base):
    """Insert one tracking event at a known time."""
    cursor.execute(
        """
        INSERT INTO tracking_events (
            package_id, location_id, status, event_timestamp
        )
        VALUES (%s, %s, %s, %s)
        """,
        (
            package_id,
            location_id,
            status,
            BASE_TIME + timedelta(minutes=minutes_after_base),
        ),
    )


@pytest.fixture
def analytics_data():
    """Create known tracking data and roll it back after the test."""
    token = uuid4().hex
    tracking_ids = {name: f"TEST-ANALYTICS-{token}-{name}" for name in "ABC"}

    with get_db_connection() as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO customers (name, email)
                    VALUES ('Analytics Test', %s)
                    RETURNING customer_id
                    """,
                    (f"analytics-{token}@example.com",),
                )
                customer_id = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO shipments (customer_id, shipment_number)
                    VALUES (%s, %s)
                    RETURNING shipment_id
                    """,
                    (customer_id, f"SHP-TEST-{token}"),
                )
                shipment_id = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO locations (location_name, city, country)
                    VALUES (%s, 'Testville', 'Testland')
                    RETURNING location_id
                    """,
                    (f"Analytics Hub {token}",),
                )
                location_id = cursor.fetchone()[0]

                package_ids = {}
                for name, tracking_id in tracking_ids.items():
                    cursor.execute(
                        """
                        INSERT INTO packages (shipment_id, tracking_id)
                        VALUES (%s, %s)
                        RETURNING package_id
                        """,
                        (shipment_id, tracking_id),
                    )
                    package_ids[name] = cursor.fetchone()[0]

                # Package A events are inserted out of chronological order.
                # The two 14:00 events share a timestamp, so event_id decides
                # their order: "Out for Delivery" is inserted first.
                package_a = package_ids["A"]
                insert_event(cursor, package_a, location_id, "Out for Delivery", 360)
                insert_event(cursor, package_a, location_id, "Created", 0)
                insert_event(cursor, package_a, location_id, "Delivered", 360)
                insert_event(cursor, package_a, location_id, "In Transit", 210)

                # Package B has its own history; package C has no events.
                package_b = package_ids["B"]
                insert_event(cursor, package_b, location_id, "Created", 60)
                insert_event(cursor, package_b, location_id, "In Transit", 120)

                yield cursor, tracking_ids
        finally:
            connection.rollback()


def test_history_query_returns_ordered_package_history():
    """Query 1 returns only TRK-1001 events, oldest first."""
    query = load_analytics_queries()[0]

    with get_db_connection() as connection, connection.cursor() as cursor:
        rows = run_query(cursor, query)

    assert rows, "Run sql/seed.sql before integration tests"
    assert {row["tracking_id"] for row in rows} == {"TRK-1001"}

    timestamps = [row["event_timestamp"] for row in rows]
    assert timestamps == sorted(timestamps)


def test_lag_query_calculates_minutes_between_scans(analytics_data):
    """Query 2 orders each history and computes per-package intervals."""
    cursor, tracking_ids = analytics_data
    rows = run_query(cursor, load_analytics_queries()[1])

    def history(name):
        return [row for row in rows if row["tracking_id"] == tracking_ids[name]]

    package_a = history("A")
    assert [row["status"] for row in package_a] == [
        "Created",
        "In Transit",
        "Out for Delivery",
        "Delivered",
    ]
    assert [row["minutes_since_previous_scan"] for row in package_a] == [
        None,
        Decimal("210.00"),
        Decimal("150.00"),
        Decimal("0.00"),
    ]

    package_b = history("B")
    assert package_b[0]["previous_event_timestamp"] is None
    assert [row["minutes_since_previous_scan"] for row in package_b] == [
        None,
        Decimal("60.00"),
    ]

    assert history("C") == []


def test_latest_event_query_returns_one_row_per_package(analytics_data):
    """Query 3 returns each package's newest event, keeping packages without events."""
    cursor, tracking_ids = analytics_data
    rows = run_query(cursor, load_analytics_queries()[2])
    latest = {
        row["tracking_id"]: row
        for row in rows
        if row["tracking_id"] in tracking_ids.values()
    }

    assert len(rows) == len({row["tracking_id"] for row in rows})

    assert latest[tracking_ids["A"]]["event_status"] == "Delivered"
    assert latest[tracking_ids["A"]]["event_timestamp"] == BASE_TIME + timedelta(
        minutes=360
    )

    assert latest[tracking_ids["B"]]["event_status"] == "In Transit"

    package_c = latest[tracking_ids["C"]]
    assert package_c["current_status"] == "Created"
    assert package_c["event_status"] is None
    assert package_c["event_timestamp"] is None
