# Performance Report: Tracking History Index

This report measures how the composite index `idx_tracking_events_package_timestamp` on `tracking_events (package_id, event_timestamp)` affects the queries that reconstruct a package's tracking history.

## 1. Summary

| Query | Before index | After index | Improvement |
|---|---:|---:|---:|
| Event lookup by `package_id` | 10.2 – 12.0 ms | 0.09 – 0.11 ms | ≈ 99% |
| Tracking history (CTE + `LAG()`) | 15.2 – 18.5 ms | 0.41 – 0.47 ms | ≈ 97% |
| Data pages accessed (both queries) | ≈ 7,360 | 75 – 80 | ≈ 99% fewer |

Ranges are the median execution times from three separate benchmark runs. With the index, PostgreSQL reads only the target package's events instead of scanning every row in `tracking_events`.

## 2. Test Environment

| Item | Value |
|---|---|
| Database | PostgreSQL 16.15 (Docker image `postgres:16`) |
| Host | Docker Desktop on Windows 11, Intel Core i7-10510U, 8 CPUs, 16 GB RAM |
| Settings | Defaults: `shared_buffers` 128 MB, `work_mem` 4 MB, `max_parallel_workers_per_gather` 2 |

## 3. Method

The benchmark is `scripts/benchmark_tracking_queries.py`. For each run it:

1. Inserts 1,000 synthetic packages and 200,000 tracking events (200 events per package) inside a single transaction.
2. Selects the package in the middle of the id range (package 500) as the target.
3. Runs `ANALYZE` on `packages` and `tracking_events` so the planner has current statistics.
4. For each query, executes one untimed warm-up run, then five `EXPLAIN (ANALYZE, BUFFERS)` runs, and records the median execution time.
5. Creates the composite index, refreshes statistics, and repeats step 4.
6. Deletes the synthetic data. If the index existed before the run, it is dropped at the start and recreated, so the schema ends unchanged. Any failure rolls back the whole transaction.

The script was run three times to show run-to-run variation.

## 4. Queries Measured

**Event lookup** retrieves one package's events in time order:

```sql
SELECT event_id, event_timestamp
FROM tracking_events
WHERE package_id = %s
ORDER BY event_timestamp, event_id;
```

**Tracking history** is the main analytics query. A CTE joins packages, events and locations, and `LAG()` returns the previous scan time so the elapsed minutes between scans can be calculated:

```sql
WITH package_history AS (
    SELECT p.package_id, p.tracking_id, event.event_id, event.status,
           event.event_timestamp, location.city, location.country,
           LAG(event.event_timestamp) OVER (
               PARTITION BY p.package_id
               ORDER BY event.event_timestamp, event.event_id
           ) AS previous_event_timestamp
    FROM packages AS p
    JOIN tracking_events AS event ON event.package_id = p.package_id
    JOIN locations AS location ON location.location_id = event.location_id
    WHERE p.tracking_id = %s
)
SELECT tracking_id, status, city, country, event_timestamp,
       previous_event_timestamp,
       ROUND((EXTRACT(EPOCH FROM (event_timestamp - previous_event_timestamp)) / 60)::numeric, 2)
           AS minutes_since_previous_scan
FROM package_history
ORDER BY event_timestamp, event_id;
```

Both queries return 200 rows.

## 5. Results

Median execution time of five measured runs, in milliseconds:

| Run | Lookup before | Lookup after | Improvement | History before | History after | Improvement |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 10.216 | 0.107 | 98.95% | 18.510 | 0.465 | 97.49% |
| 2 | 11.422 | 0.086 | 99.25% | 15.194 | 0.419 | 97.24% |
| 3 | 12.016 | 0.091 | 99.24% | 16.694 | 0.406 | 97.57% |

Every run showed the same plan change and an improvement of at least 97%. Planning time stayed between 0.03 and 0.25 ms in all cases.

## 6. Query Plan Analysis

The plans below are from run 2, abridged to the key lines.

### 6.1 Event lookup

**Before the index**, PostgreSQL scans the whole table in parallel and discards almost every row:

```text
Gather Merge (actual time=7.992..9.835 rows=200 loops=1)
  Buffers: shared hit=7363
  ->  Sort  Sort Key: event_timestamp, event_id
        ->  Parallel Seq Scan on tracking_events (rows=67 loops=3)
              Filter: (package_id = 10556)
              Rows Removed by Filter: 66602
Execution Time: 9.863 ms
```

Three processes each removed about 66,600 rows, so roughly 199,800 rows were read and thrown away to find 200.

**After the index**, PostgreSQL goes straight to the package's entries:

```text
Incremental Sort (actual time=0.020..0.072 rows=200 loops=1)
  Sort Key: event_timestamp, event_id
  Presorted Key: event_timestamp
  Buffers: shared hit=75
  ->  Index Scan using idx_tracking_events_package_timestamp on tracking_events
        Index Cond: (package_id = 10556)
Execution Time: 0.084 ms
```

### 6.2 Tracking history (CTE + `LAG()`)

**Before the index**, the package is found quickly through `packages_tracking_id_key`, but its events are still found by scanning all of `tracking_events` and hash-joining the result:

```text
Sort (actual time=12.748..14.495 rows=200 loops=1)
  Buffers: shared hit=7357
  ->  WindowAgg
        ->  Gather Merge
              ->  Hash Join  Hash Cond: (event.package_id = p.package_id)
                    ->  Parallel Seq Scan on tracking_events event (rows=66669 loops=3)
                    ->  Index Scan using packages_tracking_id_key on packages p
Execution Time: 14.546 ms
```

**After the index**, PostgreSQL uses a nested loop: it finds the package, then reads only that package's events from the composite index:

```text
Sort (actual time=0.385..0.392 rows=200 loops=1)
  Buffers: shared hit=80
  ->  WindowAgg
        ->  Sort  Sort Key: p.package_id, event.event_timestamp, event.event_id
              ->  Nested Loop
                    ->  Index Scan using packages_tracking_id_key on packages p
                    ->  Index Scan using idx_tracking_events_package_timestamp on tracking_events event
                          Index Cond: (package_id = p.package_id)
Execution Time: 0.430 ms
```

### 6.3 Why the index helps

- **It filters on the first column.** Every history query asks for one package. With `package_id` as the leading column, PostgreSQL can jump straight to the 200 matching entries instead of reading the whole table. Pages accessed (`Buffers: shared hit`) fell from about 7,360 to 75–80.
- **It stores events in time order.** Within each `package_id`, entries are sorted by `event_timestamp`, so events come out of the index already in time order.
- **The column order matters.** An index on `(event_timestamp, package_id)` would not help here, because the query does not filter on a time range and the package's events would be spread across the whole index.

## 7. Observations

- **The small remaining sort.** The queries also order by `event_id` to break ties between identical timestamps. The index does not contain `event_id`, so PostgreSQL performs an incremental sort, sorting only within groups that share a timestamp. An index on `(package_id, event_timestamp, event_id)` would remove this step, but with 200 rows per package the saving is negligible. The two-column index required by the assignment was kept.
- **Statistics affect plan choice.** An earlier version of the benchmark ran `ANALYZE` only on `tracking_events`. The planner then assumed `packages` still held a handful of rows and chose a slower merge-join plan for the history query (about 18.8 ms even with the index). Analyzing both tables produced the nested-loop plan shown above.
- **The analytics report query is different.** Query 2 in `sql/analytics.sql` computes intervals for every package at once. It reads all events by design, so a per-package index cannot make it fast. The index targets the per-package history lookup, which is the operation a tracking service performs most often.

## 8. Trade-offs

- **Storage:** the index on 200,007 events was 6.2 MB, about 32 bytes per event.
- **Write cost:** every tracking scan inserts one event and must also update the index. This is a small cost per scan, accepted because history is read far more often than any single event is written.

## 9. Limitations

- The data is synthetic and uniform: 200 events per package, one location and one status. Real data would vary per package.
- Measurements were taken on one development machine with data already in memory. Absolute times vary between runs and machines; the plan change and page counts are the more reliable evidence.
- The before-index page count reflects the table's physical size on disk, which includes space left by earlier benchmark runs until it is reclaimed by `VACUUM`.

## 10. How to Reproduce

With the Docker services running, the schema and seed data loaded, and the virtual environment active:

```powershell
python scripts/benchmark_tracking_queries.py
```

The script prints timings and full query plans for both queries, before and after the index. It can be run repeatedly and leaves the data and schema unchanged.
