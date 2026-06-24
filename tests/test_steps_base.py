"""Tests for the generic create-or-update loop in steps/base.py."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from laddel_migration.runner.context import RunContext
from laddel_migration.steps import base


class _FakeResource:
    name = "widgets"
    view = "widgets"
    mapping_table = "widget_mapping"
    key_column = "mapping_key"
    id_column = "target_widget_id"
    path = "/public-api/resources/widgets/v1.0"

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("explode"):
            raise ValueError("bad row")
        return {"name": row["name"]}

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {"mapping_key": row["mapping_key"], "target_widget_id": target_id}


class _FakeClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[object, dict[str, Any]]] = []

    def create(self, path: str, payload: dict[str, Any], **kw: object) -> dict[str, Any]:
        self.created.append(payload)
        return {"id": 555}

    def update(self, path: str, resource_id: object, payload: dict[str, Any], **kw: object):
        self.updated.append((resource_id, payload))
        return {"id": resource_id}


def _ctx(client: _FakeClient | None, *, dry_run: bool) -> RunContext:
    settings = SimpleNamespace(target_db="target")
    return RunContext(settings=settings, client=client, dry_run=dry_run)  # type: ignore[arg-type]


@pytest.fixture
def captured_mappings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    written: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        base, "write_mapping", lambda settings, table, values: written.append((table, values))
    )
    return written


def _patch_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(base, "fetch_view", lambda settings, view: rows)


def test_creates_unmapped_row_and_writes_mapping(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [{"mapping_key": "W|1", "source_label": "w1", "name": "One", "target_widget_id": None}],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _FakeResource())

    assert result.created == 1
    assert client.created == [{"name": "One"}]
    assert captured_mappings == [
        ("widget_mapping", {"mapping_key": "W|1", "target_widget_id": 555})
    ]


def test_updates_already_mapped_row(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [{"mapping_key": "W|2", "source_label": "w2", "name": "Two", "target_widget_id": 42}],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _FakeResource())

    assert result.updated == 1
    assert client.updated == [(42, {"name": "Two"})]
    assert captured_mappings == []  # no mapping write on update


def test_dry_run_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [{"mapping_key": "W|3", "source_label": "w3", "name": "Three", "target_widget_id": None}],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=True), _FakeResource())

    assert result.skipped == 1
    assert result.created == 0
    assert client.created == []
    assert captured_mappings == []


def test_row_error_is_collected_and_loop_continues(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [
            {
                "mapping_key": "W|4",
                "source_label": "bad",
                "name": "x",
                "explode": True,
                "target_widget_id": None,
            },
            {
                "mapping_key": "W|5",
                "source_label": "good",
                "name": "Five",
                "target_widget_id": None,
            },
        ],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _FakeResource())

    assert result.created == 1  # the good row still processed
    assert result.error_count == 1
    assert "bad: bad row" in result.errors[0]
