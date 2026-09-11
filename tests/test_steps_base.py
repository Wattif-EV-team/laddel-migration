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
    target_system = "ampeco"

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
    """Return the same rows for every read, whatever the pass filter is."""
    monkeypatch.setattr(
        base,
        "fetch_view",
        lambda settings, view, *, where=None, params=(): rows,
    )


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


class _SkippingResource(_FakeResource):
    """A resource that declares the optional readiness hook."""

    def skip_reason(self, row: dict[str, Any]) -> str | None:
        if row.get("parent_id") is None:
            return "parent not migrated yet"
        return None


def test_skip_reason_holds_row_back_without_calling_the_api(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [
            {
                "mapping_key": "W|6",
                "source_label": "not-ready",
                "name": "Six",
                "parent_id": None,
                "target_widget_id": None,
            },
            {
                "mapping_key": "W|7",
                "source_label": "ready",
                "name": "Seven",
                "parent_id": 9,
                "target_widget_id": None,
            },
        ],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _SkippingResource())

    assert result.total == 2
    assert result.skipped == 1
    assert result.created == 1
    assert result.error_count == 0
    assert client.created == [{"name": "Seven"}]
    assert captured_mappings == [
        ("widget_mapping", {"mapping_key": "W|7", "target_widget_id": 555})
    ]


def test_resources_without_the_hook_are_unaffected(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [{"mapping_key": "W|8", "source_label": "w8", "name": "Eight", "target_widget_id": None}],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _FakeResource())

    assert result.skipped == 0
    assert result.created == 1


class _MultiPassResource(_FakeResource):
    """A resource that splits its view into two filtered passes."""

    def passes(self) -> tuple[base.Pass, ...]:
        return (
            base.Pass(name="first", where="`kind` = %s", params=("a",)),
            base.Pass(name="second", where="`kind` = %s", params=("b",)),
        )


def test_every_pass_re_reads_the_view_with_its_own_filter(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    reads: list[tuple[str | None, tuple[object, ...]]] = []
    rows_by_kind = {
        "a": [{"mapping_key": "W|a", "source_label": "wa", "name": "A", "target_widget_id": None}],
        "b": [{"mapping_key": "W|b", "source_label": "wb", "name": "B", "target_widget_id": None}],
    }

    def fake_fetch(settings: object, view: str, *, where: str | None = None, params: tuple = ()):
        reads.append((where, params))
        return rows_by_kind[str(params[0])]

    monkeypatch.setattr(base, "fetch_view", fake_fetch)
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _MultiPassResource())

    assert reads == [("`kind` = %s", ("a",)), ("`kind` = %s", ("b",))]
    assert result.total == 2  # tallies accumulate across passes
    assert result.created == 2
    assert client.created == [{"name": "A"}, {"name": "B"}]


class _AdoptingResource(_FakeResource):
    """A resource that can find its rows in the target system by natural key."""

    def lookup_existing(self, ctx: RunContext, row: dict[str, Any]) -> base.Adoption | None:
        if row.get("natural_key") == "known":
            return base.Adoption(target_id=99, matched_by="natural_key", snapshot={"id": 99})
        return None

    def adoption_values(self, row: dict[str, Any], adoption: base.Adoption) -> dict[str, object]:
        return {"matched_by": adoption.matched_by}


def test_unmapped_row_found_in_target_is_adopted_not_recreated(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [
            {
                "mapping_key": "W|9",
                "source_label": "known",
                "name": "Nine",
                "natural_key": "known",
                "target_widget_id": None,
            },
            {
                "mapping_key": "W|10",
                "source_label": "new",
                "name": "Ten",
                "natural_key": "unknown",
                "target_widget_id": None,
            },
        ],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _AdoptingResource())

    assert result.adopted == 1
    assert result.created == 1
    assert client.updated == [(99, {"name": "Nine"})]  # patched, not re-created
    assert client.created == [{"name": "Ten"}]
    # Both outcomes are recorded, so the audit trail covers fresh creates too.
    assert captured_mappings == [
        (
            "widget_mapping",
            {"mapping_key": "W|9", "target_widget_id": 99, "matched_by": "natural_key"},
        ),
        (
            "widget_mapping",
            {"mapping_key": "W|10", "target_widget_id": 555, "matched_by": "created"},
        ),
    ]


def test_lookup_is_not_attempted_in_a_dry_run(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [
            {
                "mapping_key": "W|11",
                "source_label": "known",
                "name": "Eleven",
                "natural_key": "known",
                "target_widget_id": None,
            }
        ],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=True), _AdoptingResource())

    assert result.skipped == 1
    assert result.adopted == 0
    assert client.updated == []
    assert captured_mappings == []


def test_already_mapped_row_never_reaches_the_lookup(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(
        monkeypatch,
        [
            {
                "mapping_key": "W|12",
                "source_label": "mapped",
                "name": "Twelve",
                "natural_key": "known",
                "target_widget_id": 7,
            }
        ],
    )
    client = _FakeClient()

    result = base.run_create_or_update(_ctx(client, dry_run=False), _AdoptingResource())

    assert result.updated == 1
    assert result.adopted == 0
    assert client.updated == [(7, {"name": "Twelve"})]
    assert captured_mappings == []
