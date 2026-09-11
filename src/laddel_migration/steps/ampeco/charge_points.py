"""Create-or-update step for Ampeco charge points.

Source of truth is the ``target.charge_points`` view
(``sql/301_target_charge_points.sql``), which already encodes the SQL-side
business rules: the master/satellite election within each OCPP box, the ``name``
and ``type`` rules, the access-type matrix and the master-only NULLing. This
module's only job is to turn each view row into the Ampeco charge point payload
— applying the datatypes the view cannot carry (``TINYINT``-style ``0/1``
columns must be sent as JSON booleans, and JSON array columns arrive as strings)
— to look up pre-existing records, and to describe the mapping write.

Two passes
----------
A ``laddel.charger`` is an EVSE; several chargers can share one physical box
(one ``ocpp_id``). The lowest-``socket_id`` charger of a box owns the OCPP
connection (``direct_ocpp``); the rest are satellites that point at it through
``ocppConnectedChargePointId``. A satellite therefore needs its master's *Ampeco*
id, which only exists once the master has been created. The step runs the
masters pass first and the satellites pass second; the loop re-reads the view
between passes, so the satellites see the ids just written to
``target.charge_point_mapping``.

Idempotency
-----------
Unlike partners / locations / partner contracts, charge points have a reliable
natural key in Ampeco: ``network.id`` (the OCPP id). An unmapped master is
therefore looked up with ``filter[networkId]`` and *adopted* — patched and
recorded — instead of being created a second time. Satellites have no
``network.id`` and cannot be looked up, so every satellite write is a fresh
create; the loop's breadcrumb-then-mapping-write ordering is what protects them.
"""

from __future__ import annotations

import json
from typing import Any

from ...payload import coerce, nest, prune_none
from ...runner.context import RunContext, StepResult
from ..base import Adoption, Pass, run_create_or_update

# Ampeco "create charge point" endpoint (POST); update is PATCH `{PATH}/{id}`.
# The v1.0 family is deprecated and must not be used.
_CHARGE_POINTS_PATH = "/public-api/resources/charge-points/v2.0"

# Payload columns emitted by `target.charge_points`, in API order. Bookkeeping
# columns (mapping_key, source_label, is_master, box_size, master_mapping_key,
# target_charge_point_id) are deliberately excluded: they must never reach
# `nest`, which would turn e.g. `master_mapping_key` into a `master` object.
_STRING_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "status",
    "partner_accessType",
    "communicationMode",
    "network_id",
    "network_protocol",
    "integratedAt",
)

_INT_FIELDS: tuple[str, ...] = (
    "locationId",
    "partner_id",
    "partner_contractId",
    "ocppConnectedChargePointId",
    "security_desiredProfile",
)

# 0/1 columns that Ampeco expects as JSON booleans.
_BOOL_FIELDS: tuple[str, ...] = (
    "partner_corporateBillingAsDefault",
    "autoStartWithoutAuthorization",
    "disableAutoStartEmulation",
    "monitoringEnabled",
    "autoRecoveryEnabled",
    "uptimeTrackingEnabled",
)

# Columns the view emits as JSON strings and the API expects as arrays.
_JSON_FIELDS: tuple[str, ...] = (
    "capabilities",
    "tags",
)


class ChargePointsResource:
    """Resource description consumed by :func:`run_create_or_update`."""

    name = "charge_points"
    view = "charge_points"
    mapping_table = "charge_point_mapping"
    key_column = "mapping_key"
    id_column = "target_charge_point_id"
    path = _CHARGE_POINTS_PATH
    target_system = "ampeco"

    def passes(self) -> tuple[Pass, ...]:
        """Masters first, then the satellites that reference them.

        The discriminator is `communicationMode` rather than `is_master` because
        it is the payload field that actually imposes the ordering: a
        `via_ocpp_connected_charge_point` charge point is rejected without a
        resolvable `ocppConnectedChargePointId`.
        """
        return (
            Pass(name="masters", where="`communicationMode` = %s", params=("direct_ocpp",)),
            Pass(
                name="satellites",
                where="`communicationMode` = %s",
                params=("via_ocpp_connected_charge_point",),
            ),
        )

    def skip_reason(self, row: dict[str, Any]) -> str | None:
        """Hold back rows whose dependencies have not been migrated yet.

        The view LEFT JOINs every mapping table so the whole in-scope batch stays
        previewable, which means an unmigrated dependency shows up here as a
        ``NULL`` id.
        """
        if row.get("locationId") is None:
            return "location not migrated yet (locationId is NULL) - run the locations step first"
        if row.get("partner_id") is None:
            return "partner not migrated yet (partner_id is NULL) - run the partners step first"
        if row.get("partner_contractId") is None:
            return (
                "partner contract not migrated yet (partner_contractId is NULL) - "
                "run the partner_contracts step first"
            )
        if row.get("communicationMode") == "via_ocpp_connected_charge_point" and (
            row.get("ocppConnectedChargePointId") is None
        ):
            return (
                "master charge point not created yet (ocppConnectedChargePointId is NULL) - "
                "the masters pass must succeed for this OCPP box first"
            )
        return None

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for field in _STRING_FIELDS:
            flat[field] = coerce(row.get(field), str)
        for field in _INT_FIELDS:
            flat[field] = coerce(row.get(field), int)
        for field in _BOOL_FIELDS:
            flat[field] = coerce(row.get(field), bool)
        for field in _JSON_FIELDS:
            raw = coerce(row.get(field), str)
            flat[field] = None if raw is None else json.loads(raw)
        return prune_none(nest(flat))

    def lookup_existing(self, ctx: RunContext, row: dict[str, Any]) -> Adoption | None:
        """Adopt a pre-existing charge point matching this row's ``network.id``.

        Only masters carry a ``network_id``; satellites have no natural key in
        Ampeco and are always created. The index response doubles as the
        pre-update snapshot stored in the mapping table.
        """
        network_id = row.get("network_id")
        if not network_id:
            return None

        matches = ctx.client_for(self.target_system).get(
            self.path, params={"filter[networkId]": network_id}
        )
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(
                f"networkId {network_id!r} matches {len(matches)} existing charge points "
                f"({[m.get('id') for m in matches]}); refusing to guess which one to adopt"
            )
        existing = matches[0]
        return Adoption(target_id=existing["id"], matched_by="network_id", snapshot=existing)

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_charge_point_id": target_id,
        }

    def adoption_values(self, row: dict[str, Any], adoption: Adoption) -> dict[str, object]:
        """Audit columns recording whether we created the record or adopted one."""
        adopted = adoption.matched_by != "created"
        return {
            "charge_point_existed_before_migration": int(adopted),
            "matched_by": adoption.matched_by,
            "previous_record_snapshot": (
                json.dumps(adoption.snapshot, default=str) if adoption.snapshot else None
            ),
        }


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry."""
    return run_create_or_update(ctx, ChargePointsResource())
