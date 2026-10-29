"""PostgreSQL connection pooling and connection lifecycle management."""

import os
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2.pool import SimpleConnectionPool

load_dotenv()

_pool: SimpleConnectionPool | None = None


def get_connection_pool() -> SimpleConnectionPool:
    """Create the connection pool once and reuse it."""
    global _pool

    if _pool is None:
        _pool = SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            dbname=os.getenv("POSTGRES_DB", "logistics"),
            user=os.getenv("POSTGRES_USER", "logistics_user"),
            password=os.getenv("POSTGRES_PASSWORD"),
        )

    return _pool


@contextmanager
def get_db_connection():
    """Borrow a connection and safely return it to the pool."""
    pool = get_connection_pool()
    connection = pool.getconn()

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        pool.putconn(connection)


def close_connection_pool() -> None:
    """Close all pooled connections when the application shuts down."""
    global _pool

    if _pool is not None:
        _pool.closeall()
        _pool = None
