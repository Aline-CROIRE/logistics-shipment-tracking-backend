"""Verify a tracking scan across PostgreSQL, Redis, and MongoDB."""

import json
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import get_db_connection
from app.external_stores import get_mongo_collection, get_redis_client
from app.services.tracking_integration import process_and_publish_tracking_scan


def load_test_context(tracking_id: str, location_name: str) -> dict[str, Any]:
    """Load a seeded package and location without hardcoding database IDs."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT package_id, current_status
            FROM packages
            WHERE tracking_id = %s
            """,
            (tracking_id,),
        )
        package = cursor.fetchone()

        cursor.execute(
            """
            SELECT location_id
            FROM locations
            WHERE location_name = %s
            """,
            (location_name,),
        )
        location = cursor.fetchone()

    if package is None:
        raise LookupError(f"Package {tracking_id} was not found")
    if location is None:
        raise LookupError(f"Location {location_name} was not found")

    return {
        "package_id": package[0],
        "original_status": package[1],
        "location_id": location[0],
        "tracking_id": tracking_id,
    }


def cleanup(
    context: dict[str, Any] | None,
    scanner_id: str,
    redis_key: str,
    previous_cache: str | None,
) -> None:
    """Remove the test event and restore the original status and cache."""
    if context is not None:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM tracking_events WHERE scanner_id = %s",
                (scanner_id,),
            )
            cursor.execute(
                """
                UPDATE packages
                SET current_status = %s
                WHERE package_id = %s
                """,
                (context["original_status"], context["package_id"]),
            )

    redis_client = get_redis_client()
    if previous_cache is None:
        redis_client.delete(redis_key)
    else:
        redis_client.set(redis_key, previous_cache)

    get_mongo_collection().delete_many({"scanner_id": scanner_id})


def main() -> None:
    """Run one real scan and verify its persistence across all stores."""
    tracking_id = "TRK-1001"
    location_name = "Kigali Distribution Centre"
    scanner_id = f"E2E-TEST-{uuid.uuid4().hex}"
    context = None
    previous_cache = None
    redis_key = f"package:{tracking_id}:status"

    try:
        context = load_test_context(tracking_id, location_name)

        redis_client = get_redis_client()
        redis_client.ping()
        previous_cache = redis_client.get(redis_key)

        mongo_collection = get_mongo_collection()
        mongo_collection.database.client.admin.command("ping")

        result = process_and_publish_tracking_scan(
            package_id=context["package_id"],
            location_id=context["location_id"],
            status="Delayed",
            scanner_id=scanner_id,
            scan_metadata={
                "purpose": "end_to_end_verification",
                "test_id": scanner_id,
            },
        )

        if result["integration_errors"]:
            raise RuntimeError(f"External-store errors: {result['integration_errors']}")

        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_status
                FROM packages
                WHERE package_id = %s
                """,
                (context["package_id"],),
            )
            package_status = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT event_id, status, scanner_id
                FROM tracking_events
                WHERE event_id = %s
                """,
                (result["event_id"],),
            )
            event = cursor.fetchone()

        assert package_status == "Delayed"
        assert event is not None
        assert event[1] == "Delayed"
        assert event[2] == scanner_id
        print("PASS: PostgreSQL status update and tracking event")

        cached_value = redis_client.get(redis_key)
        assert cached_value is not None
        assert json.loads(cached_value) == {
            "status": "Delayed",
            "location": "Kigali, Rwanda",
        }
        print("PASS: Redis latest-status cache")

        document = mongo_collection.find_one({"scanner_id": scanner_id})
        assert document is not None
        assert document["event_id"] == result["event_id"]
        assert document["scan_metadata"]["test_id"] == scanner_id
        print("PASS: MongoDB scanner-event document")

        print("PASS: End-to-end tracking scan verified")

    finally:
        cleanup(context, scanner_id, redis_key, previous_cache)


if __name__ == "__main__":
    main()
