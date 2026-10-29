"""Integration tests for atomic tracking scan processing."""

from uuid import uuid4

import psycopg2
import pytest

from app.database import get_db_connection
from app.services.tracking_service import (
    PackageNotFoundError,
    process_tracking_scan,
)


@pytest.fixture
def test_package():
    """Create a package for one test and clean it up afterwards."""
    tracking_id = f"TRK-TEST-{uuid4().hex}"
    package_id = None

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT shipment_id FROM shipments ORDER BY shipment_id LIMIT 1")
        shipment = cursor.fetchone()
        assert shipment is not None, "Run sql/seed.sql before integration tests"

        cursor.execute(
            """
            INSERT INTO packages (shipment_id, tracking_id, current_status)
            VALUES (%s, %s, 'Created')
            RETURNING package_id
            """,
            (shipment[0], tracking_id),
        )
        package_id = cursor.fetchone()[0]

        cursor.execute("SELECT location_id FROM locations ORDER BY location_id LIMIT 1")
        location = cursor.fetchone()
        assert location is not None, "Run sql/seed.sql before integration tests"
        location_id = location[0]

    yield {
        "package_id": package_id,
        "location_id": location_id,
        "tracking_id": tracking_id,
    }

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM tracking_events WHERE package_id = %s",
            (package_id,),
        )
        cursor.execute(
            "DELETE FROM packages WHERE package_id = %s",
            (package_id,),
        )


def test_valid_scan_updates_status_and_inserts_event(test_package):
    """A successful scan must update the package and create its event."""
    result = process_tracking_scan(
        package_id=test_package["package_id"],
        location_id=test_package["location_id"],
        status="In Transit",
        scanner_id="SCANNER-TEST-01",
        scan_metadata={"source": "automated_test", "attempt": 1},
    )

    assert result["tracking_id"] == test_package["tracking_id"]
    assert result["status"] == "In Transit"
    assert result["event_id"] is not None

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_status FROM packages WHERE package_id = %s",
            (test_package["package_id"],),
        )
        assert cursor.fetchone()[0] == "In Transit"

        cursor.execute(
            """
            SELECT status, scanner_id, scan_metadata
            FROM tracking_events
            WHERE event_id = %s
            """,
            (result["event_id"],),
        )
        event = cursor.fetchone()

    assert event[0] == "In Transit"
    assert event[1] == "SCANNER-TEST-01"
    assert event[2] == {"source": "automated_test", "attempt": 1}


def test_invalid_status_is_rejected(test_package):
    """Unsupported statuses must be rejected before database writes."""
    with pytest.raises(ValueError, match="Unsupported tracking status"):
        process_tracking_scan(
            package_id=test_package["package_id"],
            location_id=test_package["location_id"],
            status="Flying",
        )

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_status FROM packages WHERE package_id = %s",
            (test_package["package_id"],),
        )
        assert cursor.fetchone()[0] == "Created"


def test_unknown_package_is_rejected(test_package):
    """A scan for a nonexistent package must not create an event."""
    with pytest.raises(PackageNotFoundError):
        process_tracking_scan(
            package_id=999999999,
            location_id=test_package["location_id"],
            status="In Transit",
        )


def test_failed_event_insert_rolls_back_package_update(test_package):
    """A foreign-key failure must roll back the preceding status update."""
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        process_tracking_scan(
            package_id=test_package["package_id"],
            location_id=999999999,
            status="In Transit",
        )

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_status FROM packages WHERE package_id = %s",
            (test_package["package_id"],),
        )
        assert cursor.fetchone()[0] == "Created"

        cursor.execute(
            "SELECT COUNT(*) FROM tracking_events WHERE package_id = %s",
            (test_package["package_id"],),
        )
        assert cursor.fetchone()[0] == 0
