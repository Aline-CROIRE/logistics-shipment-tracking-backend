"""Validate tracking analytics and benchmark the composite index."""

import statistics
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
MEASURED_RUNS = 5
# A package from the middle of the id range avoids favouring early rows.
TARGET_SEQUENCE = PACKAGE_COUNT // 2

# Direct lookup of one package's events, ordered by time.
EVENT_LOOKUP_QUERY = """
    SELECT event_id, event_timestamp
    FROM tracking_events
    WHERE package_id = %s
    ORDER BY event_timestamp, event_id
"""

# Full tracking history with elapsed time between scans (CTE + LAG).
# Matches the package-level analytics query used by demo.py.
HISTORY_QUERY = """
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
"""


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


def measure_query(cursor: Any, query: str, parameters: tuple) -> dict[str, Any]:
    """Run EXPLAIN ANALYZE several times and summarize the timings."""
    # Warm-up run so the first measurement is not penalized by cold caches.
    cursor.execute(query, parameters)
    cursor.fetchall()

    runs = []
    for _ in range(MEASURED_RUNS):
        cursor.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query,
            parameters,
        )
        runs.append(summarize_plan(cursor.fetchone()[0]))

    cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + query, parameters)
    plan_text = "\n".join(row[0] for row in cursor.fetchall())

    execution_times = [run["execution_ms"] for run in runs]
    return {
        "median_ms": statistics.median(execution_times),
        "min_ms": min(execution_times),
        "max_ms": max(execution_times),
        "planning_ms": statistics.median(run["planning_ms"] for run in runs),
        "actual_rows": runs[-1]["actual_rows"],
        "nodes": runs[-1]["nodes"],
        "indexes": runs[-1]["indexes"],
        "plan_text": plan_text,
    }


def print_measurement(title: str, measurement: dict[str, Any]) -> None:
    """Display timing statistics and a representative query plan."""
    print(f"\n=== {title} ===")
    print(
        f"Execution time over {MEASURED_RUNS} runs: "
        f"median {measurement['median_ms']:.3f} ms "
        f"(min {measurement['min_ms']:.3f}, max {measurement['max_ms']:.3f})"
    )
    print(f"Median planning time: {measurement['planning_ms']:.3f} ms")
    print(f"Rows returned: {measurement['actual_rows']}")
    print(f"Indexes used: {', '.join(measurement['indexes']) or 'none'}")
    print("Representative plan:")
    print(measurement["plan_text"])


def print_comparison(name: str, before: dict[str, Any], after: dict[str, Any]) -> None:
    """Compare median execution times before and after indexing."""
    before_ms = before["median_ms"]
    after_ms = after["median_ms"]

    print(f"\n=== PERFORMANCE COMPARISON: {name} ===")
    print(f"Before index (median): {before_ms:.3f} ms")
    print(f"After index (median):  {after_ms:.3f} ms")

    if before_ms > 0:
        change = (before_ms - after_ms) / before_ms * 100
        print(f"Execution-time improvement: {change:.2f}%")
        if change > 0:
            print("The indexed query was faster in this run.")
        elif change < 0:
            print("The indexed query was slower in this run.")
        else:
            print("Execution times were equal in this run.")


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
        index_existed = cursor.fetchone()[0]

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
            if index_existed:
                # DDL is transactional, so a failure rolls this drop back.
                # The index is recreated below, leaving the schema unchanged.
                cursor.execute(f"DROP INDEX {INDEX_NAME}")

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

            cursor.execute(
                """
                SELECT p.package_id, p.tracking_id
                FROM benchmark_package_ids AS package_ids
                JOIN packages AS p
                    ON p.package_id = package_ids.package_id
                WHERE package_ids.seq = %s
                """,
                (TARGET_SEQUENCE,),
            )
            target_row = cursor.fetchone()
            if target_row is None:
                raise RuntimeError("Could not find the benchmark package.")

            target_package_id, target_tracking_id = target_row
            benchmarks = [
                (
                    "Event lookup by package_id",
                    EVENT_LOOKUP_QUERY,
                    (target_package_id,),
                ),
                (
                    "Tracking history with CTE and LAG()",
                    HISTORY_QUERY,
                    (target_tracking_id,),
                ),
            ]

            print(
                f"\nBenchmark data: {PACKAGE_COUNT} packages, "
                f"{EVENT_COUNT} tracking events. "
                f"Target package: {target_tracking_id}"
            )

            # Both tables changed, so refresh planner statistics for both.
            cursor.execute("ANALYZE packages, tracking_events")
            before = {
                name: measure_query(cursor, query, parameters)
                for name, query, parameters in benchmarks
            }
            for name, measurement in before.items():
                print_measurement(f"BEFORE INDEX: {name}", measurement)

            cursor.execute(f"""
                CREATE INDEX {INDEX_NAME}
                ON tracking_events (package_id, event_timestamp)
                """)

            cursor.execute("ANALYZE packages, tracking_events")
            after = {
                name: measure_query(cursor, query, parameters)
                for name, query, parameters in benchmarks
            }
            for name, measurement in after.items():
                print_measurement(f"AFTER INDEX: {name}", measurement)

            for name, _, _ in benchmarks:
                print_comparison(name, before[name], after[name])

            cursor.execute(
                "DELETE FROM tracking_events WHERE scanner_id = %s",
                (scanner_id,),
            )
            cursor.execute(
                "DELETE FROM packages WHERE tracking_id LIKE %s",
                (tracking_prefix + "%",),
            )
            cursor.execute("ANALYZE packages, tracking_events")
            cursor.execute("RELEASE SAVEPOINT benchmark_data")

            print("\nPASS: Synthetic tracking events and packages removed")
            if index_existed:
                print(f"PASS: Index {INDEX_NAME} recreated")
            else:
                print(f"PASS: Index {INDEX_NAME} created")

        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT benchmark_data")
            cursor.execute("RELEASE SAVEPOINT benchmark_data")
            raise

    print("PASS: Analytics validation and benchmark completed")


if __name__ == "__main__":
    main()
