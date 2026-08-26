"""Create-or-update step for SiteTracker (Salesforce) Sites.

Source of truth is the ``target.sitetracker_sites`` view
(``sql/315_target_sitetracker_sites.sql``), which already encodes the SQL-side
business rules (grain = one Site per ``laddel.facility``, scope = facilities
whose organization is ``READY``/``MIGRATE``, site-type/owner-type
classification, date truncation). This module's only job is to turn each view
row into the Salesforce Site payload and to describe the mapping write.

Idempotency is by ``mapping_key``, but Site ``Name`` must additionally be
unique in the target org, so a row cannot be created *or updated* blindly
(unlike ``sitetracker_accounts``/``sitetracker_site_relations``). Before every
create or update, we SOQL-lookup an existing Site by ``Name``:

* No match (create) → create with the payload as-is.
* Match with the same project code (``Site_ID__c``) (create) → the Site
  already exists (e.g. from a prior partial run) — adopt it: update in place
  and write the mapping, instead of attempting a create.
* Match belonging to a *different* Salesforce record than the one being
  written (create with a different project code, or update) → the name
  collides with an unrelated Site; disambiguate ours by appending the project
  code in square brackets (``"<name> [<project_code>]"``) before writing. This
  also covers Sites that were mapped **before** this dedup logic existed and
  whose ``Name`` duplicates another Site's — Salesforce rejects every update to
  either one until the clash is resolved, so the guard applies on update too.

This mirrors the lookup-before-create pattern in the reference implementation
(reference/projectsaturn/CreateOrUpdateSiteTrackerSites.py), re-implemented for
this codebase's client/runner shape. Cannot use the generic
:func:`~..base.run_create_or_update` loop because that lookup/rename branching
is specific to this resource.
"""

from __future__ import annotations

from typing import Any

from ...db import fetch_view, write_mapping
from ...logging import get_logger, mapping_breadcrumb
from ...payload import coerce, prune_none
from ...runner.context import RunContext, StepResult

logger = get_logger(__name__)

# Salesforce "create Site" endpoint, version-prefixed by the client.
# (POST /services/data/{api_version}/sobjects/sitetracker__Site__c; update is
# PATCH `{PATH}/{id}`.)
_SITES_PATH = "/sobjects/sitetracker__Site__c"
_SOBJECT = "sitetracker__Site__c"

# Site fields emitted by `target.sitetracker_sites` that are plain text,
# picklist, or date values. These are flat Salesforce API names — the `__c`
# underscores are part of the name, NOT nesting, so the payload is built flat
# (no `nest()`). Date columns are already truncated to `DATE` in the view, so
# `coerce(value, str)` yields the `YYYY-MM-DD` string the API expects directly.
# Mapping/source helper columns (mapping_key, source_label,
# target_sf_site_id) are deliberately excluded.
_STRING_FIELDS: tuple[str, ...] = (
    "Site_ID__c",
    "Name",
    "sitetracker__Site_Status__c",
    "sitetracker__Site_Type__c",
    "Owner_Type__c",
    "Load_Management__c",
    "sitetracker__Street_Address__c",
    "sitetracker__City__c",
    "sitetracker__Zip_Code__c",
    "Country__c",
    "EV_Connector_Type__c",
    "EV_Charging_Level__c",
    "Open_Date__c",
    "Installed_Date__c",
    "Operator__c",
    "Operator_ID__c",
    "previous_CPO__c",
)

# Compound geolocation field halves — sent as flat, separately-named fields
# (not re-nested), same as every other Site field.
_FLOAT_FIELDS: tuple[str, ...] = (
    "sitetracker__Location__Latitude__s",
    "sitetracker__Location__Longitude__s",
)


class SiteTrackerSitesResource:
    """Resource description consumed by this module's :func:`run` loop."""

    name = "sitetracker_sites"
    view = "sitetracker_sites"
    mapping_table = "sitetracker_site_mapping"
    key_column = "mapping_key"
    id_column = "target_sf_site_id"
    path = _SITES_PATH
    target_system = "sitetracker"

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {field: coerce(row.get(field), str) for field in _STRING_FIELDS}
        for field in _FLOAT_FIELDS:
            flat[field] = coerce(row.get(field), float)
        # Strip NULLs only: a column the view left NULL must be omitted rather
        # than sent as `null`. `prune_none` deliberately KEEPS empty strings
        # (see payload.prune_none) and that is fine here — the view's trim can
        # only produce '' for the three free-text address fields, which
        # Salesforce accepts, and we never PATCH a record we did not create
        # ourselves (no SOQL adopt — see sitetracker_site.md Q15), so a blank
        # cannot clobber third-party data.
        return prune_none(flat)

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_sf_site_id": target_id,
        }


def _escape_soql_string(value: str) -> str:
    """Escape a value for safe interpolation into a single-quoted SOQL literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_site_by_name(client: Any, name: str) -> dict[str, Any] | None:
    """SOQL-lookup an existing Site with an exact ``Name`` match, if any.

    Used both before create (unmapped rows) and before update (already-mapped
    rows) — Site names must be unique in the target org, so this is how we
    detect a pre-existing or colliding Site (see module docstring).
    """
    soql = (
        f"SELECT Id, Site_ID__c, Name FROM {_SOBJECT} "
        f"WHERE Name = '{_escape_soql_string(name)}' LIMIT 1"
    )
    records = client.query(soql)
    return records[0] if records else None


def _avoid_name_collision(
    client: Any,
    payload: dict[str, Any],
    project_code: object,
    label: str,
    resource_name: str,
    *,
    exclude_id: object,
) -> dict[str, Any]:
    """Return ``payload`` with a disambiguated ``Name`` if it collides.

    A collision is a different Salesforce Site (``Id != exclude_id``) already
    holding this exact ``Name``. Matters for update as much as for create: a
    Site mapped **before** this dedup logic existed can still hold a `Name`
    that duplicates another Site's, and Salesforce rejects every subsequent
    update to either one until the clash is resolved — this is what produced
    the "Duplicate Site Name" 400s seen in production runs. ``exclude_id`` is
    ``None`` when creating (nothing to exclude yet).
    """
    name = payload.get("Name")
    if not name:
        return payload
    existing = _find_site_by_name(client, name)
    if existing is None or existing["Id"] == exclude_id:
        return payload
    suffixed_name = f"{name} [{project_code}]"
    logger.warning(
        "[%s] %s: Name %r already used by Site %s (Site_ID__c=%r) — using %r instead",
        resource_name,
        label,
        name,
        existing["Id"],
        existing.get("Site_ID__c"),
        suffixed_name,
        extra={"icon": "⚠️"},
    )
    return {**payload, "Name": suffixed_name}


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry.

    Cannot delegate to the generic :func:`~..base.run_create_or_update` loop:
    Site names must be unique in the target org, so every unmapped row needs a
    SOQL lookup-by-name before create, with its own adopt/rename branching (see
    module docstring) — a per-resource concern the shared loop does not support.
    """
    resource = SiteTrackerSitesResource()
    result = StepResult(step=resource.name)
    rows = fetch_view(ctx.settings.target_db, resource.view)
    result.total = len(rows)
    logger.info(
        "[%s] %d row(s) from `%s`",
        resource.name,
        result.total,
        resource.view,
        extra={"icon": "📋"},
    )

    for index, row in enumerate(rows, start=1):
        label = str(row.get("source_label", row.get(resource.key_column, f"row {index}")))
        progress = f"{index}/{result.total}"
        payload: dict[str, Any] | None = None
        try:
            payload = resource.build_payload(row)
            _process_row(ctx, resource, row, payload, label, progress, result)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - collect per-row errors, keep going
            result.errors.append(f"{label}: {exc}")
            logger.error(
                "[%s] %s %s failed: %s", resource.name, progress, label, exc, extra={"icon": "❌"}
            )
            # Only on errors do we surface the offending object, to aid triage.
            logger.error(
                "[%s]   object: %s", resource.name, payload if payload is not None else row
            )

    logger.info(
        "[%s] done - created=%d updated=%d skipped=%d errors=%d",
        resource.name,
        result.created,
        result.updated,
        result.skipped,
        result.error_count,
        extra={"icon": "🏁"},
    )
    return result


def _process_row(
    ctx: RunContext,
    resource: SiteTrackerSitesResource,
    row: dict[str, Any],
    payload: dict[str, Any],
    label: str,
    progress: str,
    result: StepResult,
) -> None:
    target_id = row.get(resource.id_column)
    # The full payload is verbose: keep it at DEBUG (file only) unless something
    # fails, in which case the caller logs it at ERROR.
    logger.debug("[%s] %s %s payload=%s", resource.name, progress, label, payload)

    if target_id is not None:
        if ctx.dry_run:
            logger.info(
                "[%s] %s would update %s (id=%s)",
                resource.name,
                progress,
                label,
                target_id,
                extra={"icon": "🔄"},
            )
            result.skipped += 1
            return
        client = ctx.client_for(resource.target_system)
        payload = _avoid_name_collision(
            client, payload, row.get("Site_ID__c"), label, resource.name, exclude_id=target_id
        )
        client.update(resource.path, target_id, payload)
        result.updated += 1
        logger.info(
            "[%s] %s updated %s (id=%s)",
            resource.name,
            progress,
            label,
            target_id,
            extra={"icon": "🔄"},
        )
        return

    if ctx.dry_run:
        logger.info("[%s] %s would create %s", resource.name, progress, label, extra={"icon": "✨"})
        result.skipped += 1
        return

    # Unmapped: Site names must be unique in the target org, so look up by
    # Name before create (see module docstring).
    client = ctx.client_for(resource.target_system)
    project_code = row.get("Site_ID__c")
    name = payload.get("Name")
    existing = _find_site_by_name(client, name) if name else None

    if existing is not None and existing.get("Site_ID__c") == project_code:
        # Same Site already exists in the target org, just not mapped yet
        # (e.g. a prior partial run) — adopt it: update in place + write mapping.
        sf_id = existing["Id"]
        client.update(resource.path, sf_id, payload)
        mapping_breadcrumb(logger, str(row[resource.key_column]), sf_id)
        write_mapping(
            ctx.settings.target_db,
            resource.mapping_table,
            resource.mapping_values(row, sf_id),
        )
        result.updated += 1
        logger.info(
            "[%s] %s adopted existing Site %s (id=%s)",
            resource.name,
            progress,
            label,
            sf_id,
            extra={"icon": "🔄"},
        )
        return

    if existing is not None:
        # Name collides with an unrelated Site (different project code) —
        # disambiguate ours rather than fail the row.
        suffixed_name = f"{name} [{project_code}]"
        logger.warning(
            "[%s] %s: Name %r already used by Site %s (Site_ID__c=%r) — creating as %r instead",
            resource.name,
            label,
            name,
            existing["Id"],
            existing.get("Site_ID__c"),
            suffixed_name,
            extra={"icon": "⚠️"},
        )
        payload = {**payload, "Name": suffixed_name}

    data = client.create(resource.path, payload)
    new_id = data["id"]
    # Breadcrumb BEFORE the mapping write so a lost write is recoverable.
    mapping_breadcrumb(logger, str(row[resource.key_column]), new_id)
    write_mapping(
        ctx.settings.target_db,
        resource.mapping_table,
        resource.mapping_values(row, new_id),
    )
    result.created += 1
    logger.info(
        "[%s] %s created %s (id=%s)",
        resource.name,
        progress,
        label,
        new_id,
        extra={"icon": "✅"},
    )
