"""Tests for Redis and MongoDB tracking integrations."""

import json
from uuid import uuid4

from app.database import get_db_connection
from app.services import tracking_integration


class FakeRedis:
    """In-memory stand-in for Redis."""

    def __init__(self):
        self.values = {}
        self.expiry_seconds = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.expiry_seconds[key] = ex


class FakeMongoCollection:
    """In-memory stand-in for a MongoDB collection."""

    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        self.documents.append(document)


def configure_successful_scan(monkeypatch, redis_client, mongo_collection):
    """Configure the PostgreSQL result and external-store dependencies."""
    monkeypatch.setattr(
        tracking_integration,
        "process_tracking_scan",
        lambda **kwargs: {
            "event_id": 101,
            "package_id": 5,
            "tracking_id": "TRK-TEST-100",
            "location_id": 2,
            "status": "In Transit",
            "scanner_id": "SCANNER-01",
        },
    )
    monkeypatch.setattr(
        tracking_integration,
        "_get_event_context",
        lambda event_id: {
            "event_timestamp": "2026-10-29T08:30:00+00:00",
            "location_name": "Kigali Distribution Centre",
            "city": "Kigali",
            "country": "Rwanda",
        },
    )
    monkeypatch.setattr(
        tracking_integration,
        "get_redis_client",
        lambda: redis_client,
    )
    monkeypatch.setattr(
        tracking_integration,
        "get_mongo_collection",
        lambda: mongo_collection,
    )


def test_scan_updates_redis_and_logs_mongodb_event(monkeypatch):
    """A successful scan updates both secondary stores."""
    redis_client = FakeRedis()
    mongo_collection = FakeMongoCollection()
    configure_successful_scan(monkeypatch, redis_client, mongo_collection)

    result = tracking_integration.process_and_publish_tracking_scan(
        package_id=5,
        location_id=2,
        status="In Transit",
        scanner_id="SCANNER-01",
        scan_metadata={"device": "handheld"},
    )

    cache_key = "package:TRK-TEST-100:status"
    assert json.loads(redis_client.values[cache_key]) == {
        "status": "In Transit",
        "location": "Kigali, Rwanda",
    }
    assert (
        redis_client.expiry_seconds[cache_key]
        == tracking_integration.STATUS_CACHE_TTL_SECONDS
    )

    assert len(mongo_collection.documents) == 1
    document = mongo_collection.documents[0]
    assert document["event_id"] == 101
    assert document["scanner_id"] == "SCANNER-01"
    assert document["scan_metadata"] == {"device": "handheld"}
    assert document["location_name"] == "Kigali Distribution Centre"
    assert result["integration_errors"] == {}


def test_redis_failure_does_not_prevent_mongodb_logging(monkeypatch):
    """MongoDB is still attempted when Redis is unavailable."""
    mongo_collection = FakeMongoCollection()
    configure_successful_scan(monkeypatch, None, mongo_collection)

    def fail_redis():
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(
        tracking_integration,
        "get_redis_client",
        fail_redis,
    )

    result = tracking_integration.process_and_publish_tracking_scan(
        package_id=5,
        location_id=2,
        status="In Transit",
    )

    assert "redis" in result["integration_errors"]
    assert len(mongo_collection.documents) == 1


def test_mongodb_failure_does_not_undo_redis_update(monkeypatch):
    """Redis is still updated when MongoDB logging fails."""
    redis_client = FakeRedis()
    configure_successful_scan(monkeypatch, redis_client, None)

    def fail_mongodb():
        raise ConnectionError("MongoDB unavailable")

    monkeypatch.setattr(
        tracking_integration,
        "get_mongo_collection",
        fail_mongodb,
    )

    result = tracking_integration.process_and_publish_tracking_scan(
        package_id=5,
        location_id=2,
        status="In Transit",
    )

    assert "package:TRK-TEST-100:status" in redis_client.values
    assert "mongodb" in result["integration_errors"]


def fail_database_lookup(tracking_id):
    raise AssertionError("PostgreSQL should not be queried on a cache hit")


def test_status_lookup_returns_cached_value(monkeypatch):
    """A cache hit is answered by Redis without querying PostgreSQL."""
    redis_client = FakeRedis()
    redis_client.set(
        "package:TRK-TEST-100:status",
        json.dumps({"status": "Delayed", "location": "Huye, Rwanda"}),
    )
    monkeypatch.setattr(tracking_integration, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(
        tracking_integration, "_load_status_from_database", fail_database_lookup
    )

    assert tracking_integration.get_package_status("TRK-TEST-100") == {
        "status": "Delayed",
        "location": "Huye, Rwanda",
        "source": "redis",
    }


def test_status_lookup_miss_loads_postgresql_and_caches(monkeypatch):
    """A cache miss reads PostgreSQL and stores the value with an expiry."""
    redis_client = FakeRedis()
    monkeypatch.setattr(tracking_integration, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(
        tracking_integration,
        "_load_status_from_database",
        lambda tracking_id: {"status": "In Transit", "location": "Kigali, Rwanda"},
    )

    result = tracking_integration.get_package_status("TRK-TEST-100")

    assert result == {
        "status": "In Transit",
        "location": "Kigali, Rwanda",
        "source": "postgresql",
    }
    cache_key = "package:TRK-TEST-100:status"
    assert json.loads(redis_client.values[cache_key]) == {
        "status": "In Transit",
        "location": "Kigali, Rwanda",
    }
    assert (
        redis_client.expiry_seconds[cache_key]
        == tracking_integration.STATUS_CACHE_TTL_SECONDS
    )


def test_status_lookup_uses_postgresql_when_redis_is_down(monkeypatch):
    """Redis outages do not prevent status lookups."""

    def fail_redis():
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(tracking_integration, "get_redis_client", fail_redis)
    monkeypatch.setattr(
        tracking_integration,
        "_load_status_from_database",
        lambda tracking_id: {"status": "Delivered", "location": "Kigali, Rwanda"},
    )

    result = tracking_integration.get_package_status("TRK-TEST-100")

    assert result["status"] == "Delivered"
    assert result["source"] == "postgresql"


def test_status_lookup_for_unknown_package_returns_none(monkeypatch):
    """Unknown packages return None and are not cached."""
    redis_client = FakeRedis()
    monkeypatch.setattr(tracking_integration, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(
        tracking_integration, "_load_status_from_database", lambda tracking_id: None
    )

    assert tracking_integration.get_package_status("TRK-MISSING") is None
    assert redis_client.values == {}


def test_database_status_uses_latest_event_location():
    """The PostgreSQL fallback reports the location of the newest scan."""
    tracking_id = f"TRK-TEST-{uuid4().hex}"

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO packages (shipment_id, tracking_id, current_status)
            SELECT MIN(shipment_id), %s, 'In Transit'
            FROM shipments
            RETURNING package_id
            """,
            (tracking_id,),
        )
        package_id = cursor.fetchone()[0]

    try:
        assert tracking_integration._load_status_from_database(tracking_id) == {
            "status": "In Transit",
            "location": None,
        }

        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tracking_events (
                    package_id, location_id, status, event_timestamp
                )
                SELECT %s, location_id, 'In Transit', %s::timestamptz
                FROM locations
                WHERE location_name = %s
                """,
                (
                    package_id,
                    "2026-01-01 08:00:00+00",
                    "Kigali Distribution Centre",
                ),
            )
            cursor.execute(
                """
                INSERT INTO tracking_events (
                    package_id, location_id, status, event_timestamp
                )
                SELECT %s, location_id, 'In Transit', %s::timestamptz
                FROM locations
                WHERE location_name = %s
                """,
                (package_id, "2026-01-01 12:00:00+00", "Huye Transit Hub"),
            )

        assert tracking_integration._load_status_from_database(tracking_id) == {
            "status": "In Transit",
            "location": "Huye, Rwanda",
        }
        assert tracking_integration._load_status_from_database("TRK-MISSING") is None
    finally:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM tracking_events WHERE package_id = %s",
                (package_id,),
            )
            cursor.execute(
                "DELETE FROM packages WHERE package_id = %s",
                (package_id,),
            )
