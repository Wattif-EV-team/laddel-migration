"""Test the partner contract payload builder: nesting, bool/Decimal coercion.

The contract-model matrix itself lives in `target.partner_contracts`
(``sql/306_target_partner_contracts.sql``); these tests only pin the shape the
step produces from a view row, per contract type.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from laddel_migration.steps.ampeco.partner_contracts import PartnerContractsResource


def _view_row(**overrides: Any) -> dict[str, Any]:
    """A row shaped like `target.partner_contracts` output.

    Booleans arrive as 0/1 ints and money/percentages as ``Decimal``, exactly as
    the MySQL driver returns them. Defaults describe a ``SUBSCRIPTION`` facility.
    """
    row: dict[str, Any] = {
        "mapping_key": "Laddel|Facility|1238",
        "source_label": "Al Grønnebakken Borettslag (fac=1238)",
        "target_partner_contract_id": None,
        "title": "W047L1238 - Al Grønnebakken Borettslag (Subscription 89 NOK/user)",
        "partnerId": 1402,
        "contractType": "paymentFacilitation",
        "startDate": "2026-09-03T00:00:00Z",
        "endDate": None,
        "autoRenewal": 1,
        "externalId": None,
        "accessAndPermissions_sessionsRemoteControl": 1,
        "accessAndPermissions_startReservation": 1,
        "accessAndPermissions_stopReservation": 1,
        "accessAndPermissions_resetChargePoint": 1,
        "accessAndPermissions_firmwareUpdate": 1,
        "accessAndPermissions_createFromTemplate": 1,
        "accessAndPermissions_changeSystemStatus": 1,
        "revenueSharing_partnerSharePercentageAcEvse": Decimal("100"),
        "revenueSharing_partnerSharePercentageDcEvse": Decimal("100"),
        "revenueSharing_excludeConnectionFee": 0,
        "revenueSharing_deductElectricityCost": 0,
        "revenueSharing_reimburseForElectricityCost": 0,
        "revenueSharing_handlingFee": Decimal("0.0000"),
        "monthlyPlatformFees_perChargePoint": 0,
        "monthlyPlatformFees_perAcEvse": Decimal("0.00"),
        "monthlyPlatformFees_perDcEvse": Decimal("0.00"),
    }
    row.update(overrides)
    return row


def test_build_payload_matches_ampeco_shape() -> None:
    payload = PartnerContractsResource().build_payload(_view_row())
    assert payload == {
        "title": "W047L1238 - Al Grønnebakken Borettslag (Subscription 89 NOK/user)",
        "partnerId": 1402,
        "contractType": "paymentFacilitation",
        "startDate": "2026-09-03T00:00:00Z",
        "autoRenewal": True,
        "accessAndPermissions": {
            "sessionsRemoteControl": True,
            "startReservation": True,
            "stopReservation": True,
            "resetChargePoint": True,
            "firmwareUpdate": True,
            "createFromTemplate": True,
            "changeSystemStatus": True,
        },
        "revenueSharing": {
            "partnerSharePercentageAcEvse": 100.0,
            "partnerSharePercentageDcEvse": 100.0,
            "excludeConnectionFee": False,
            "deductElectricityCost": False,
            "reimburseForElectricityCost": False,
            "handlingFee": 0.0,
        },
        "monthlyPlatformFees": {
            "perChargePoint": 0.0,
            "perAcEvse": 0.0,
            "perDcEvse": 0.0,
        },
    }


def test_boolean_fields_are_real_bools_not_ints() -> None:
    """Ampeco rejects integers where the schema declares a boolean."""
    payload = PartnerContractsResource().build_payload(_view_row())
    assert payload["autoRenewal"] is True
    assert payload["accessAndPermissions"]["changeSystemStatus"] is True
    assert payload["revenueSharing"]["excludeConnectionFee"] is False
    assert payload["revenueSharing"]["deductElectricityCost"] is False


def test_null_optional_fields_are_omitted() -> None:
    """endDate and externalId are always NULL in the view and must not be sent."""
    payload = PartnerContractsResource().build_payload(_view_row())
    assert "endDate" not in payload
    assert "externalId" not in payload


def test_subscription_zero_handling_fee_is_kept() -> None:
    """`0` is falsey but not None, so prune_none must not drop it."""
    payload = PartnerContractsResource().build_payload(_view_row())
    assert payload["revenueSharing"]["handlingFee"] == 0.0


def test_markup_handling_fee_is_a_number() -> None:
    row = _view_row(
        title="W047L1016 - Scandic Sunnfjord (Markup 10%, 69 NOK/evse)",
        revenueSharing_handlingFee=Decimal("8.8889"),
        monthlyPlatformFees_perAcEvse=Decimal("69.00"),
        monthlyPlatformFees_perDcEvse=Decimal("69.00"),
    )
    payload = PartnerContractsResource().build_payload(row)
    assert payload["contractType"] == "paymentFacilitation"
    assert payload["revenueSharing"]["handlingFee"] == 8.8889
    assert payload["monthlyPlatformFees"]["perAcEvse"] == 69.0


def test_commission_row_omits_handling_fee_and_splits_revenue() -> None:
    row = _view_row(
        title="W047L0005 - Rosfjord Strandhotell (Commission 35%, 0 NOK/evse)",
        contractType="revenueSharing",
        revenueSharing_partnerSharePercentageAcEvse=Decimal("65.000"),
        revenueSharing_partnerSharePercentageDcEvse=Decimal("65.000"),
        revenueSharing_handlingFee=None,
    )
    payload = PartnerContractsResource().build_payload(row)
    assert payload["contractType"] == "revenueSharing"
    assert payload["revenueSharing"]["partnerSharePercentageAcEvse"] == 65.0
    assert payload["revenueSharing"]["partnerSharePercentageDcEvse"] == 65.0
    assert "handlingFee" not in payload["revenueSharing"]


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = PartnerContractsResource().mapping_values(_view_row(), 5150)
    assert values == {
        "mapping_key": "Laddel|Facility|1238",
        "target_partner_contract_id": 5150,
    }


def test_row_with_a_partner_is_not_skipped() -> None:
    assert PartnerContractsResource().skip_reason(_view_row()) is None


def test_row_without_a_partner_is_skipped() -> None:
    """The view stays previewable, so unmigrated partners surface as NULL here."""
    reason = PartnerContractsResource().skip_reason(_view_row(partnerId=None))
    assert reason is not None
    assert "partnerId" in reason
