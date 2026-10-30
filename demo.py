"""Interactive reviewer demonstration for the logistics tracking backend."""

import json
import sys
from pathlib import Path
from typing import Any

import psycopg2
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import close_connection_pool, get_db_connection
from app.external_stores import get_mongo_collection, get_redis_client
from app.services.tracking_integration import process_and_publish_tracking_scan

STATUSES = [
    "Created",
    "In Transit",
    "Out for Delivery",
    "Delivered",
    "Delayed",
    "Returned",
]


def print_header(title: str) -> None:
    """Display a consistent section heading."""
    print("\n" + "=" * 65)
    print(f" {title}")
    print("=" * 65)


def print_records(records: list[dict[str, Any]]) -> None:
    """Display database records in a readable format."""
    if not records:
        print("No records found.")
        return

    for index, record in enumerate(records, start=1):
        print(f"\nRecord {index}")
        print("-" * 40)
        for key, value in record.items():
            print(f"{key.replace('_', ' ').title()}: {value}")


def fetch_all(query: str, parameters: tuple = ()) -> list[dict[str, Any]]:
    """Execute a parameterized SELECT query and return dictionaries."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(query, parameters)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_package(tracking_id: str) -> dict[str, Any] | None:
    """Retrieve a package and its related shipment and customer details."""
    records = fetch_all(
        """
        SELECT
            p.package_id,
            p.tracking_id,
            p.current_status,
            p.package_metadata,
            p.created_at,
            s.shipment_number,
            c.name AS customer_name,
            c.email AS customer_email
        FROM packages AS p
        JOIN shipments AS s
            ON s.shipment_id = p.shipment_id
        JOIN customers AS c
            ON c.customer_id = s.customer_id
        WHERE p.tracking_id = %s
        """,
        (tracking_id,),
    )
    return records[0] if records else None


def show_all_packages() -> None:
    """Display all registered packages."""
    print_header("REGISTERED PACKAGES")

    records = fetch_all("""
        SELECT
            p.tracking_id,
            p.current_status,
            s.shipment_number,
            c.name AS customer_name
        FROM packages AS p
        JOIN shipments AS s
            ON s.shipment_id = p.shipment_id
        JOIN customers AS c
            ON c.customer_id = s.customer_id
        ORDER BY p.package_id
        """)
    print_records(records)


def show_package_details() -> None:
    """Display details for a selected package."""
    print_header("PACKAGE DETAILS")

    tracking_id = input("Enter tracking ID: ").strip().upper()
    if not tracking_id:
        print("Tracking ID cannot be empty.")
        return

    package = get_package(tracking_id)

    if package is None:
        print(f"Package {tracking_id} was not found.")
        return

    print_records([package])


def get_locations() -> list[dict[str, Any]]:
    """Retrieve available scanning locations."""
    return fetch_all("""
        SELECT location_id, location_name, city, country
        FROM locations
        ORDER BY location_id
        """)


def choose_location() -> dict[str, Any] | None:
    """Let the user select a valid scanning location."""
    locations = get_locations()

    if not locations:
        print("No scanning locations are registered.")
        return None

    print("\nAvailable scanning locations:")

    for location in locations:
        print(
            f'{location["location_id"]}. '
            f'{location["location_name"]} '
            f'({location["city"]}, {location["country"]})'
        )

    try:
        location_id = int(input("\nChoose location ID: ").strip())
    except ValueError:
        print("Please enter a valid numeric location ID.")
        return None

    return next(
        (location for location in locations if location["location_id"] == location_id),
        None,
    )


def choose_status() -> str | None:
    """Let the user select a supported tracking status."""
    print("\nAvailable tracking statuses:")

    for index, status in enumerate(STATUSES, start=1):
        print(f"{index}. {status}")

    try:
        choice = int(input("\nChoose status number: ").strip())
    except ValueError:
        print("Please enter a valid number.")
        return None

    if not 1 <= choice <= len(STATUSES):
        print("Invalid status selection.")
        return None

    return STATUSES[choice - 1]


def process_new_scan() -> None:
    """Process a real tracking scan through all integrated services."""
    print_header("PROCESS A TRACKING SCAN")

    tracking_id = input("Enter package tracking ID: ").strip().upper()

    if not tracking_id:
        print("Tracking ID cannot be empty.")
        return

    package = get_package(tracking_id)

    if package is None:
        print(f"Package {tracking_id} was not found.")
        return

    print(f'\nPackage: {package["tracking_id"]}')
    print(f'Current status: {package["current_status"]}')

    location = choose_location()
    if location is None:
        print("Scan cancelled: invalid location.")
        return

    status = choose_status()
    if status is None:
        print("Scan cancelled: invalid status.")
        return

    scanner_id = input("\nEnter scanner ID (press Enter for DEMO-SCANNER-01): ").strip()
    scanner_id = scanner_id or "DEMO-SCANNER-01"

    metadata_text = input(
        "Enter additional metadata as JSON (press Enter for {}): "
    ).strip()

    try:
        metadata = json.loads(metadata_text) if metadata_text else {}
    except json.JSONDecodeError:
        print("Invalid JSON metadata. Scan cancelled.")
        return

    if not isinstance(metadata, dict):
        print("Metadata must be a JSON object. Scan cancelled.")
        return

    print("\nProcessing scan...")

    try:
        result = process_and_publish_tracking_scan(
            package_id=package["package_id"],
            location_id=location["location_id"],
            status=status,
            scanner_id=scanner_id,
            scan_metadata=metadata,
        )
    except ValueError as exc:
        print(f"\nScan rejected: {exc}")
        print("No changes were made.")
        return

    print("\nPostgreSQL tracking operation completed.")
    print(f'Event ID: {result["event_id"]}')
    print(f'Tracking ID: {result["tracking_id"]}')
    print(f'Status: {result["status"]}')
    print(f'Scanner ID: {result["scanner_id"]}')

    errors = result["integration_errors"]

    if errors:
        print("\nSome external integrations reported errors:")
        for service, error in errors.items():
            print(f"- {service}: {error}")
    else:
        print("\nRedis status cache updated successfully.")
        print("MongoDB scanner event stored successfully.")

    print("\nThe scan operation has finished.")


def show_tracking_history() -> None:
    """Display the complete tracking history for a package."""
    print_header("PACKAGE TRACKING HISTORY")

    tracking_id = input("Enter tracking ID: ").strip().upper()

    if not tracking_id:
        print("Tracking ID cannot be empty.")
        return

    records = fetch_all(
        """
        SELECT
            p.tracking_id,
            event.event_id,
            event.status,
            event.event_timestamp,
            location.location_name,
            location.city,
            location.country,
            event.scanner_id
        FROM packages AS p
        JOIN tracking_events AS event
            ON event.package_id = p.package_id
        JOIN locations AS location
            ON location.location_id = event.location_id
        WHERE p.tracking_id = %s
        ORDER BY event.event_timestamp, event.event_id
        """,
        (tracking_id,),
    )

    if not records:
        print(f"No tracking history found for {tracking_id}.")
        return

    print_records(records)


def show_redis_status() -> None:
    """Retrieve the latest cached package status from Redis."""
    print_header("REDIS LATEST STATUS")

    tracking_id = input("Enter tracking ID: ").strip().upper()

    if not tracking_id:
        print("Tracking ID cannot be empty.")
        return

    redis_client = get_redis_client()
    redis_client.ping()

    redis_key = f"package:{tracking_id}:status"
    cached_value = redis_client.get(redis_key)

    if cached_value is None:
        print(f"No cached status found for {tracking_id}.")
        return

    print(f"Redis key: {redis_key}")
    print("Cached value:")

    try:
        print(json.dumps(json.loads(cached_value), indent=4))
    except json.JSONDecodeError:
        print(cached_value)


def show_mongodb_logs() -> None:
    """Retrieve scanner-event documents stored in MongoDB."""
    print_header("MONGODB SCANNER EVENTS")

    tracking_id = input("Enter tracking ID: ").strip().upper()

    if not tracking_id:
        print("Tracking ID cannot be empty.")
        return

    collection = get_mongo_collection()

    collection.database.client.admin.command("ping")

    documents = list(
        collection.find(
            {"tracking_id": tracking_id},
            {"_id": 0},
        )
        .sort("event_timestamp", -1)
        .limit(10)
    )

    if not documents:
        print(f"No MongoDB scanner events found for {tracking_id}.")
        return

    for index, document in enumerate(documents, start=1):
        print(f"\nScanner event {index}")
        print(json.dumps(document, indent=4, default=str))


def show_tracking_analytics() -> None:
    """Calculate elapsed time between consecutive package scans."""
    print_header("TRACKING ANALYTICS")

    tracking_id = input("Enter tracking ID: ").strip().upper()

    if not tracking_id:
        print("Tracking ID cannot be empty.")
        return

    records = fetch_all(
        """
        WITH package_history AS (
            SELECT
                p.package_id,
                p.tracking_id,
                event.event_id,
                event.status,
                event.event_timestamp,
                location.city,
                location.country,
                LAG(event.event_timestamp) OVER (
                    PARTITION BY p.package_id
                    ORDER BY event.event_timestamp, event.event_id
                ) AS previous_event_timestamp
            FROM packages AS p
            JOIN tracking_events AS event
                ON event.package_id = p.package_id
            JOIN locations AS location
                ON location.location_id = event.location_id
            WHERE p.tracking_id = %s
        )
        SELECT
            tracking_id,
            status,
            city,
            country,
            event_timestamp,
            previous_event_timestamp,
            ROUND(
                (
                    EXTRACT(
                        EPOCH FROM (
                            event_timestamp - previous_event_timestamp
                        )
                    ) / 60
                )::numeric,
                2
            ) AS minutes_since_previous_scan
        FROM package_history
        ORDER BY event_timestamp, event_id
        """,
        (tracking_id,),
    )

    if not records:
        print(f"No analytics data found for {tracking_id}.")
        return

    print_records(records)
    print("\nThe first scan has no previous event, so its interval is NULL.")


def show_menu() -> None:
    """Display the interactive demonstration menu."""
    print_header("LOGISTICS SHIPMENT TRACKING SYSTEM")
    print("Welcome to the Logistics Shipment Tracking Backend Demo.")
    print("\nChoose an operation:")
    print("1. View all packages")
    print("2. View package details")
    print("3. Process a new tracking scan")
    print("4. View complete tracking history")
    print("5. Check Redis latest status")
    print("6. View MongoDB scanner logs")
    print("7. Run tracking analytics")
    print("0. Exit")


def main() -> None:
    """Run the interactive reviewer demonstration."""
    try:
        while True:
            show_menu()
            choice = input("\nEnter your choice: ").strip()

            actions = {
                "1": show_all_packages,
                "2": show_package_details,
                "3": process_new_scan,
                "4": show_tracking_history,
                "5": show_redis_status,
                "6": show_mongodb_logs,
                "7": show_tracking_analytics,
            }

            if choice == "0":
                print("\nThank you for exploring the tracking system.")
                break

            action = actions.get(choice)

            if action is None:
                print("\nInvalid choice. Select an option from 0 to 7.")
                continue

            try:
                action()
            except (
                psycopg2.Error,
                RedisError,
                PyMongoError,
                ValueError,
                LookupError,
            ) as exc:
                print(f"\nOperation failed: {exc}")
                print(
                    "Check that PostgreSQL, Redis, and MongoDB are running "
                    "and that your .env configuration is correct."
                )

            input("\nPress Enter to return to the main menu...")

    except KeyboardInterrupt:
        print("\n\nDemo interrupted.")

    finally:
        close_connection_pool()


if __name__ == "__main__":
    main()
