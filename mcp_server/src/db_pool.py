"""
Database connection pool management

Provides a connection pool for PostgreSQL database access to improve
performance and prevent connection exhaustion. Uses psycopg2's
SimpleConnectionPool for thread-safe connection management.
"""

import logging
from contextlib import contextmanager
from typing import Optional
import psycopg2
from psycopg2 import pool
from psycopg2.extensions import connection

from config import DB_CONN

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Singleton database connection pool

    Manages a pool of PostgreSQL connections for efficient reuse.
    Connections are automatically returned to the pool when done.
    """

    _instance: Optional["DatabasePool"] = None
    _pool: Optional[pool.SimpleConnectionPool] = None

    def __new__(cls):
        """Ensure singleton instance"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize connection pool (only once)"""
        if self._pool is None:
            self._initialize_pool()

    def _initialize_pool(self):
        """Create the connection pool"""
        if not DB_CONN:
            raise ValueError("DB_CONN configuration is required for database pool")

        try:
            # Create connection pool with 1-10 connections
            # minconn=1: Always keep at least 1 connection ready
            # maxconn=10: Maximum of 10 concurrent connections
            self._pool = pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=DB_CONN,
            )
            logger.info("✓ Database connection pool initialized (1-10 connections)")
        except psycopg2.Error as e:
            logger.error(f"Failed to initialize database pool: {e}")
            raise

    def get_connection(self) -> connection:
        """
        Get a connection from the pool

        Returns:
            psycopg2 connection object

        Raises:
            psycopg2.Error: If pool is exhausted or connection fails
        """
        if self._pool is None:
            self._initialize_pool()

        try:
            conn = self._pool.getconn()
            logger.debug("Retrieved connection from pool")
            return conn
        except psycopg2.Error as e:
            logger.error(f"Failed to get connection from pool: {e}")
            raise

    def return_connection(self, conn: connection):
        """
        Return a connection to the pool

        Args:
            conn: Connection to return

        Note:
            Should be called in finally block to ensure cleanup
        """
        if self._pool is not None and conn is not None:
            self._pool.putconn(conn)
            logger.debug("Returned connection to pool")

    def close_all(self):
        """
        Close all connections in the pool

        Should be called on application shutdown
        """
        if self._pool is not None:
            self._pool.closeall()
            logger.info("Database connection pool closed")
            self._pool = None


# Global database pool instance
_db_pool: Optional[DatabasePool] = None


def get_db_pool() -> DatabasePool:
    """
    Get or create the global database pool

    Returns:
        DatabasePool singleton instance
    """
    global _db_pool
    if _db_pool is None:
        _db_pool = DatabasePool()
    return _db_pool


@contextmanager
def get_db_connection():
    """
    Context manager for database connections

    Automatically gets connection from pool and returns it when done.
    Handles errors and ensures connection is always returned.

    Usage:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT ...")
                results = cursor.fetchall()

    Yields:
        psycopg2 connection object

    Example:
        >>> with get_db_connection() as conn:
        ...     with conn.cursor() as cursor:
        ...         cursor.execute("SELECT COUNT(*) FROM users")
        ...         count = cursor.fetchone()[0]
    """
    db_pool = get_db_pool()
    conn = None

    try:
        conn = db_pool.get_connection()
        yield conn
        # Commit if no exception occurred
        conn.commit()
    except Exception as e:
        # Rollback on error
        if conn is not None:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        # Always return connection to pool
        if conn is not None:
            db_pool.return_connection(conn)
