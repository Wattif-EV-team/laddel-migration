"""Tests for the Ampeco charge points step.

The `target.charge_points` view owns the business rules; what is asserted here is
the Python side: datatype coercion (MySQL 0/1 -> JSON bool, JSON strings ->
arrays), the flat-to-nested fold, the master/satellite split, the readiness
hold-backs and the mapping/adoption write.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from laddel_migration.runner.context import RunContext
from laddel_migration.steps.ampeco.charge_points import ChargePointsResource
from laddel_migration.steps.base import CREATED, Adoption

_CAPABILITIES = '["remote_start_stop_capable","meter_values","stop_transaction_on_ev_disconnect"]'


def _master_row(**overrides: Any) -> dict[str, Any]:
    """A row shaped like `target.charge_points` output for an OCPP master.

    Booleans arrive from MySQL as 0/1 ints and the array fields as JSON strings.
    """
    row: dict[str, Any] = {
        "mapping_key": "Laddel|Charger|9001",
        "source_label": "LDE1040 (chg=9001, fac=274)",
        "is_master": 1,
        "box_size": 2,
        "master_mapping_key": "Laddel|Charger|9001",
        "target_charge_point_id": None,
        "name": "NORLDE1040 [MASTER]",
        "type": "private",
        "status": "enabled",
        "locationId": 5001,
        "partner_id": 1402,
        "partner_contractId": 5150,
        "partner_corporateBillingAsDefault": 0,
        "partner_accessType": "private_view_public_use",
        "communicationMode": "direct_ocpp",
        "ocppConnectedChargePointId": None,
        "network_id": "EVB-P2309218",
        "network_protocol": "ocpp 1.6",
        "security_desiredProfile": 0,
        "capabilities": _CAPABILITIES,
        "autoStartWithoutAuthorization": 0,
        "disableAutoStartEmulation": 0,
        "monitoringEnabled": 1,
        "autoRecoveryEnabled": 1,
        "uptimeTrackingEnabled": 1,
        "tags": '["Owner:Customer","Source:Laddel","LocationType:MDU"]',
        "integratedAt": "2021-04-08T09:12:33",
    }
    row.update(overrides)
    return row


def _satellite_row(**overrides: Any) -> dict[str, Any]:
    """A satellite row: the view NULLs every master-only column."""
    row = _master_row(
        mapping_key="Laddel|Charger|9002",
        source_label="LDE1056 (chg=9002, fac=274)",
        is_master=0,
        master_mapping_key="Laddel|Charger|9001",
        name="NORLDE1056 [SLAVE]",
        communicationMode="via_ocpp_connected_charge_point",
        ocppConnectedChargePointId=7001,
        network_id=None,
        network_protocol=None,
        security_desiredProfile=None,
        capabilities=None,
        autoStartWithoutAuthorization=None,
        disableAutoStartEmulation=None,
        monitoringEnabled=None,
        autoRecoveryEnabled=None,
        uptimeTrackingEnabled=None,
        integratedAt=None,
    )
    row.update(overrides)
    return row


class _FakeClient:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
        self.matches = matches or []
        self.gets: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.gets.append((path, params))
        return self.matches


def _ctx(client: _FakeClient) -> RunContext:
    settings = SimpleNamespace(target_db="target")
    return RunContext(settings=settings, client=client, dry_run=False)  # type: ignore[arg-type]


def test_master_payload_matches_ampeco_shape() -> None:
    payload = ChargePointsResource().build_payload(_master_row())

    assert payload == {
        "name": "NORLDE1040 [MASTER]",
        "type": "private",
        "status": "enabled",
        "locationId": 5001,
        "partner": {
            "id": 1402,
            "contractId": 5150,
            "corporateBillingAsDefault": False,
            "accessType": "private_view_public_use",
        },
        "communicationMode": "direct_ocpp",
        "network": {"id": "EVB-P2309218", "protocol": "ocpp 1.6"},
        "security": {"desiredProfile": 0},
        "capabilities": [
            "remote_start_stop_capable",
            "meter_values",
            "stop_transaction_on_ev_disconnect",
        ],
        "autoStartWithoutAuthorization": False,
        "disableAutoStartEmulation": False,
        "monitoringEnabled": True,
        "autoRecoveryEnabled": True,
        "uptimeTrackingEnabled": True,
        "tags": ["Owner:Customer", "Source:Laddel", "LocationType:MDU"],
        "integratedAt": "2021-04-08T09:12:33",
    }


def test_satellite_payload_omits_every_master_only_block() -> None:
    """`via_ocpp_connected_charge_point` forbids the network/security/behaviour keys."""
    payload = ChargePointsResource().build_payload(_satellite_row())

    assert payload["communicationMode"] == "via_ocpp_connected_charge_point"
    assert payload["ocppConnectedChargePointId"] == 7001
    for absent in (
        "network",
        "security",
        "capabilities",
        "integratedAt",
        "monitoringEnabled",
        "autoRecoveryEnabled",
        "uptimeTrackingEnabled",
        "autoStartWithoutAuthorization",
        "disableAutoStartEmulation",
    ):
        assert absent not in payload


def test_master_payload_has_no_ocpp_connected_charge_point_id() -> None:
    assert "ocppConnectedChargePointId" not in ChargePointsResource().build_payload(_master_row())


def test_boolean_fields_are_real_bools_not_ints() -> None:
    """Ampeco rejects integers where the schema declares a boolean."""
    payload = ChargePointsResource().build_payload(_master_row())
    assert payload["monitoringEnabled"] is True
    assert payload["partner"]["corporateBillingAsDefault"] is False


def test_zero_desired_profile_survives_pruning() -> None:
    """0 (No Authentication) is falsey but not None, so prune_none must keep it."""
    payload = ChargePointsResource().build_payload(_master_row())
    assert payload["security"]["desiredProfile"] == 0


def test_public_charge_point_has_no_access_type() -> None:
    payload = ChargePointsResource().build_payload(
        _master_row(type="public", partner_accessType=None)
    )
    assert "accessType" not in payload["partner"]


def test_bookkeeping_columns_never_leak_into_the_payload() -> None:
    payload = ChargePointsResource().build_payload(_master_row())
    for leaked in ("mapping_key", "source_label", "is_master", "box_size", "master", "target"):
        assert leaked not in payload


def test_passes_run_masters_before_satellites() -> None:
    passes = ChargePointsResource().passes()

    assert [p.name for p in passes] == ["masters", "satellites"]
    assert [p.params for p in passes] == [("direct_ocpp",), ("via_ocpp_connected_charge_point",)]
    assert {p.where for p in passes} == {"`communicationMode` = %s"}


@pytest.mark.parametrize(
    ("column", "fragment"),
    [
        ("locationId", "location not migrated yet"),
        ("partner_id", "partner not migrated yet"),
        ("partner_contractId", "partner contract not migrated yet"),
    ],
)
def test_missing_dependency_holds_the_row_back(column: str, fragment: str) -> None:
    reason = ChargePointsResource().skip_reason(_master_row(**{column: None}))
    assert reason is not None
    assert fragment in reason


def test_satellite_without_its_master_is_held_back() -> None:
    reason = ChargePointsResource().skip_reason(_satellite_row(ocppConnectedChargePointId=None))
    assert reason is not None
    assert "master charge point not created yet" in reason


def test_master_without_ocpp_connected_id_is_not_held_back() -> None:
    """The NULL is expected on masters and must not be mistaken for a missing parent."""
    assert ChargePointsResource().skip_reason(_master_row()) is None
    assert ChargePointsResource().skip_reason(_satellite_row()) is None


def test_lookup_adopts_a_charge_point_matching_the_network_id() -> None:
    client = _FakeClient(matches=[{"id": 7001, "name": "NORLDE1040"}])

    adoption = ChargePointsResource().lookup_existing(_ctx(client), _master_row())

    assert client.gets == [
        ("/public-api/resources/charge-points/v2.0", {"filter[networkId]": "EVB-P2309218"})
    ]
    assert adoption == Adoption(
        target_id=7001, matched_by="network_id", snapshot={"id": 7001, "name": "NORLDE1040"}
    )


def test_lookup_returns_none_when_nothing_matches() -> None:
    client = _FakeClient(matches=[])
    assert ChargePointsResource().lookup_existing(_ctx(client), _master_row()) is None


def test_ambiguous_lookup_refuses_to_guess() -> None:
    client = _FakeClient(matches=[{"id": 1}, {"id": 2}])
    with pytest.raises(ValueError, match="matches 2 existing charge points"):
        ChargePointsResource().lookup_existing(_ctx(client), _master_row())


def test_satellites_are_never_looked_up() -> None:
    """They have no network.id, so there is nothing to match on."""
    client = _FakeClient(matches=[{"id": 7001}])

    assert ChargePointsResource().lookup_existing(_ctx(client), _satellite_row()) is None
    assert client.gets == []


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = ChargePointsResource().mapping_values(_master_row(), 7001)
    assert values == {
        "mapping_key": "Laddel|Charger|9001",
        "target_charge_point_id": 7001,
    }


def test_adoption_values_record_a_fresh_create() -> None:
    values = ChargePointsResource().adoption_values(_master_row(), CREATED)
    assert values == {
        "charge_point_existed_before_migration": 0,
        "matched_by": "created",
        "previous_record_snapshot": None,
    }


def test_adoption_values_snapshot_the_pre_update_record() -> None:
    adoption = Adoption(target_id=7001, matched_by="network_id", snapshot={"id": 7001})

    values = ChargePointsResource().adoption_values(_master_row(), adoption)

    assert values["charge_point_existed_before_migration"] == 1
    assert values["matched_by"] == "network_id"
    assert values["previous_record_snapshot"] == '{"id": 7001}'
