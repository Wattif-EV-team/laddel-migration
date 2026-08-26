"""``ladmig sitetracker`` command group: ad-hoc SOQL queries and schema inspection.

Mirrors the existing ``ladmig sql`` command (see ``cli.py``) but targets the
SiteTracker (Salesforce) org instead of the MySQL databases. Kept as its own
module — rather than growing ``cli.py`` further — per the project's convention
of graduating reusable scratch-script behaviour into a proper subcommand
without letting a single CLI module become a god module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from .clients.sitetracker import SiteTrackerClient
from .clients.sitetracker_describe import ChangedField, DescribeDiff, diff_describes
from .config import Settings, require_sitetracker

sitetracker_app = typer.Typer(
    name="sitetracker",
    help="Query and inspect the SiteTracker (Salesforce) org.",
    no_args_is_help=True,
)

# Where live describe snapshots are read from / written to. Authoritative copies
# backing the "confirmed live" claims in docs/sitetracker-reference.md.
REPO_ROOT = Path(__file__).resolve().parents[2]
DESCRIBE_SNAPSHOT_DIR = REPO_ROOT / "docs" / "sitetracker-describes"


def _snapshot_path(sobject: str) -> Path:
    return DESCRIBE_SNAPSHOT_DIR / f"sitetracker_describe_{sobject}.json"


def _require_sitetracker(settings: Settings) -> SiteTrackerClient:
    """Return a configured :class:`SiteTrackerClient`, or exit(1) with a FAIL message."""
    try:
        st = require_sitetracker(settings)
    except RuntimeError as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(code=1) from exc
    return SiteTrackerClient(st)


def _append_limit(query: str, limit: int | None) -> str:
    """Append a ``LIMIT`` clause to ``query`` for ``--limit`` sampling.

    Raises a clear CLI error (rather than sending invalid double-LIMIT SOQL to
    Salesforce) if the query already has its own ``LIMIT`` clause.
    """
    if limit is None:
        return query
    if "limit" in query.lower():
        typer.echo(
            "FAIL query already contains a LIMIT clause; remove --limit or the inline LIMIT."
        )
        raise typer.Exit(code=1)
    return f"{query} LIMIT {limit}"


def _records_to_table(records: list[dict[str, Any]]) -> tuple[list[str], list[tuple[object, ...]]]:
    """Convert SOQL result records into ``(columns, rows)`` for the table/CSV renderer.

    Drops Salesforce's ``attributes`` envelope key, takes column order from the
    first record, and JSON-stringifies any nested dict/list value (relationship
    subquery results) so every cell is a flat, printable scalar.
    """
    if not records:
        return [], []

    columns = [key for key in records[0] if key != "attributes"]
    rows: list[tuple[object, ...]] = []
    for record in records:
        row = []
        for column in columns:
            value = record.get(column)
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            row.append(value)
        rows.append(tuple(row))
    return columns, rows


@sitetracker_app.command("soql")
def soql(
    query: str = typer.Argument(..., help="The SOQL query to run."),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Append a LIMIT clause for quick sampling."
    ),
    csv: bool = typer.Option(False, "--csv", help="Output as CSV instead of an aligned table."),
) -> None:
    """Run an ad-hoc SOQL query against SiteTracker, auto-paginating all pages."""
    from .cli import _print_result
    from .config import load_settings

    settings = load_settings()
    client = _require_sitetracker(settings)

    effective_query = _append_limit(query, limit)
    records = client.query(effective_query)
    columns, rows = _records_to_table(records)
    _print_result(columns, rows, csv=csv)


_FIELD_COLUMNS = ("name", "type", "label", "length", "createable", "updateable", "picklist")


def _describe_field_rows(describe: dict[str, Any]) -> list[tuple[object, ...]]:
    """Build one row per field for the default ``describe`` table output."""
    from .clients.sitetracker_describe import picklist_values

    rows: list[tuple[object, ...]] = []
    for f in describe["fields"]:
        values = picklist_values(f)
        picklist = ", ".join(values)
        if len(picklist) > 60:
            picklist = picklist[:57] + "..."
        rows.append(
            (
                f["name"],
                f["type"],
                f["label"],
                f.get("length", ""),
                f["createable"],
                f["updateable"],
                picklist,
            )
        )
    return rows


def _print_diff(sobject: str, diff: DescribeDiff) -> None:
    """Print a NEW / REMOVED / CHANGED field summary for a describe diff."""
    typer.echo(f"\nDiff for {sobject} (vs. previous saved snapshot):")

    if diff.new_fields:
        typer.echo(f"  NEW fields ({len(diff.new_fields)}): {', '.join(diff.new_fields)}")
    else:
        typer.echo("  NEW fields: none")

    if diff.removed_fields:
        typer.echo(
            f"  REMOVED fields ({len(diff.removed_fields)}): {', '.join(diff.removed_fields)}"
        )
    else:
        typer.echo("  REMOVED fields: none")

    if diff.changed_fields:
        typer.echo(f"  CHANGED fields ({len(diff.changed_fields)}):")
        for changed in diff.changed_fields:
            typer.echo(f"    {_format_changed_field(changed)}")
    else:
        typer.echo("  CHANGED fields: none")


def _format_changed_field(changed: ChangedField) -> str:
    parts = [changed.name + ":"]
    if changed.type_changed:
        parts.append(f"type {changed.old_type}->{changed.new_type}")
    if changed.label_changed:
        parts.append(f"label {changed.old_label!r}->{changed.new_label!r}")
    if changed.picklist_added:
        parts.append(f"picklist +{changed.picklist_added}")
    if changed.picklist_removed:
        parts.append(f"picklist -{changed.picklist_removed}")
    return " ".join(parts)


@sitetracker_app.command("describe")
def describe(
    sobject: str = typer.Argument(..., help="The sObject API name, e.g. sitetracker__Site__c."),
    json_output: bool = typer.Option(
        False, "--json", help="Print the raw describe JSON instead of a field table."
    ),
    save: bool = typer.Option(
        False,
        "--save",
        help=f"Save the describe JSON to {DESCRIBE_SNAPSHOT_DIR.as_posix()}/.",
    ),
    diff: bool = typer.Option(
        False,
        "--diff",
        help="Diff against the previously saved snapshot (if any) before printing/saving.",
    ),
) -> None:
    """Show sObject field metadata: name, type, label, length, picklist values."""
    from .cli import _print_result
    from .config import load_settings

    settings = load_settings()
    client = _require_sitetracker(settings)

    fresh = client.get(f"/sobjects/{sobject}/describe/")

    if diff:
        snapshot_path = _snapshot_path(sobject)
        if snapshot_path.exists():
            old = json.loads(snapshot_path.read_text(encoding="utf-8", errors="replace"))
            _print_diff(sobject, diff_describes(old, fresh))
        else:
            typer.echo(f"(no previous snapshot at {snapshot_path}, skipping diff)")

    if save:
        DESCRIBE_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path = _snapshot_path(sobject)
        snapshot_path.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
        typer.echo(f"Saved -> {snapshot_path}")

    if json_output:
        typer.echo(json.dumps(fresh, indent=2))
    else:
        _print_result(list(_FIELD_COLUMNS), _describe_field_rows(fresh), csv=False)


@sitetracker_app.command("list")
def list_sobjects(
    csv: bool = typer.Option(False, "--csv", help="Output as CSV instead of an aligned table."),
) -> None:
    """List every sObject available in the org (name, label, custom, queryable)."""
    from .cli import _print_result
    from .config import load_settings

    settings = load_settings()
    client = _require_sitetracker(settings)

    body = client.get("/sobjects/")
    columns = ["name", "label", "custom", "queryable", "createable", "updateable"]
    rows = [tuple(entry.get(c) for c in columns) for entry in body.get("sobjects", [])]
    _print_result(columns, rows, csv=csv)
