"""Create-or-update step for Ampeco partners.

Source of truth is the ``target.partners`` view (``sql/307_target_partners.sql``),
which already encodes the SQL-side business rules. This module's only job is to
turn each view row into the Ampeco partner payload — crucially applying the
field datatypes the view cannot carry (``TINYINT(1)`` columns come back as
``0/1`` ints and must be sent as JSON booleans) — and to describe the mapping
write. Partners have no reliable natural key, so there is no lookup/adopt:
every unmapped row is a fresh create.
"""

from __future__ import annotations

from typing import Any

from ...payload import coerce, nest, prune_none
from ...runner.context import RunContext, StepResult
from ..base import run_create_or_update

# Ampeco "create partner" endpoint (POST); update is PATCH `{PATH}/{id}`.
_PARTNERS_PATH = "/public-api/resources/partners/v2.0"

# Payload columns emitted by `target.partners`, in API order. Mapping/source
# helper columns (mapping_key, source_label, target_partner_id) are
# deliberately excluded.
_STRING_FIELDS: tuple[str, ...] = (
    "name",
    "businessName",
    "externalId",
    "regNo",
    "vatNo",
    "address",
    "postcode",
    "city",
    "country",
    "contactDetails_administrative_contactPerson",
    "contactDetails_administrative_email",
    "contactDetails_administrative_phone",
    "contactDetails_billing_contactPerson",
    "contactDetails_billing_email",
    "receiptsPrefix",
    "invoiceNumberPrefix",
    "options_userVisibility",
    "options_settlementReportBreakdown",
    "notifications_billing_settlementReportLanguage",
    "bankDetails_bankCode",
    "bankDetails_bankAccountNumber",
)

# TINYINT(1) columns that Ampeco expects as JSON booleans.
_BOOL_FIELDS: tuple[str, ...] = (
    "options_allowViewingAllSessionsOfInvitedUsers",
    "options_createUsers",
    "options_addUserBalance",
    "options_supplierOnReceipts",
    "options_supplierOnInvoices",
    "options_allowToControlTariffs",
    "options_allowToControlTariffGroups",
    "options_allowToControlCpConfigurations",
    "corporateBilling_enabled",
    "notifications_technical_chargePointFaults",
    "notifications_billing_settlementReports",
)

_INT_FIELDS: tuple[str, ...] = ("monthlyPlatformFee",)


class PartnersResource:
    """Resource description consumed by :func:`run_create_or_update`."""

    name = "partners"
    view = "partners"
    mapping_table = "partner_mapping"
    key_column = "mapping_key"
    id_column = "target_partner_id"
    path = _PARTNERS_PATH
    target_system = "ampeco"

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for field in _STRING_FIELDS:
            flat[field] = coerce(row.get(field), str)
        for field in _BOOL_FIELDS:
            flat[field] = coerce(row.get(field), bool)
        for field in _INT_FIELDS:
            flat[field] = coerce(row.get(field), int)
        return prune_none(nest(flat))

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_partner_id": target_id,
        }


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry."""
    return run_create_or_update(ctx, PartnersResource())
