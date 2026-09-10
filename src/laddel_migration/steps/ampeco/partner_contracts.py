"""Create-or-update step for Ampeco partner contracts.

Source of truth is the ``target.partner_contracts`` view
(``sql/306_target_partner_contracts.sql``), which already encodes the SQL-side
business rules: the contract-model matrix driven by ``price_information``, the
``title`` format and the handling-fee formula. This module's only job is to turn
each view row into the Ampeco partner contract payload — applying the field
datatypes the view cannot carry (``TINYINT``-style ``0/1`` columns must be sent
as JSON booleans, and ``DECIMAL`` columns arrive as :class:`decimal.Decimal`) —
and to describe the mapping write.

Partner contracts have no reliable natural key in Ampeco, so there is no
lookup/adopt: every unmapped row is a fresh create.

``handlingFee`` is deliberately ``NULL`` for ``COMMISSION`` contracts and is
dropped from the payload by :func:`prune_none`; ``0`` (the ``SUBSCRIPTION``
value) is falsey but not ``None``, so it survives.

The view stays previewable: it emits every in-scope facility, including those
whose partner has not been migrated yet. Holding those rows back is this
module's job — see :meth:`PartnerContractsResource.skip_reason`.
"""

from __future__ import annotations

from typing import Any

from ...payload import coerce, nest, prune_none
from ...runner.context import RunContext, StepResult
from ..base import run_create_or_update

# Ampeco "create partner contract" endpoint (POST); update is PATCH `{PATH}/{id}`.
_PARTNER_CONTRACTS_PATH = "/public-api/resources/partner-contracts/v1.0"

# Payload columns emitted by `target.partner_contracts`, in API order. Mapping/
# source helper columns (mapping_key, source_label, target_partner_contract_id)
# are deliberately excluded.
_STRING_FIELDS: tuple[str, ...] = (
    "title",
    "contractType",
    "startDate",
    "endDate",
    "externalId",
)

_INT_FIELDS: tuple[str, ...] = ("partnerId",)

# 0/1 columns that Ampeco expects as JSON booleans.
_BOOL_FIELDS: tuple[str, ...] = (
    "autoRenewal",
    "accessAndPermissions_sessionsRemoteControl",
    "accessAndPermissions_startReservation",
    "accessAndPermissions_stopReservation",
    "accessAndPermissions_resetChargePoint",
    "accessAndPermissions_firmwareUpdate",
    "accessAndPermissions_createFromTemplate",
    "accessAndPermissions_changeSystemStatus",
    "revenueSharing_excludeConnectionFee",
    "revenueSharing_deductElectricityCost",
    "revenueSharing_reimburseForElectricityCost",
)

# DECIMAL columns that must be sent as JSON numbers, not strings.
_FLOAT_FIELDS: tuple[str, ...] = (
    "revenueSharing_partnerSharePercentageAcEvse",
    "revenueSharing_partnerSharePercentageDcEvse",
    "revenueSharing_handlingFee",
    "monthlyPlatformFees_perChargePoint",
    "monthlyPlatformFees_perAcEvse",
    "monthlyPlatformFees_perDcEvse",
)


class PartnerContractsResource:
    """Resource description consumed by :func:`run_create_or_update`."""

    name = "partner_contracts"
    view = "partner_contracts"
    mapping_table = "partner_contract_mapping"
    key_column = "mapping_key"
    id_column = "target_partner_contract_id"
    path = _PARTNER_CONTRACTS_PATH
    target_system = "ampeco"

    def skip_reason(self, row: dict[str, Any]) -> str | None:
        """Hold back rows whose partner has not been migrated yet.

        ``partnerId`` is API-required. The view LEFT JOINs `partner_mapping` so
        the whole in-scope batch stays previewable, which means an unmigrated
        partner shows up here as a ``NULL`` `partnerId`.
        """
        if row.get("partnerId") is None:
            return "partner not migrated yet (partnerId is NULL) - run the partners step first"
        return None

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for field in _STRING_FIELDS:
            flat[field] = coerce(row.get(field), str)
        for field in _INT_FIELDS:
            flat[field] = coerce(row.get(field), int)
        for field in _BOOL_FIELDS:
            flat[field] = coerce(row.get(field), bool)
        for field in _FLOAT_FIELDS:
            flat[field] = coerce(row.get(field), float)
        return prune_none(nest(flat))

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_partner_contract_id": target_id,
        }


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry."""
    return run_create_or_update(ctx, PartnerContractsResource())
