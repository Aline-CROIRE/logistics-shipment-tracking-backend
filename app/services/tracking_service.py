"""Business logic for processing package tracking scans."""

from typing import Any

from psycopg2.extras import Json

from app.database import get_db_connection

ALLOWED_STATUSES = {
    "Created",
    "In Transit",
    "Out for Delivery",
    "Delivered",
    "Delayed",
    "Returned",
}

# A package in a final status has finished its journey and accepts no scans.
FINAL_STATUSES = {"Delivered", "Returned"}


class PackageNotFoundError(LookupError):
    """Raised when a scan references a package that does not exist."""


class InvalidStatusTransitionError(ValueError):
    """Raised when a scan targets a package that is already in a final status."""


def process_tracking_scan(
    package_id: int,
    location_id: int,
    status: str,
    scanner_id: str | None = None,
    scan_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a scan and update package status atomically."""
    if (
        isinstance(package_id, bool)
        or not isinstance(package_id, int)
        or package_id <= 0
    ):
        raise ValueError("package_id must be a positive integer")

    if (
        isinstance(location_id, bool)
        or not isinstance(location_id, int)
        or location_id <= 0
    ):
        raise ValueError("location_id must be a positive integer")

    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unsupported tracking status: {status!r}")

    if scanner_id is not None and not isinstance(scanner_id, str):
        raise ValueError("scanner_id must be a string or None")

    if scan_metadata is not None and not isinstance(scan_metadata, dict):
        raise ValueError("scan_metadata must be a dictionary or None")

    metadata = scan_metadata if scan_metadata is not None else {}

    with get_db_connection() as connection, connection.cursor() as cursor:
        # Lock the package row so concurrent scans of the same package are
        # applied one at a time and the status check below stays valid.
        cursor.execute(
            """
                SELECT tracking_id, current_status
                FROM packages
                WHERE package_id = %s
                FOR UPDATE
                """,
            (package_id,),
        )
        package = cursor.fetchone()

        if package is None:
            raise PackageNotFoundError(
                f"No package exists with package_id={package_id}"
            )

        tracking_id, current_status = package

        if current_status in FINAL_STATUSES:
            raise InvalidStatusTransitionError(
                f"Package {tracking_id} is already {current_status} "
                "and cannot accept further scans"
            )

        cursor.execute(
            """
                UPDATE packages
                SET current_status = %s
                WHERE package_id = %s
                """,
            (status, package_id),
        )

        cursor.execute(
            """
                INSERT INTO tracking_events (
                    package_id,
                    location_id,
                    status,
                    scanner_id,
                    scan_metadata
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING event_id
                """,
            (
                package_id,
                location_id,
                status,
                scanner_id,
                Json(metadata),
            ),
        )
        event_id = cursor.fetchone()[0]

    return {
        "event_id": event_id,
        "package_id": package_id,
        "tracking_id": tracking_id,
        "location_id": location_id,
        "status": status,
        "scanner_id": scanner_id,
    }
