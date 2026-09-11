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
