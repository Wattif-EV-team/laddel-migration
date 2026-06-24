"""Run selected steps in order and produce a consolidated end-of-run report."""

from __future__ import annotations

from ..logging import get_logger
from .context import RunContext, StepResult
from .registry import Step, resolve_steps

logger = get_logger(__name__)


def run_steps(
    ctx: RunContext,
    *,
    profile: str | None = None,
    names: tuple[str, ...] | None = None,
) -> list[StepResult]:
    """Run the resolved steps in registry order, returning each step's result.

    Integrity failures (``SystemExit`` from a mapping write) propagate and abort
    the run. Per-row business errors are tallied inside each step and reflected
    in the returned results; :func:`report` summarises them.
    """
    steps: list[Step] = resolve_steps(profile=profile, names=names)
    mode = "DRY-RUN" if ctx.dry_run else "LIVE"
    logger.info(
        "Running %d step(s) [%s]: %s",
        len(steps),
        mode,
        ", ".join(s.name for s in steps),
        extra={"icon": "🚀"},
    )

    results: list[StepResult] = []
    for step in steps:
        logger.info("step: %s", step.name, extra={"icon": "▶️"})
        results.append(step.run(ctx))
    return results


def report(results: list[StepResult], *, plain: bool = False) -> str:
    """Build a consolidated, multi-line summary across all steps."""
    title = "Migration run summary" if plain else "📊 Migration run summary"
    lines = ["", "=" * 60, title, "=" * 60]
    grand = {"total": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}
    for result in results:
        lines.append(
            f"  {result.step:<24} "
            f"total={result.total} created={result.created} "
            f"updated={result.updated} skipped={result.skipped} "
            f"errors={result.error_count}"
        )
        grand["total"] += result.total
        grand["created"] += result.created
        grand["updated"] += result.updated
        grand["skipped"] += result.skipped
        grand["errors"] += result.error_count

    lines.append("-" * 60)
    lines.append(
        f"  {'TOTAL':<24} "
        f"total={grand['total']} created={grand['created']} "
        f"updated={grand['updated']} skipped={grand['skipped']} "
        f"errors={grand['errors']}"
    )

    failed = [r for r in results if r.errors]
    if failed:
        lines.append("")
        lines.append("Errors:" if plain else "❌ Errors:")
        for result in failed:
            for error in result.errors:
                lines.append(f"  [{result.step}] {error}")
    lines.append("=" * 60)
    return "\n".join(lines)


def has_errors(results: list[StepResult]) -> bool:
    """Return ``True`` if any step recorded a per-row error."""
    return any(result.errors for result in results)
