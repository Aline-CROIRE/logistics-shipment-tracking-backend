"""Verify live Redis and MongoDB connectivity and basic persistence."""

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Allow this script to import the app package from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.external_stores import get_mongo_collection, get_redis_client


def main() -> None:
    """Run non-destructive connectivity and persistence checks."""
    verification_id = uuid.uuid4().hex
    redis_key = f"verification:{verification_id}:status"
    redis_value = {
        "status": "In Transit",
        "location": "Kigali, Rwanda",
    }
    redis_client = None
    mongo_collection = None
    mongo_client = None

    try:
        redis_client = get_redis_client()
        redis_client.ping()
        print("PASS: Redis connection")

        mongo_collection = get_mongo_collection()
        mongo_client = mongo_collection.database.client
        mongo_client.admin.command("ping")
        print("PASS: MongoDB connection")

        redis_client.set(redis_key, json.dumps(redis_value))
        cached_value = redis_client.get(redis_key)
        assert cached_value is not None
        assert json.loads(cached_value) == redis_value
        print("PASS: Redis write and read")

        mongo_document = {
            "verification_id": verification_id,
            "purpose": "external_store_verification",
            "created_at": datetime.now(UTC),
        }
        mongo_collection.insert_one(mongo_document)

        stored_document = mongo_collection.find_one(
            {"verification_id": verification_id}
        )
        assert stored_document is not None
        assert stored_document["purpose"] == "external_store_verification"
        print("PASS: MongoDB insert and read")

    finally:
        if redis_client is not None:
            try:
                redis_client.delete(redis_key)
            finally:
                redis_client.close()

        if mongo_collection is not None:
            try:
                mongo_collection.delete_one({"verification_id": verification_id})
            finally:
                if mongo_client is not None:
                    mongo_client.close()


if __name__ == "__main__":
    main()
