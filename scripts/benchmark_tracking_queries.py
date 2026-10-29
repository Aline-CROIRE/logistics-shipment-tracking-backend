"""Validate tracking analytics and benchmark the composite index."""

import json
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import get_db_connection

PACKAGE_COUNT = 1_000
EVENT_COUNT = 200_000
INDEX_NAME = "idx_tracking_events_package_timestamp"


def summarize_plan(plan_result: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract useful metrics from PostgreSQL's JSON EXPLAIN output."""
    report = plan_result[0]
    node_types: list[str] = []
    index_names: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        node_types.append(node.get("Node Type", "Unknown"))
        index_name = node.get("Index Name")
        if index_name:
            index_names.append(index_name)

        for child in node.get("Plans", []):
            visit(child)

    visit(report["Plan"])

    return {
        "planning_ms": report.get("Planning Time"),
        "execution_ms": report.get("Execution Time"),
        "actual_rows": report["Plan"].get("Actual Rows"),
        "nodes": node_types,
        "indexes": index_names,
    }


def explain_package_history(cursor: Any, package_id: int) -> dict[str, Any]:
    """Run and summarize an actual execution plan."""
    cursor.execute(
        """
        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT event_id, event_timestamp
        FROM tracking_events
        WHERE package_id = %s
        ORDER BY event_timestamp, event_id
        """,
        (package_id,),
    )

    return summarize_plan(cursor.fetchone()[0])


def validate_analytics(cursor: Any) -> None:
    """Execute each analytics query and report its result count."""
    analytics_path = PROJECT_ROOT / "sql" / "analytics.sql"
    sql_text = analytics_path.read_text(encoding="utf-8")
    statements = [
        statement.strip()
        for statement in sql_text.split(";")
        if statement.strip()
        and any(
            line.strip() and not line.strip().startswith("--")
            for line in statement.splitlines()
        )
    ]

    if len(statements) != 3:
        raise ValueError(f"Expected 3 analytics queries, found {len(statements)}")

    for number, statement in enumerate(statements, start=1):
        cursor.execute(statement)
        rows = cursor.fetchall()
        print(f"PASS: Analytics query {number} returned {len(rows)} rows")


def main() -> None:
    """Validate analytics and benchmark indexing safely."""
    token = uuid.uuid4().hex
    tracking_prefix = f"BENCHMARK-{token}-"
    scanner_id = f"BENCHMARK-{token}"
    index_created = False

    with get_db_connection() as connection, connection.cursor() as cursor:
        validate_analytics(cursor)

        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = %s
            )
            """,
            (INDEX_NAME,),
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(
                f"{INDEX_NAME} already exists. "
                "The baseline benchmark needs to run before index creation."
            )

        cursor.execute("SELECT shipment_id FROM shipments ORDER BY shipment_id LIMIT 1")
        shipment_row = cursor.fetchone()
        cursor.execute("SELECT location_id FROM locations ORDER BY location_id LIMIT 1")
        location_row = cursor.fetchone()

        if shipment_row is None or location_row is None:
            raise RuntimeError(
                "Seed at least one shipment and one location before benchmarking."
            )

        shipment_id = shipment_row[0]
        location_id = location_row[0]

        cursor.execute("SAVEPOINT benchmark_data")

        try:
            cursor.execute(
                """
                INSERT INTO packages (
                    shipment_id,
                    tracking_id,
                    current_status,
                    package_metadata
                )
                SELECT
                    %s,
                    %s || generated.n::text,
                    'Created',
                    '{"benchmark": true}'::jsonb
                FROM generate_series(1, %s) AS generated(n)
                """,
                (shipment_id, tracking_prefix, PACKAGE_COUNT),
            )

            cursor.execute(
                """
                CREATE TEMP TABLE benchmark_package_ids
                ON COMMIT DROP AS
                SELECT
                    package_id,
                    ROW_NUMBER() OVER (
                        ORDER BY package_id
                    )::integer AS seq
                FROM packages
                WHERE tracking_id LIKE %s
                """,
                (tracking_prefix + "%",),
            )

            cursor.execute(
                """
                INSERT INTO tracking_events (
                    package_id,
                    location_id,
                    status,
                    event_timestamp,
                    scanner_id,
                    scan_metadata
                )
                SELECT
                    package_ids.package_id,
                    %s,
                    'In Transit',
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                        + generated.n * INTERVAL '1 second',
                    %s,
                    '{"benchmark": true}'::jsonb
                FROM generate_series(1, %s) AS generated(n)
                JOIN benchmark_package_ids AS package_ids
                    ON package_ids.seq =
                        ((generated.n - 1) %% %s) + 1
                """,
                (location_id, scanner_id, EVENT_COUNT, PACKAGE_COUNT),
            )

            cursor.execute("""
                SELECT package_id
                FROM benchmark_package_ids
                WHERE seq = 42
                """)
            target_row = cursor.fetchone()
            if target_row is None:
                raise RuntimeError("Could not find the benchmark package.")

            target_package_id = target_row[0]

            cursor.execute("ANALYZE tracking_events")
            before = explain_package_history(cursor, target_package_id)

            print("\n=== BEFORE INDEX ===")
            print(json.dumps(before, indent=2))

            cursor.execute(f"""
                CREATE INDEX {INDEX_NAME}
                ON tracking_events (package_id, event_timestamp)
                """)
            index_created = True

            cursor.execute("ANALYZE tracking_events")
            after = explain_package_history(cursor, target_package_id)

            print("\n=== AFTER INDEX ===")
            print(json.dumps(after, indent=2))

            before_ms = before["execution_ms"]
            after_ms = after["execution_ms"]

            print("\n=== PERFORMANCE COMPARISON ===")
            print(f"Before execution time: {before_ms:.3f} ms")
            print(f"After execution time:  {after_ms:.3f} ms")

            if before_ms > 0:
                change = (before_ms - after_ms) / before_ms * 100
                print(f"Execution-time improvement: {change:.2f}%")
                if change > 0:
                    print("The indexed query was faster in this run.")
                elif change < 0:
                    print("The indexed query was slower in this run.")
                else:
                    print("Execution times were equal in this run.")

            cursor.execute(
                "DELETE FROM tracking_events WHERE scanner_id = %s",
                (scanner_id,),
            )
            cursor.execute(
                "DELETE FROM packages WHERE tracking_id LIKE %s",
                (tracking_prefix + "%",),
            )
            cursor.execute("ANALYZE tracking_events")
            cursor.execute("RELEASE SAVEPOINT benchmark_data")

            print("\nPASS: Synthetic tracking events and packages removed")
            if index_created:
                print(f"PASS: Index {INDEX_NAME} created")

        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT benchmark_data")
            cursor.execute("RELEASE SAVEPOINT benchmark_data")
            raise

    print("PASS: Analytics validation and benchmark completed")


if __name__ == "__main__":
    main()
