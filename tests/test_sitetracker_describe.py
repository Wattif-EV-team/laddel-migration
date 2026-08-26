"""Unit tests for the pure SiteTracker describe helpers (no live API calls)."""

from __future__ import annotations

from laddel_migration.clients.sitetracker_describe import (
    diff_describes,
    field_map,
    picklist_values,
)


def _field(
    name: str,
    type_: str = "string",
    label: str | None = None,
    picklist: list[str] | None = None,
) -> dict:
    field: dict = {"name": name, "type": type_, "label": label or name}
    if picklist is not None:
        field["picklistValues"] = [{"value": v, "active": True} for v in picklist]
    return field


def _describe(fields: list[dict]) -> dict:
    return {"fields": fields}


def test_field_map_indexes_by_name() -> None:
    describe = _describe([_field("Id"), _field("Name")])

    assert set(field_map(describe)) == {"Id", "Name"}
    assert field_map(describe)["Name"]["type"] == "string"


def test_picklist_values_returns_only_active() -> None:
    field = {
        "picklistValues": [
            {"value": "A", "active": True},
            {"value": "B", "active": False},
            {"value": "C"},  # active defaults to True when absent
        ]
    }

    assert picklist_values(field) == ["A", "C"]


def test_diff_describes_detects_new_and_removed_fields() -> None:
    old = _describe([_field("Id"), _field("Name"), _field("Legacy__c")])
    new = _describe([_field("Id"), _field("Name"), _field("New__c")])

    diff = diff_describes(old, new)

    assert diff.new_fields == ["New__c"]
    assert diff.removed_fields == ["Legacy__c"]
    assert diff.changed_fields == []
    assert diff.has_changes


def test_diff_describes_detects_type_label_and_picklist_changes() -> None:
    old = _describe(
        [
            _field("Status__c", type_="picklist", picklist=["Open", "Closed"]),
            _field("Amount__c", type_="double", label="Amount"),
        ]
    )
    new = _describe(
        [
            _field("Status__c", type_="picklist", picklist=["Open", "Closed", "Pending"]),
            _field("Amount__c", type_="currency", label="Total Amount"),
        ]
    )

    diff = diff_describes(old, new)

    assert diff.new_fields == []
    assert diff.removed_fields == []
    changed_by_name = {c.name: c for c in diff.changed_fields}
    assert changed_by_name["Status__c"].picklist_added == ["Pending"]
    assert changed_by_name["Status__c"].picklist_removed == []
    assert not changed_by_name["Status__c"].type_changed
    assert changed_by_name["Amount__c"].type_changed
    assert changed_by_name["Amount__c"].old_type == "double"
    assert changed_by_name["Amount__c"].new_type == "currency"
    assert changed_by_name["Amount__c"].label_changed


def test_diff_describes_no_changes_reports_empty() -> None:
    describe = _describe([_field("Id"), _field("Name")])

    diff = diff_describes(describe, describe)

    assert not diff.has_changes
