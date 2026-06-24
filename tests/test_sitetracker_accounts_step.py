"""Test the SiteTracker Account payload builder and mapping description.

Account API field names contain underscores (`Business_Registration_Number__c`,
`Email__c`) that are NOT nesting, so the payload must stay flat.
"""

from __future__ import annotations

from typing import Any

from laddel_migration.steps.sitetracker.accounts import SiteTrackerAccountsResource


def _view_row() -> dict[str, Any]:
    """A row shaped like `target.sitetracker_accounts` output."""
    return {
        "mapping_key": "Laddel|SiteTrackerAccount|42",
        "source_label": "Acme AS (cust=42)",
        "target_sf_account_id": None,
        "Name": "Acme AS",
        "Business_Registration_Number__c": "999888777",
        "Type": "Customer",
        "BillingStreet": "Road 1",
        "BillingCity": "Oslo",
        "BillingPostalCode": "0123",
        "BillingCountry": "Norway",
        "Email__c": "post@acme.no",
        "Phone": "12345678",
    }


def test_build_payload_is_flat_with_underscored_field_names() -> None:
    payload = SiteTrackerAccountsResource().build_payload(_view_row())

    assert payload == {
        "Name": "Acme AS",
        "Business_Registration_Number__c": "999888777",
        "Type": "Customer",
        "BillingStreet": "Road 1",
        "BillingCity": "Oslo",
        "BillingPostalCode": "0123",
        "BillingCountry": "Norway",
        "Email__c": "post@acme.no",
        "Phone": "12345678",
    }


def test_build_payload_omits_blank_optional_fields() -> None:
    row = _view_row()
    row["Email__c"] = None
    row["Phone"] = None
    row["Business_Registration_Number__c"] = ""  # private individual

    payload = SiteTrackerAccountsResource().build_payload(row)

    assert "Email__c" not in payload
    assert "Phone" not in payload
    # Empty string is a falsey value that prune_none keeps (not None).
    assert payload["Business_Registration_Number__c"] == ""
    assert payload["Name"] == "Acme AS"


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = SiteTrackerAccountsResource().mapping_values(_view_row(), "001AB000003xyzAB")
    assert values == {
        "mapping_key": "Laddel|SiteTrackerAccount|42",
        "target_sf_account_id": "001AB000003xyzAB",
    }


def test_resource_targets_sitetracker() -> None:
    resource = SiteTrackerAccountsResource()
    assert resource.target_system == "sitetracker"
    assert resource.view == "sitetracker_accounts"
    assert resource.mapping_table == "sitetracker_account_mapping"
    assert resource.id_column == "target_sf_account_id"
