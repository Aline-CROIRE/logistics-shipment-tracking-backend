
# Logistics Shipment Tracking Backend

A Python database-backed logistics service built for the PBEM04 Database Fundamentals lab.

## Objectives

- Design a normalized PostgreSQL database.
- Process tracking scans using atomic transactions with psycopg2.
- Cache package statuses using Redis.
- Store raw scanner events in MongoDB.
- Use PostgreSQL JSONB for flexible package metadata.
- Reconstruct tracking history using CTEs and window functions.
- Measure query performance using EXPLAIN ANALYZE and composite indexes.

## Technology Stack

- Python 3.11+
- PostgreSQL
- Redis
- MongoDB
- Docker Compose
- psycopg2, redis-py, pymongo
- pytest, Ruff, Black

## Project Status

Initial repository setup. Implementation will be developed incrementally through feature branches and pull requests.

## License

Educational project for the PBEM04 Database Fundamentals module.
