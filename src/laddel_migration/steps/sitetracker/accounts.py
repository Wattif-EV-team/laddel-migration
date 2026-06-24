"""Create-or-update step for SiteTracker (Salesforce) Accounts.

Source of truth is the ``target.sitetracker_accounts`` view
(``sql/314_target_sitetracker_accounts.sql``), which already encodes the
SQL-side business rules (grain = one Account per ``laddel.customer``, scope =
customers linked to a ``READY`` organization, field shaping/cleaning). This
module's only job is to turn each view row into the Salesforce Account payload
and to describe the mapping write.

Idempotency is by ``mapping_key`` only — there is no SOQL lookup / adopt, so an
unmapped row is always a fresh create (see docs/fieldmapping/sitetracker_account.md).
"""

from __future__ import annotations

from typing import Any

from ...payload import coerce, prune_none
from ...runner.context import RunContext, StepResult
from ..base import run_create_or_update

# Salesforce "create Account" endpoint, version-prefixed by the client.
# (POST /services/data/{api_version}/sobjects/Account; update is PATCH `{PATH}/{id}`.)
_ACCOUNTS_PATH = "/sobjects/Account"

# Account fields emitted by `target.sitetracker_accounts`. These are flat
# Salesforce API names — the underscores in `Business_Registration_Number__c` /
# `Email__c` are part of the name, NOT nesting, so the payload is built flat
# (no `nest()`). Mapping/source helper columns (mapping_key, source_label,
# target_sf_account_id) are deliberately excluded.
_STRING_FIELDS: tuple[str, ...] = (
    "Name",
    "Business_Registration_Number__c",
    "Type",
    "BillingStreet",
    "BillingCity",
    "BillingPostalCode",
    "BillingCountry",
    "Email__c",
    "Phone",
)


class SiteTrackerAccountsResource:
    """Resource description consumed by :func:`run_create_or_update`."""

    name = "sitetracker_accounts"
    view = "sitetracker_accounts"
    mapping_table = "sitetracker_account_mapping"
    key_column = "mapping_key"
    id_column = "target_sf_account_id"
    path = _ACCOUNTS_PATH
    target_system = "sitetracker"

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {field: coerce(row.get(field), str) for field in _STRING_FIELDS}
        # Drop empty/None so we never overwrite a Salesforce field with a blank.
        return prune_none(flat)

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_sf_account_id": target_id,
        }


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry."""
    return run_create_or_update(ctx, SiteTrackerAccountsResource())
