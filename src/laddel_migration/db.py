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
    logger.debug("SQL query @%s: %s", settings.database, sql)
    with connect(settings) as conn, conn.cursor() as cursor:
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
    return columns, list(rows)


def fetch_view(settings: DatabaseSettings, view: str) -> list[dict[str, object]]:
    """Return every row of ``view`` as a list of column-name -> value dicts.

    ``view`` is an internal, trusted identifier (a target view name), quoted
    with backticks. Used by create-or-update steps to read their payload rows.
    """
    columns, rows = run_query(settings, f"SELECT * FROM `{view}`")
    return [dict(zip(columns, row, strict=True)) for row in rows]


def write_mapping(
    settings: DatabaseSettings,
    table: str,
    values: dict[str, object],
) -> None:
    """Insert one mapping row, halting the whole run if it does not land.

    Mapping writes are a single ``INSERT`` into an initially-per-resource table.
    We verify exactly one row was affected and ``raise SystemExit`` otherwise —
    the dangerous moment is after a resource exists in the target but before its
    id is persisted, so a doubtful write must stop the pipeline rather than risk
    an orphaned resource. Callers MUST emit the ``MAPPING_RECORD`` breadcrumb
    (see :func:`laddel_migration.logging.mapping_breadcrumb`) before calling this.
    """
    columns = list(values)
    column_sql = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO `{table}` ({column_sql}) VALUES ({placeholders})"
    params = tuple(values[c] for c in columns)
    logger.debug("SQL mapping insert @%s: %s params=%s", settings.database, sql, params)

    try:
        with connect(settings) as conn, conn.cursor() as cursor:
            cursor.execute(sql, params)
            if cursor.rowcount != 1:
                raise SystemExit(
                    f"Mapping INSERT into `{table}` affected {cursor.rowcount} row(s) "
                    f"(expected 1); halting to prevent an orphaned target resource."
                )
            conn.commit()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure here is an integrity risk
        raise SystemExit(
            f"Mapping write to `{table}` failed ({values}): {exc} - "
            f"halting to prevent orphaned resources."
        ) from exc


def execute_script(settings: DatabaseSettings, sql_text: str) -> None:
    """Execute a multi-statement SQL script (e.g. a single DDL file).

    All ``;``-separated statements in ``sql_text`` run in one batch; every
    result set produced is drained so the connection is left ready for reuse.
    DDL in MySQL is auto-committed, but we commit explicitly for safety.
    """
    logger.debug("SQL script @%s (%d chars)", settings.database, len(sql_text))
    with connect(settings, multi_statements=True) as conn, conn.cursor() as cursor:
        cursor.execute(sql_text)
        # Drain any result sets so the cursor/connection is left clean.
        while cursor.nextset():
            pass
        conn.commit()
