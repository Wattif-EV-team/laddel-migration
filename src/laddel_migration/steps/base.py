"""The generic create-or-update loop shared by every Ampeco resource step.

A resource describes *what* to migrate (its view, mapping table, key/id columns,
endpoint path and how to build its payload); this module owns the loop that is
identical across resources: fetch the view, decide create vs. update per row,
emit the mapping breadcrumb, write the mapping atomically, and tally results.

Per-row business errors are collected and counted so one bad row does not abort
the run. Integrity failures (a mapping write that does not land) raise
``SystemExit`` and stop the whole pipeline — see :func:`db.write_mapping`.

Four hooks are optional. They are looked up dynamically rather than declared on
the :class:`Resource` protocol, so a resource only implements what it needs:

``skip_reason(row) -> str | None``
    Target views are kept previewable (they do not filter out rows whose
    dependencies are not migrated yet), so this hook is where a resource
    declares "this row is not ready to be posted". Rows it rejects are counted
    as skipped and never reach the API.

``passes() -> Sequence[Pass]``
    Split the view into several ordered passes, each with its own ``WHERE``.
    The view is re-read at the start of every pass, so a later pass sees the ids
    an earlier one has just written. This is how a resource whose rows reference
    *each other* (charge-point satellites pointing at their master) is migrated
    in dependency order out of a single view.

``lookup_existing(ctx, row) -> Adoption | None``
    Ask the target system whether an unmapped row already exists under some
    natural key. A match is *adopted*: patched instead of re-created, and its id
    written to the mapping table.

``adoption_values(row, adoption) -> dict[str, object]``
    Extra mapping-table columns recording how a row was matched. Called for
    fresh creates too (with :data:`CREATED`), so the audit trail is complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..db import fetch_view, write_mapping
from ..logging import get_logger, mapping_breadcrumb
from ..runner.context import RunContext, StepResult

logger = get_logger(__name__)


@dataclass(frozen=True)
class Pass:
    """One ordered read of a resource's view, optionally filtered.

    ``where`` is a SQL fragment authored by the resource itself and appended to
    ``SELECT * FROM <view>`` verbatim; the values it compares against belong in
    ``params`` so the driver binds them.
    """

    name: str
    where: str | None = None
    params: tuple[object, ...] = ()


@dataclass(frozen=True)
class Adoption:
    """How an unmapped row was matched to a record in the target system."""

    target_id: object
    matched_by: str
    snapshot: dict[str, Any] | None = None


#: The outcome recorded for a row this run created from scratch.
CREATED = Adoption(target_id=None, matched_by="created", snapshot=None)

_DEFAULT_PASSES: tuple[Pass, ...] = (Pass(name="default"),)


@runtime_checkable
class Resource(Protocol):
    """Contract a resource module implements to be driven by the loop."""

    name: str
    view: str
    mapping_table: str
    key_column: str
    id_column: str
    path: str
    target_system: str

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        """Turn one view row into the target API request body."""
        ...

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        """Columns to insert into the mapping table after a create."""
        ...


def _skip_reason(resource: Resource, row: dict[str, Any]) -> str | None:
    """Ask ``resource`` whether ``row`` should be held back, if it cares.

    Optional part of the resource contract, so it is looked up dynamically
    rather than declared on the :class:`Resource` protocol: most resources have
    no readiness condition and should not have to implement a stub.
    """
    hook = getattr(resource, "skip_reason", None)
    if hook is None:
        return None
    return hook(row)


def _passes(resource: Resource) -> tuple[Pass, ...]:
    """Return ``resource``'s ordered passes, defaulting to a single full read."""
    hook = getattr(resource, "passes", None)
    if hook is None:
        return _DEFAULT_PASSES
    return tuple(hook())


def _lookup_existing(resource: Resource, ctx: RunContext, row: dict[str, Any]) -> Adoption | None:
    """Ask the target system whether ``row`` already exists, if ``resource`` can."""
    hook = getattr(resource, "lookup_existing", None)
    if hook is None:
        return None
    return hook(ctx, row)


def _mapping_values(
    resource: Resource,
    row: dict[str, Any],
    target_id: object,
    adoption: Adoption,
) -> dict[str, object]:
    """Build the mapping row, folding in the optional adoption audit columns."""
    values = dict(resource.mapping_values(row, target_id))
    hook = getattr(resource, "adoption_values", None)
    if hook is not None:
        values.update(hook(row, adoption))
    return values


def run_create_or_update(ctx: RunContext, resource: Resource) -> StepResult:
    """Run ``resource``'s create-or-update flow and return its :class:`StepResult`."""
    result = StepResult(step=resource.name)
    passes = _passes(resource)

    for ordinal, pass_ in enumerate(passes, start=1):
        _run_pass(ctx, resource, pass_, result, multi_pass=len(passes) > 1, ordinal=ordinal)

    logger.info(
        "[%s] done - created=%d adopted=%d updated=%d skipped=%d errors=%d",
        resource.name,
        result.created,
        result.adopted,
        result.updated,
        result.skipped,
        result.error_count,
        extra={"icon": "🏁"},
    )
    return result


def _run_pass(
    ctx: RunContext,
    resource: Resource,
    pass_: Pass,
    result: StepResult,
    *,
    multi_pass: bool,
    ordinal: int,
) -> None:
    """Read the view as filtered by ``pass_`` and process every row it returns.

    Re-reading the view per pass is deliberate: a later pass may depend on ids
    the previous one has just written into the mapping table.
    """
    rows = fetch_view(ctx.settings.target_db, resource.view, where=pass_.where, params=pass_.params)
    result.total += len(rows)
    prefix = f"pass {ordinal} '{pass_.name}': " if multi_pass else ""
    logger.info(
        "[%s] %s%d row(s) from `%s`",
        resource.name,
        prefix,
        len(rows),
        resource.view,
        extra={"icon": "📋"},
    )

    for index, row in enumerate(rows, start=1):
        label = str(row.get("source_label", row.get(resource.key_column, f"row {index}")))
        progress = f"{index}/{len(rows)}"
        payload: dict[str, Any] | None = None
        try:
            reason = _skip_reason(resource, row)
            if reason is not None:
                result.skipped += 1
                logger.warning(
                    "[%s] %s skipped %s: %s",
                    resource.name,
                    progress,
                    label,
                    reason,
                    extra={"icon": "⏭️"},
                )
                continue
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


def _process_row(
    ctx: RunContext,
    resource: Resource,
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

    client = ctx.client_for(resource.target_system)
    # An unmapped row may still exist in the target system — from an aborted run
    # or from manual work. Adopting it is what keeps the migration idempotent for
    # resources that have a natural key; resources without one skip this.
    adoption = _lookup_existing(resource, ctx, row)
    if adoption is not None:
        client.update(resource.path, adoption.target_id, payload)
        new_id = adoption.target_id
        result.adopted += 1
        verb, icon = "adopted", "🤝"
    else:
        adoption = CREATED
        new_id = client.create(resource.path, payload)["id"]
        result.created += 1
        verb, icon = "created", "✅"

    # Breadcrumb BEFORE the mapping write so a lost write is recoverable.
    mapping_breadcrumb(logger, str(row[resource.key_column]), new_id)
    write_mapping(
        ctx.settings.target_db,
        resource.mapping_table,
        _mapping_values(resource, row, new_id, adoption),
    )
    logger.info(
        "[%s] %s %s %s (id=%s)",
        resource.name,
        progress,
        verb,
        label,
        new_id,
        extra={"icon": icon},
    )
