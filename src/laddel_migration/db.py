"""MySQL connectivity helpers."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pymysql
from pymysql.constants import CLIENT

from .config import DatabaseSettings
from .logging import get_logger

logger = get_logger(__name__)


@contextlib.contextmanager
def connect(
    settings: DatabaseSettings,
    *,
    multi_statements: bool = False,
) -> Iterator[pymysql.connections.Connection]:
    """Open a MySQL connection as a context manager.

    Parameters
    ----------
    multi_statements:
        When ``True`` allow several ``;``-separated statements per ``execute``
        call. Needed to run a DDL file that issues ``DROP VIEW`` followed by
        ``CREATE VIEW`` in a single batch.
    """
    conn = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        connect_timeout=10,
        client_flag=CLIENT.MULTI_STATEMENTS if multi_statements else 0,
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


def run_query(
    settings: DatabaseSettings,
    sql: str,
) -> tuple[list[str], list[tuple[object, ...]]]:
    """Run a read query and return ``(columns, rows)``.

    Intended for ad-hoc inspection of the source/target databases.
    """
    with connect(settings) as conn, conn.cursor() as cursor:
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
    return columns, list(rows)


def execute_script(settings: DatabaseSettings, sql_text: str) -> None:
    """Execute a multi-statement SQL script (e.g. a single DDL file).

    All ``;``-separated statements in ``sql_text`` run in one batch; every
    result set produced is drained so the connection is left ready for reuse.
    DDL in MySQL is auto-committed, but we commit explicitly for safety.
    """
    with connect(settings, multi_statements=True) as conn, conn.cursor() as cursor:
        cursor.execute(sql_text)
        # Drain any result sets so the cursor/connection is left clean.
        while cursor.nextset():
            pass
        conn.commit()
