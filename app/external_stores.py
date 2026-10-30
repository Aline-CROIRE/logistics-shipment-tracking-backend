"""Client factories for Redis and MongoDB."""

import os
from functools import lru_cache

import redis
from dotenv import load_dotenv
from pymongo import MongoClient
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
    return client[database_name]["scanner_events"]
