"""Create-or-update step for SiteTracker (Salesforce) Site Relations.

Source of truth is the ``target.sitetracker_site_relations`` view
(``sql/316_target_sitetracker_site_relations.sql``), which already encodes the
SQL-side business rules (grain = one relation per ``laddel.facility``, scope =
facilities whose organization is ``READY``/``MIGRATE``, and the hard dependency
gate requiring both the Site and Account to already be mapped). This module's
only job is to turn each view row into the Salesforce ``Site_Relation__c``
payload and to describe the mapping write.

Idempotency is by ``mapping_key`` only — same simple pattern as
``sitetracker_accounts``/``sitetracker_sites``: no SOQL lookup / adopt, so an
unmapped row is always a fresh create (see
docs/fieldmapping/sitetracker_site_relation.md).
"""

from __future__ import annotations

from typing import Any

from ...payload import coerce, prune_none
from ...runner.context import RunContext, StepResult
from ..base import run_create_or_update

# Salesforce "create Site Relation" endpoint, version-prefixed by the client.
# (POST /services/data/{api_version}/sobjects/Site_Relation__c; update is
# PATCH `{PATH}/{id}`.)
_SITE_RELATIONS_PATH = "/sobjects/Site_Relation__c"

# Site Relation fields emitted by `target.sitetracker_site_relations`. All are
# flat text values: `Site__c`/`Company__c` are Salesforce ids (resolved by the
# view's joins to the Site/Account mapping tables), and
# `Site_Relation_Start_Date__c` is already truncated to `DATE` in the view, so
# `coerce(value, str)` yields the `YYYY-MM-DD` string the API expects directly.
# Mapping/source helper columns (mapping_key, source_label,
# target_sf_site_relation_id) are deliberately excluded.
_STRING_FIELDS: tuple[str, ...] = (
    "Site__c",
    "Company__c",
    "Site_Relation_Role__c",
    "Site_Relation_Start_Date__c",
    "previous_CPO__c",
)


class SiteTrackerSiteRelationsResource:
    """Resource description consumed by :func:`run_create_or_update`."""

    name = "sitetracker_site_relations"
    view = "sitetracker_site_relations"
    mapping_table = "sitetracker_site_relation_mapping"
    key_column = "mapping_key"
    id_column = "target_sf_site_relation_id"
    path = _SITE_RELATIONS_PATH
    target_system = "sitetracker"

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {field: coerce(row.get(field), str) for field in _STRING_FIELDS}
        # Strip NULLs only — `prune_none` keeps empty strings by design (see
        # payload.prune_none). Harmless here: every field above is either a
        # constant, a mapping-resolved id gated NOT NULL by the view, or a
        # DATE, so none can arrive blank.
        return prune_none(flat)

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_sf_site_relation_id": target_id,
        }


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry."""
    return run_create_or_update(ctx, SiteTrackerSiteRelationsResource())
