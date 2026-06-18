"""MySQL connectivity helpers."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pymysql

from .config import DatabaseSettings
from .logging import get_logger

logger = get_logger(__name__)


@contextlib.contextmanager
def connect(settings: DatabaseSettings) -> Iterator[pymysql.connections.Connection]:
    """Open a MySQL connection as a context manager."""
    conn = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        connect_timeout=10,
    )
    try:
        yield conn
    finally:
        conn.close()


def check_connection(settings: DatabaseSettings) -> bool:
    """Return ``True`` if a ``SELECT 1`` succeeds against the database."""
    logger.info("Checking connectivity to %s", settings.safe_dsn)
    with connect(settings) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
    return result is not None and result[0] == 1
