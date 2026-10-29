"""Unit tests for Redis and MongoDB tracking integrations."""

import json

from app.services import tracking_integration


class FakeRedis:
    """In-memory stand-in for Redis."""

    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


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
