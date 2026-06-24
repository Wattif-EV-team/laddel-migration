"""Tests for the mapping-write helper's atomic / hard-halt behaviour."""

from __future__ import annotations

import contextlib

import pytest

from laddel_migration import db
from laddel_migration.config import DatabaseSettings

_SETTINGS = DatabaseSettings(host="h", port=3306, user="u", password="p", database="target")


class _FakeCursor:
    def __init__(self, *, rowcount: int, raise_on_execute: bool = False) -> None:
        self.rowcount = rowcount
        self._raise = raise_on_execute
        self.executed: tuple[str, tuple[object, ...]] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        if self._raise:
            raise RuntimeError("connection reset")
        self.executed = (sql, params or ())


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
        {"mapping_key": "Laddel|Facility|7", "target_partner_id": 1007},
    )

    assert conn.committed is True
    assert cursor.executed is not None
    sql, params = cursor.executed
    assert sql == (
        "INSERT INTO `partner_mapping` (`mapping_key`, `target_partner_id`) VALUES (%s, %s)"
    )
    assert params == ("Laddel|Facility|7", 1007)


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
