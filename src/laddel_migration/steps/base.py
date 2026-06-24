"""The generic create-or-update loop shared by every Ampeco resource step.

A resource describes *what* to migrate (its view, mapping table, key/id columns,
endpoint path and how to build its payload); this module owns the loop that is
identical across resources: fetch the view, decide create vs. update per row,
emit the mapping breadcrumb, write the mapping atomically, and tally results.

Per-row business errors are collected and counted so one bad row does not abort
the run. Integrity failures (a mapping write that does not land) raise
``SystemExit`` and stop the whole pipeline — see :func:`db.write_mapping`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..db import fetch_view, write_mapping
from ..logging import get_logger, mapping_breadcrumb
from ..runner.context import RunContext, StepResult

logger = get_logger(__name__)


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


def run_create_or_update(ctx: RunContext, resource: Resource) -> StepResult:
    """Run ``resource``'s create-or-update flow and return its :class:`StepResult`."""
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
        payload: dict[str, Any] | None = None
        try:
            payload = resource.build_payload(row)
            _process_row(ctx, resource, row, payload, label, result)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - collect per-row errors, keep going
            result.errors.append(f"{label}: {exc}")
            logger.error("[%s] %s failed: %s", resource.name, label, exc, extra={"icon": "❌"})
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
    resource: Resource,
    row: dict[str, Any],
    payload: dict[str, Any],
    label: str,
    result: StepResult,
) -> None:
    target_id = row.get(resource.id_column)
    # The full payload is verbose: keep it at DEBUG (file only) unless something
    # fails, in which case the caller logs it at ERROR.
    logger.debug("[%s] %s payload=%s", resource.name, label, payload)

    if target_id is not None:
        if ctx.dry_run:
            logger.info(
                "[%s] would update %s (id=%s)",
                resource.name,
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
            "[%s] updated %s (id=%s)", resource.name, label, target_id, extra={"icon": "🔄"}
        )
        return

    if ctx.dry_run:
        logger.info("[%s] would create %s", resource.name, label, extra={"icon": "✨"})
        result.skipped += 1
        return

    data = ctx.client_for(resource.target_system).create(resource.path, payload)
    new_id = data["id"]
    # Breadcrumb BEFORE the mapping write so a lost write is recoverable.
    mapping_breadcrumb(logger, str(row[resource.key_column]), new_id)
    write_mapping(
        ctx.settings.target_db,
        resource.mapping_table,
        resource.mapping_values(row, new_id),
    )
    result.created += 1
    logger.info("[%s] created %s (id=%s)", resource.name, label, new_id, extra={"icon": "✅"})
