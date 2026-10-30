"""Client factories for Redis and MongoDB."""

import os
from functools import lru_cache

import redis
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection

load_dotenv()


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    """Create and reuse a Redis client."""
    client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    return client


@lru_cache(maxsize=1)
def get_mongo_collection() -> Collection:
    """Create and reuse the MongoDB scanner-events collection."""
    client = MongoClient(
        os.getenv("MONGODB_URI", "mongodb://localhost:27017/"),
        serverSelectionTimeoutMS=3000,
    )
    database_name = os.getenv("MONGODB_DATABASE", "logistics")
    collection = client[database_name]["scanner_events"]

    # create_index does nothing if the index already exists.
    # Each PostgreSQL event is logged at most once. Documents without an
    # event_id, such as connectivity checks, are outside this rule.
    collection.create_index(
        "event_id",
        unique=True,
        partialFilterExpression={"event_id": {"$exists": True}},
    )
    # Supports reading one package's scanner events, newest first.
    collection.create_index(
        [("tracking_id", ASCENDING), ("event_timestamp", DESCENDING)]
    )
    return collection
