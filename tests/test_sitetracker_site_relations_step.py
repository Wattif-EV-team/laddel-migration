"""Test the SiteTracker Site Relation payload builder and mapping description.

`Site__c`/`Company__c` arrive already resolved to Salesforce ids by the view's
joins to the Site/Account mapping tables — the step just passes them through as
flat string fields, same as every other Site_Relation__c field.
"""

from __future__ import annotations

from typing import Any

from laddel_migration.steps.sitetracker.site_relations import SiteTrackerSiteRelationsResource


def _view_row() -> dict[str, Any]:
    """A row shaped like `target.sitetracker_site_relations` output."""
    return {
        "mapping_key": "Laddel|Facility|42",
        "source_label": "Acme Housing (fac=42)",
        "target_sf_site_relation_id": None,
        "Site__c": "0WSAB000003xyzAB",
        "Company__c": "001AB000003xyzAB",
        "Site_Relation_Role__c": "OWNER of SITE",
        "Site_Relation_Start_Date__c": "2023-11-02",
        "previous_CPO__c": "Laddel (eMabler)",
    }


def test_build_payload_is_flat_with_resolved_ids() -> None:
    payload = SiteTrackerSiteRelationsResource().build_payload(_view_row())

    assert payload == {
        "Site__c": "0WSAB000003xyzAB",
        "Company__c": "001AB000003xyzAB",
        "Site_Relation_Role__c": "OWNER of SITE",
        "Site_Relation_Start_Date__c": "2023-11-02",
        "previous_CPO__c": "Laddel (eMabler)",
    }


def test_build_payload_omits_null_optional_fields() -> None:
    row = _view_row()
    row["Site_Relation_Start_Date__c"] = None

    payload = SiteTrackerSiteRelationsResource().build_payload(row)

    assert "Site_Relation_Start_Date__c" not in payload
    assert payload["Site__c"] == "0WSAB000003xyzAB"


def test_build_payload_keeps_empty_strings() -> None:
    """Empty strings are sent, not dropped — `prune_none` keeps falsey values.

    Pinned for symmetry with the Sites step. In practice no Site Relation field
    can arrive blank: every one is a constant, a mapping-resolved id the view
    gates NOT NULL, or a DATE.
    """
    row = _view_row()
    row["previous_CPO__c"] = ""

    payload = SiteTrackerSiteRelationsResource().build_payload(row)

    assert payload["previous_CPO__c"] == ""


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = SiteTrackerSiteRelationsResource().mapping_values(_view_row(), "a0BAB000003xyzAB")
    assert values == {
        "mapping_key": "Laddel|Facility|42",
        "target_sf_site_relation_id": "a0BAB000003xyzAB",
    }


def test_resource_targets_sitetracker() -> None:
    resource = SiteTrackerSiteRelationsResource()
    assert resource.target_system == "sitetracker"
    assert resource.view == "sitetracker_site_relations"
    assert resource.mapping_table == "sitetracker_site_relation_mapping"
    assert resource.id_column == "target_sf_site_relation_id"
