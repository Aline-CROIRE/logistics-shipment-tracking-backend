
# Logistics Shipment Tracking Backend

A Python backend service developed for the PBEM04 Database Fundamentals lab. It demonstrates relational database design, transactional tracking updates, Redis caching, MongoDB event logging, analytical SQL, and query optimization.

## Features

- Normalized PostgreSQL schema with customers, shipments, packages, locations, and tracking events.
- Atomic package-status updates and tracking-event insertion using psycopg2 transactions.
- Redis caching of the latest package status.
- MongoDB storage for scanner-event documents.
- PostgreSQL JSONB metadata.
- Tracking-history analytics using CTEs, `LAG()`, and `ROW_NUMBER()`.
- Query performance benchmarking with `EXPLAIN ANALYZE` and a composite index.
- Automated tests, Ruff linting, and Black formatting.

## Technology Stack

Python, PostgreSQL 16, Redis 7, MongoDB 7, Docker Compose, psycopg2, redis-py, pymongo, pytest, Ruff, and Black.

## Database Design

| Table | Purpose |
|---|---|
| `customers` | Customer records |
| `shipments` | Shipments associated with customers |
| `packages` | Packages associated with shipments and their current status |
| `locations` | Locations where scans occur |
| `tracking_events` | Historical package scans, timestamps, and scanner metadata |

Primary keys, foreign keys, unique constraints, and status checks support data integrity. JSONB columns store flexible package and scanner metadata.

## Setup

### Prerequisites

Install Git, Python, Docker Desktop, and Docker Compose.

### 1. Install dependencies

Run from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set a local PostgreSQL password. Do not commit `.env` or share its secrets.

### 3. Start the database services

```powershell
docker compose up -d --wait
docker compose ps
```

### 4. Create the schema and load sample data

```powershell
Get-Content sql/schema.sql -Raw |
    docker compose exec -T postgres psql -U logistics_user -d logistics

Get-Content sql/seed.sql -Raw |
    docker compose exec -T postgres psql -U logistics_user -d logistics
```

These commands use the default database and username configured in `docker-compose.yml`.

## Verification and Tests

Run the automated tests:

```powershell
python -m pytest -v
```

Check code quality:

```powershell
python -m ruff check .
python -m black --check .
```

Verify the local Redis and MongoDB integrations:

```powershell
python scripts/verify_external_stores.py
```

Run the end-to-end tracking scan verification:

```powershell
python scripts/verify_end_to_end_scan.py
```

The verification scripts require running local services and the expected database setup.

## Analytics and Optimization

- `sql/analytics.sql` contains tracking-history queries, elapsed-time calculations, and latest-event queries.
- `sql/indexes.sql` defines a composite index on `(package_id, event_timestamp)`.
- `scripts/benchmark_tracking_queries.py` measures query plans before and after indexing using synthetic data.

In the recorded benchmark, query execution time decreased from **20.116 ms to 0.421 ms**, an observed reduction of **97.91%**. These figures are from one local synthetic workload, not a production performance guarantee.

See [the performance report](docs/performance-report.md) for the methodology and limitations.

**Important:** The benchmark creates and retains the composite index. It refuses to run if that index already exists. Do not rerun it against the same database without deliberately resetting the benchmark conditions, and never run it on production.

## Project Structure

```text
app/
  database.py
  external_stores.py
  services/
    tracking_service.py
    tracking_integration.py
sql/
  schema.sql
  seed.sql
  analytics.sql
  indexes.sql
scripts/
  verify_external_stores.py
  verify_end_to_end_scan.py
  benchmark_tracking_queries.py
tests/
  test_database.py
  test_tracking_integration.py
  test_tracking_service.py
docs/
  performance-report.md
.env.example
docker-compose.yml
requirements.txt
README.md
```

## Limitations

- The current project provides a Python service layer, not a demonstrated public HTTP API.
- PostgreSQL commits before Redis and MongoDB writes. External-store failures do not roll back the committed PostgreSQL transaction.
- Benchmark results depend on the workload, environment, and PostgreSQL query planner.

## Performance Report

See [docs/performance-report.md](docs/performance-report.md).
