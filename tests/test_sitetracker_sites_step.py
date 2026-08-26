"""Test the SiteTracker Site payload builder, mapping description, and the
lookup-before-create ``run()`` loop.

Site API field names contain underscores (`sitetracker__Site_Status__c`,
`EV_Connector_Type__c`, ...) that are NOT nesting, so the payload must stay
flat. The compound geolocation fields (`sitetracker__Location__Latitude__s` /
`..._Longitude__s`) are floats and must be coerced accordingly.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from laddel_migration.runner.context import RunContext
from laddel_migration.steps.sitetracker import sites
from laddel_migration.steps.sitetracker.sites import SiteTrackerSitesResource


def _view_row() -> dict[str, Any]:
    """A row shaped like `target.sitetracker_sites` output."""
    return {
        "mapping_key": "Laddel|Facility|42",
        "source_label": "Acme Housing (fac=42)",
        "target_sf_site_id": None,
        "Site_ID__c": "W047L0042",
        "Name": "Acme Housing",
        "sitetracker__Site_Status__c": "Under Migration",
        "sitetracker__Site_Type__c": "HOUSING_ASSOCIATION",
        "Owner_Type__c": "C-ClientOwned",
        "Load_Management__c": "NONE",
        "sitetracker__Street_Address__c": "Road 1",
        "sitetracker__City__c": "Oslo",
        "sitetracker__Zip_Code__c": "0123",
        "Country__c": "Norway(NOR)",
        "sitetracker__Location__Latitude__s": 59.9139,
        "sitetracker__Location__Longitude__s": 10.7522,
        "EV_Connector_Type__c": "Type 2",
        "EV_Charging_Level__c": "Level 2 AC 22kWh",
        "Open_Date__c": "2023-11-02",
        "Installed_Date__c": "2023-11-02",
        "Operator__c": "Laddel NO",
        "Operator_ID__c": "6",
        "previous_CPO__c": "Laddel (eMabler)",
    }


def test_build_payload_is_flat_with_underscored_field_names() -> None:
    payload = SiteTrackerSitesResource().build_payload(_view_row())

    assert payload == {
        "Site_ID__c": "W047L0042",
        "Name": "Acme Housing",
        "sitetracker__Site_Status__c": "Under Migration",
        "sitetracker__Site_Type__c": "HOUSING_ASSOCIATION",
        "Owner_Type__c": "C-ClientOwned",
        "Load_Management__c": "NONE",
        "sitetracker__Street_Address__c": "Road 1",
        "sitetracker__City__c": "Oslo",
        "sitetracker__Zip_Code__c": "0123",
        "Country__c": "Norway(NOR)",
        "sitetracker__Location__Latitude__s": 59.9139,
        "sitetracker__Location__Longitude__s": 10.7522,
        "EV_Connector_Type__c": "Type 2",
        "EV_Charging_Level__c": "Level 2 AC 22kWh",
        "Open_Date__c": "2023-11-02",
        "Installed_Date__c": "2023-11-02",
        "Operator__c": "Laddel NO",
        "Operator_ID__c": "6",
        "previous_CPO__c": "Laddel (eMabler)",
    }


def test_build_payload_coerces_geoposition_to_float() -> None:
    row = _view_row()
    row["sitetracker__Location__Latitude__s"] = "59.9139"
    row["sitetracker__Location__Longitude__s"] = "10.7522"

    payload = SiteTrackerSitesResource().build_payload(row)

    assert payload["sitetracker__Location__Latitude__s"] == 59.9139
    assert payload["sitetracker__Location__Longitude__s"] == 10.7522
    assert isinstance(payload["sitetracker__Location__Latitude__s"], float)


def test_build_payload_omits_null_optional_fields() -> None:
    row = _view_row()
    row["sitetracker__Street_Address__c"] = None
    row["sitetracker__City__c"] = None

    payload = SiteTrackerSitesResource().build_payload(row)

    assert "sitetracker__Street_Address__c" not in payload
    assert "sitetracker__City__c" not in payload
    assert payload["Name"] == "Acme Housing"


def test_build_payload_keeps_empty_strings() -> None:
    """Empty strings are sent, not dropped — deliberate, see `build_payload`.

    The view's trim turns whitespace-only source text into `''` (119/630 street,
    120/630 city, 122/630 zip as of 2026-08-13). `prune_none` keeps falsey
    values, so those reach Salesforce as `""`. That is safe because we only ever
    PATCH records we created ourselves, and only free-text fields can be blank
    — dates and picklists are constants/CASE expressions in the view, and
    Salesforce would reject a blank date.
    """
    row = _view_row()
    row["sitetracker__Street_Address__c"] = ""
    row["sitetracker__City__c"] = ""

    payload = SiteTrackerSitesResource().build_payload(row)

    assert payload["sitetracker__Street_Address__c"] == ""
    assert payload["sitetracker__City__c"] == ""


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = SiteTrackerSitesResource().mapping_values(_view_row(), "0WSAB000003xyzAB")
    assert values == {
        "mapping_key": "Laddel|Facility|42",
        "target_sf_site_id": "0WSAB000003xyzAB",
    }


def test_resource_targets_sitetracker() -> None:
    resource = SiteTrackerSitesResource()
    assert resource.target_system == "sitetracker"
    assert resource.view == "sitetracker_sites"
    assert resource.mapping_table == "sitetracker_site_mapping"
    assert resource.id_column == "target_sf_site_id"


# -- run(): lookup-before-create --------------------------------------------


class _FakeSiteTrackerClient:
    """Minimal query/create/update double for exercising ``run()``."""

    def __init__(self, query_results: list[dict[str, Any]] | None = None) -> None:
        self.queries: list[str] = []
        self._query_results = query_results or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[object, dict[str, Any]]] = []

    def query(self, soql: str) -> list[dict[str, Any]]:
        self.queries.append(soql)
        return self._query_results

    def create(self, path: str, payload: dict[str, Any], **kw: object) -> dict[str, Any]:
        self.created.append(payload)
        return {"id": "0WSNEW00000000AAB"}

    def update(self, path: str, resource_id: object, payload: dict[str, Any], **kw: object):
        self.updated.append((resource_id, payload))
        return None


def _ctx(client: _FakeSiteTrackerClient, *, dry_run: bool = False) -> RunContext:
    settings = SimpleNamespace(target_db="target")
    return RunContext(
        settings=settings,  # type: ignore[arg-type]
        client=None,
        sitetracker=client,  # type: ignore[arg-type]
        dry_run=dry_run,
    )


@pytest.fixture
def captured_mappings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    written: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        sites, "write_mapping", lambda settings, table, values: written.append((table, values))
    )
    return written


def _patch_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(sites, "fetch_view", lambda settings, view: rows)


def test_creates_directly_when_no_name_collision(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(monkeypatch, [_view_row()])
    client = _FakeSiteTrackerClient(query_results=[])

    result = sites.run(_ctx(client))

    assert result.created == 1
    assert client.queries == [
        "SELECT Id, Site_ID__c, Name FROM sitetracker__Site__c WHERE Name = 'Acme Housing' LIMIT 1"
    ]
    assert client.created[0]["Name"] == "Acme Housing"
    assert captured_mappings == [
        (
            "sitetracker_site_mapping",
            {"mapping_key": "Laddel|Facility|42", "target_sf_site_id": "0WSNEW00000000AAB"},
        )
    ]


def test_adopts_existing_site_when_name_and_project_code_match(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(monkeypatch, [_view_row()])
    client = _FakeSiteTrackerClient(
        query_results=[
            {"Id": "0WSEXIST0000001AB", "Site_ID__c": "W047L0042", "Name": "Acme Housing"}
        ]
    )

    result = sites.run(_ctx(client))

    assert result.updated == 1
    assert result.created == 0
    assert client.created == []
    assert client.updated[0][0] == "0WSEXIST0000001AB"
    assert client.updated[0][1]["Name"] == "Acme Housing"
    assert captured_mappings == [
        (
            "sitetracker_site_mapping",
            {"mapping_key": "Laddel|Facility|42", "target_sf_site_id": "0WSEXIST0000001AB"},
        )
    ]


def test_suffixes_name_with_project_code_on_collision_with_different_site(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(monkeypatch, [_view_row()])
    client = _FakeSiteTrackerClient(
        query_results=[
            {"Id": "0WSOTHER000001AB", "Site_ID__c": "W047L9999", "Name": "Acme Housing"}
        ]
    )

    result = sites.run(_ctx(client))

    assert result.created == 1
    assert client.created[0]["Name"] == "Acme Housing [W047L0042]"
    assert captured_mappings == [
        (
            "sitetracker_site_mapping",
            {"mapping_key": "Laddel|Facility|42", "target_sf_site_id": "0WSNEW00000000AAB"},
        )
    ]


def test_dry_run_skips_lookup_and_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    _patch_rows(monkeypatch, [_view_row()])
    client = _FakeSiteTrackerClient(query_results=[])

    result = sites.run(_ctx(client, dry_run=True))

    assert result.skipped == 1
    assert result.created == 0
    assert client.queries == []
    assert client.created == []
    assert captured_mappings == []


def test_already_mapped_row_updates_after_self_only_lookup(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    """No collision: the Name lookup finds only the record being updated."""
    row = _view_row()
    row["target_sf_site_id"] = "0WSMAPPED0001AB"
    _patch_rows(monkeypatch, [row])
    client = _FakeSiteTrackerClient(
        query_results=[{"Id": "0WSMAPPED0001AB", "Site_ID__c": "W047L0042", "Name": "Acme Housing"}]
    )

    result = sites.run(_ctx(client))

    assert result.updated == 1
    assert len(client.queries) == 1  # already-mapped rows are still guarded before update
    assert client.updated[0][0] == "0WSMAPPED0001AB"
    assert client.updated[0][1]["Name"] == "Acme Housing"
    assert captured_mappings == []


def test_already_mapped_row_renames_on_collision_with_a_different_site(
    monkeypatch: pytest.MonkeyPatch, captured_mappings: list[tuple[str, dict[str, object]]]
) -> None:
    """A Site mapped before this dedup logic existed can still collide with a
    different Site's Name (the root cause of the "Duplicate Site Name" 400s
    seen in production) — the update must disambiguate instead of failing.
    """
    row = _view_row()
    row["target_sf_site_id"] = "0WSMAPPED0001AB"
    _patch_rows(monkeypatch, [row])
    client = _FakeSiteTrackerClient(
        query_results=[
            {"Id": "0WSOTHER000001AB", "Site_ID__c": "W047L9999", "Name": "Acme Housing"}
        ]
    )

    result = sites.run(_ctx(client))

    assert result.updated == 1
    assert client.updated[0][0] == "0WSMAPPED0001AB"
    assert client.updated[0][1]["Name"] == "Acme Housing [W047L0042]"
    assert captured_mappings == []
