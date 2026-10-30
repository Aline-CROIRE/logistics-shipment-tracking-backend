"""Coordinate committed PostgreSQL scans with Redis and MongoDB."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_db_connection
from app.external_stores import get_mongo_collection, get_redis_client
from app.services.tracking_service import process_tracking_scan

logger = logging.getLogger(__name__)

# Cached statuses expire so a missed cache update cannot stay stale forever.
STATUS_CACHE_TTL_SECONDS = 3600


def _status_cache_key(tracking_id: str) -> str:
    """Build the Redis key that stores a package's latest status."""
    return f"package:{tracking_id}:status"


def _get_event_context(event_id: int) -> dict[str, Any]:
    """Read the committed event timestamp and location details."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                event.event_timestamp,
                location.location_name,
                location.city,
                location.country
            FROM tracking_events AS event
            JOIN locations AS location
                ON location.location_id = event.location_id
            WHERE event.event_id = %s
            """,
            (event_id,),
        )
        row = cursor.fetchone()

    if row is None:
        raise LookupError(f"Tracking event {event_id} was not found")

    event_timestamp, location_name, city, country = row
    return {
        "event_timestamp": event_timestamp,
        "location_name": location_name,
        "city": city,
        "country": country,
    }


def process_and_publish_tracking_scan(
    package_id: int,
    location_id: int,
    status: str,
    scanner_id: str | None = None,
    scan_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Commit a scan first, then attempt Redis and MongoDB updates.

    Failures in external stores are reported in integration_errors.
    They do not imply that the PostgreSQL transaction was rolled back.
    """
    scan = process_tracking_scan(
        package_id=package_id,
        location_id=location_id,
        status=status,
        scanner_id=scanner_id,
        scan_metadata=scan_metadata,
    )

    result: dict[str, Any] = {
        **scan,
        "integration_errors": {},
    }

    try:
        context = _get_event_context(scan["event_id"])
    except Exception as exc:
        logger.exception("Could not read committed tracking event context")
        result["integration_errors"]["event_context"] = str(exc)
        result["integration_errors"][
            "redis"
        ] = "Skipped because event context could not be read"
        result["integration_errors"][
            "mongodb"
        ] = "Skipped because event context could not be read"
        return result

    location_text = f'{context["city"]}, {context["country"]}'
    timestamp = context["event_timestamp"]

    cache_value = {
        "status": scan["status"],
        "location": location_text,
    }

    try:
        redis_client = get_redis_client()
        redis_client.set(
            _status_cache_key(scan["tracking_id"]),
            json.dumps(cache_value),
            ex=STATUS_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.exception("Redis status cache update failed")
        result["integration_errors"]["redis"] = str(exc)

    try:
        mongo_collection = get_mongo_collection()
        mongo_collection.insert_one(
            {
                "event_id": scan["event_id"],
                "package_id": scan["package_id"],
                "tracking_id": scan["tracking_id"],
                "location_id": scan["location_id"],
                "location_name": context["location_name"],
                "city": context["city"],
                "country": context["country"],
                "status": scan["status"],
                "scanner_id": scan["scanner_id"],
                "event_timestamp": timestamp,
                "received_at": datetime.now(UTC),
                "scan_metadata": scan_metadata or {},
            }
        )
    except Exception as exc:
        logger.exception("MongoDB scanner-event logging failed")
        result["integration_errors"]["mongodb"] = str(exc)

    return result


def _load_status_from_database(tracking_id: str) -> dict[str, Any] | None:
    """Read a package's current status and latest scan location."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                p.current_status,
                latest.city,
                latest.country
            FROM packages AS p
            LEFT JOIN LATERAL (
                SELECT location.city, location.country
                FROM tracking_events AS event
                JOIN locations AS location
                    ON location.location_id = event.location_id
                WHERE event.package_id = p.package_id
                ORDER BY event.event_timestamp DESC, event.event_id DESC
                LIMIT 1
            ) AS latest ON TRUE
            WHERE p.tracking_id = %s
            """,
            (tracking_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    status, city, country = row
    return {
        "status": status,
        "location": f"{city}, {country}" if city is not None else None,
    }


def get_package_status(tracking_id: str) -> dict[str, Any] | None:
    """Return a package's latest status, using Redis before PostgreSQL.

    On a cache miss the status is read from PostgreSQL and cached. If Redis
    is unavailable, PostgreSQL still answers. Returns None for unknown
    packages. The "source" field shows where the value came from.
    """
    cache_key = _status_cache_key(tracking_id)

    try:
        cached_value = get_redis_client().get(cache_key)
        if cached_value is not None:
            return {**json.loads(cached_value), "source": "redis"}
    except Exception:
        logger.exception("Redis status lookup failed; using PostgreSQL")

    status = _load_status_from_database(tracking_id)
    if status is None:
        return None

    try:
        get_redis_client().set(
            cache_key,
            json.dumps(status),
            ex=STATUS_CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.exception("Redis status cache refresh failed")

    return {**status, "source": "postgresql"}
