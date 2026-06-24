"""Test the partner payload builder: nesting and the TINYINT->bool coercion."""

from __future__ import annotations

from typing import Any

from laddel_migration.steps.ampeco.partners import PartnersResource


def _view_row() -> dict[str, Any]:
    """A row shaped like `target.partners` output (0/1 ints for booleans)."""
    return {
        "mapping_key": "Laddel|Facility|7",
        "source_label": "Acme/Fac",
        "project_code": "P1",
        "target_partner_id": None,
        "name": "Acme",
        "businessName": "Acme [P1]",
        "externalId": "P1",
        "regNo": "999888777",
        "vatNo": "999888777MVA",
        "address": "Road 1",
        "postcode": "0123",
        "city": "Oslo",
        "country": "NO",
        "contactDetails_administrative_contactPerson": "Acme",
        "contactDetails_administrative_email": "a@acme.no",
        "contactDetails_administrative_phone": "12345678",
        "contactDetails_billing_contactPerson": "Elbil-lading",
        "contactDetails_billing_email": "b@acme.no",
        "monthlyPlatformFee": 0,
        "receiptsPrefix": "P1",
        "options_userVisibility": "all",
        "options_allowViewingAllSessionsOfInvitedUsers": 0,
        "options_createUsers": 0,
        "options_addUserBalance": 0,
        "options_supplierOnReceipts": 1,
        "options_supplierOnInvoices": 1,
        "options_allowToControlTariffs": 1,
        "options_allowToControlTariffGroups": 1,
        "options_allowToControlCpConfigurations": 0,
        "options_settlementReportBreakdown": "by_location_and_partner_contract",
        "corporateBilling_enabled": 0,
        "notifications_technical_chargePointFaults": 0,
        "notifications_billing_settlementReports": 0,
        "notifications_billing_settlementReportLanguage": "nb-NO",
        "bankDetails_bankCode": "1234567",
        "bankDetails_bankAccountNumber": "12345678901",
    }


def test_build_payload_matches_ampeco_shape() -> None:
    payload = PartnersResource().build_payload(_view_row())

    assert payload == {
        "name": "Acme",
        "businessName": "Acme [P1]",
        "externalId": "P1",
        "regNo": "999888777",
        "vatNo": "999888777MVA",
        "address": "Road 1",
        "postcode": "0123",
        "city": "Oslo",
        "country": "NO",
        "contactDetails": {
            "administrative": {
                "contactPerson": "Acme",
                "email": "a@acme.no",
                "phone": "12345678",
            },
            "billing": {
                "contactPerson": "Elbil-lading",
                "email": "b@acme.no",
            },
        },
        "monthlyPlatformFee": 0,
        "receiptsPrefix": "P1",
        "options": {
            "userVisibility": "all",
            "allowViewingAllSessionsOfInvitedUsers": False,
            "createUsers": False,
            "addUserBalance": False,
            "supplierOnReceipts": True,
            "supplierOnInvoices": True,
            "allowToControlTariffs": True,
            "allowToControlTariffGroups": True,
            "allowToControlCpConfigurations": False,
            "settlementReportBreakdown": "by_location_and_partner_contract",
        },
        "corporateBilling": {"enabled": False},
        "notifications": {
            "technical": {"chargePointFaults": False},
            "billing": {
                "settlementReports": False,
                "settlementReportLanguage": "nb-NO",
            },
        },
        "bankDetails": {
            "bankCode": "1234567",
            "bankAccountNumber": "12345678901",
        },
    }


def test_boolean_fields_are_real_bools_not_ints() -> None:
    payload = PartnersResource().build_payload(_view_row())
    # Distinguishes True from 1 — Ampeco rejects integers for boolean fields.
    assert payload["options"]["supplierOnReceipts"] is True
    assert payload["options"]["createUsers"] is False


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = PartnersResource().mapping_values(_view_row(), 1007)
    assert values == {"mapping_key": "Laddel|Facility|7", "target_partner_id": 1007}
