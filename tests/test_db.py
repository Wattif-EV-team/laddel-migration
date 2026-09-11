"""Tests for the view-read and mapping-write helpers in db.py."""

from __future__ import annotations

import contextlib

import pytest

from laddel_migration import db
from laddel_migration.config import DatabaseSettings

_SETTINGS = DatabaseSettings(host="h", port=3306, user="u", password="p", database="target")


class _FakeCursor:
    def __init__(
        self,
        *,
        rowcount: int,
        raise_on_execute: bool = False,
        description: tuple[tuple[str, ...], ...] | None = None,
        rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.rowcount = rowcount
        self._raise = raise_on_execute
        self.description = description
        self._rows = rows or []
        self.executed: tuple[str, tuple[object, ...] | None] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        if self._raise:
            raise RuntimeError("connection reset")
        self.executed = (sql, params)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


def _patch_connect(monkeypatch: pytest.MonkeyPatch, conn: _FakeConn) -> None:
    @contextlib.contextmanager
    def fake_connect(settings: DatabaseSettings, **kwargs: object):  # noqa: ANN003
        yield conn

    monkeypatch.setattr(db, "connect", fake_connect)


def test_write_mapping_commits_on_single_row(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(rowcount=1)
    conn = _FakeConn(cursor)
    _patch_connect(monkeypatch, conn)

    db.write_mapping(
        _SETTINGS,
        "partner_mapping",
        {"mapping_key": "Laddel|Customer|7", "target_partner_id": 1007},
    )

    assert conn.committed is True
    assert cursor.executed is not None
    sql, params = cursor.executed
    assert sql == (
        "INSERT INTO `partner_mapping` (`mapping_key`, `target_partner_id`) VALUES (%s, %s)"
    )
    assert params == ("Laddel|Customer|7", 1007)


def test_write_mapping_halts_when_zero_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn(_FakeCursor(rowcount=0))
    _patch_connect(monkeypatch, conn)

    with pytest.raises(SystemExit, match="affected 0 row"):
        db.write_mapping(_SETTINGS, "partner_mapping", {"mapping_key": "k"})

    assert conn.committed is False


def test_write_mapping_halts_on_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn(_FakeCursor(rowcount=1, raise_on_execute=True))
    _patch_connect(monkeypatch, conn)

    with pytest.raises(SystemExit, match="failed"):
        db.write_mapping(_SETTINGS, "partner_mapping", {"mapping_key": "k"})


def _view_cursor() -> _FakeCursor:
    return _FakeCursor(
        rowcount=1,
        description=(("mapping_key",), ("target_charge_point_id",)),
        rows=[("Laddel|Charger|1", 42)],
    )


def test_fetch_view_reads_the_whole_view_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _view_cursor()
    _patch_connect(monkeypatch, _FakeConn(cursor))

    rows = db.fetch_view(_SETTINGS, "charge_points")

    assert cursor.executed == ("SELECT * FROM `charge_points`", None)
    assert rows == [{"mapping_key": "Laddel|Charger|1", "target_charge_point_id": 42}]


def test_fetch_view_appends_where_and_binds_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """A multi-pass step filters the view; the values must be bound, not inlined."""
    cursor = _view_cursor()
    _patch_connect(monkeypatch, _FakeConn(cursor))

    db.fetch_view(
        _SETTINGS,
        "charge_points",
        where="`communicationMode` = %s",
        params=("direct_ocpp",),
    )

    assert cursor.executed == (
        "SELECT * FROM `charge_points` WHERE `communicationMode` = %s",
        ("direct_ocpp",),
    )


class _TxCursor:
    """Records the full statement sequence, including ``executemany`` batches."""

    def __init__(self, *, deleted: int = 7, fail_on_insert: bool = False) -> None:
        self.rowcount = deleted
        self._fail = fail_on_insert
        self.statements: list[str] = []
        self.batches: list[list[tuple[object, ...]]] = []

    def __enter__(self) -> _TxCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.statements.append(sql)

    def executemany(self, sql: str, rows: list[tuple[object, ...]]) -> None:
        if self._fail:
            raise RuntimeError("server went away mid-load")
        self.statements.append(sql)
        self.batches.append(list(rows))


class _TxConn:
    def __init__(self, cursor: _TxCursor) -> None:
        self._cursor = cursor
        self.begun = False
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _TxCursor:
        return self._cursor

    def begin(self) -> None:
        self.begun = True

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def _replace_setup(
    monkeypatch: pytest.MonkeyPatch, **cursor_kwargs: object
) -> tuple[_TxCursor, _TxConn]:
    cursor = _TxCursor(**cursor_kwargs)  # type: ignore[arg-type]
    conn = _TxConn(cursor)
    _patch_connect(monkeypatch, conn)  # type: ignore[arg-type]
    return cursor, conn


def test_replace_table_rows_deletes_then_inserts_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor, conn = _replace_setup(monkeypatch)

    written = db.replace_table_rows(
        _SETTINGS,
        "emabler_charger",
        ("emabler_id", "charger_id"),
        [(1, "A"), (2, "B")],
    )

    assert written == 2
    assert conn.begun and conn.committed and not conn.rolled_back
    assert cursor.statements == [
        "DELETE FROM `emabler_charger`",
        "INSERT INTO `emabler_charger` (`emabler_id`, `charger_id`) VALUES (%s, %s)",
    ]
    assert cursor.batches == [[(1, "A"), (2, "B")]]


def test_replace_table_rows_never_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRUNCATE is DDL in MySQL and implicitly commits, discarding the old snapshot.

    Using it would make the rollback guarantee below impossible, so the choice of
    DELETE is load-bearing rather than stylistic.
    """
    cursor, _ = _replace_setup(monkeypatch)

    db.replace_table_rows(_SETTINGS, "emabler_charger", ("a",), [(1,)])

    assert not any("TRUNCATE" in sql.upper() for sql in cursor.statements)


def test_replace_table_rows_rolls_back_and_reraises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed reload must leave the previous snapshot intact."""
    _, conn = _replace_setup(monkeypatch, fail_on_insert=True)

    with pytest.raises(RuntimeError, match="server went away"):
        db.replace_table_rows(_SETTINGS, "emabler_charger", ("a",), [(1,)])

    assert conn.rolled_back
    assert not conn.committed


def test_replace_table_rows_splits_large_loads_into_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor, _ = _replace_setup(monkeypatch)

    written = db.replace_table_rows(
        _SETTINGS,
        "emabler_charger",
        ("a",),
        [(i,) for i in range(5)],
        batch_size=2,
    )

    assert written == 5
    assert [len(batch) for batch in cursor.batches] == [2, 2, 1]


def test_replace_table_rows_handles_an_empty_load(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor, conn = _replace_setup(monkeypatch)

    assert db.replace_table_rows(_SETTINGS, "emabler_charger", ("a",), []) == 0
    assert cursor.statements == ["DELETE FROM `emabler_charger`"]
    assert conn.committed
