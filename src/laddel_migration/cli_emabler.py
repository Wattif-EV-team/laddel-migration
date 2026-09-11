"""``ladmig emabler`` command group: extract data from the eMabler API.

Mirrors ``ladmig sitetracker`` (see ``cli_sitetracker.py``) as a vendor-scoped
sub-app, kept out of ``cli.py`` so that module does not become a god module.

Where ``ladmig run`` *writes* to a target system, these commands *read* from the
outgoing CSMS and land the result in a `target` snapshot table, so the rest of
the migration can reach it from plain SQL.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import typer

from .clients.emabler import MAX_PAGE_SIZE, EmablerClient
from .config import Settings, require_emabler
from .db import replace_table_rows
from .extract import emabler_chargers

emabler_app = typer.Typer(
    name="emabler",
    help="Extract data from the eMabler API into the target database.",
    no_args_is_help=True,
)

extract_app = typer.Typer(
    name="extract",
    help="Extract an eMabler entity into its `target` snapshot table.",
    no_args_is_help=True,
)
emabler_app.add_typer(extract_app, name="extract")

# GET {EMABLER_V2_API_URL}/v2/chargers - operationId `getChargers`.
CHARGERS_PATH = "/v2/chargers"

# Human-readable protocol for each chargerDto.ocppVersion value.
#
# docs/emabler-entity.json declares this field as an int enum (0-3), but the
# live API returns the enum *names* as strings (verified 2026-09-10). Unknown
# keys fall back to the raw value, so a new name shows up instead of crashing.
# Used for the command summary only — the migration decodes the stored column
# in SQL.
OCPP_VERSION_LABELS: dict[str, str] = {
    "Unknown": "unreported",
    "Version15": "ocpp 1.5",
    "Version16": "ocpp 1.6",
    "Version20": "ocpp 2.0",
    "Version201": "ocpp 2.0.1",
}


def _require_emabler(settings: Settings) -> EmablerClient:
    """Return a configured :class:`EmablerClient`, or exit(1) with a FAIL message."""
    try:
        em = require_emabler(settings)
    except RuntimeError as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(code=1) from exc
    return EmablerClient(em)


@extract_app.command("chargers")
def chargers(
    page_size: int = typer.Option(
        MAX_PAGE_SIZE,
        "--page-size",
        help=f"Records per API page (1-{MAX_PAGE_SIZE}). Lower it only to debug pagination.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch and summarise without writing to the database.",
    ),
) -> None:
    """Refresh `target.emabler_charger` from the eMabler chargers endpoint.

    Fetches every page into memory first, then replaces the table's contents in
    a single transaction — so a failure part-way through leaves the previous
    snapshot intact rather than a half-loaded table.
    """
    from .config import load_settings

    settings = load_settings()
    client = _require_emabler(settings)

    typer.echo(f"Fetching chargers from {settings.emabler.safe_base_url}{CHARGERS_PATH}")  # type: ignore[union-attr]
    items = client.get_all_pages(CHARGERS_PATH, page_size=page_size)
    if not items:
        typer.echo("FAIL the API returned no chargers; refusing to empty the table.")
        raise typer.Exit(code=1)

    extracted_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    try:
        rows = [emabler_chargers.to_row(item, extracted_at) for item in items]
    except ValueError as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(code=1) from exc

    rows, duplicates = emabler_chargers.dedupe(rows)
    if duplicates:
        typer.echo(
            f"NOTE {duplicates} duplicate charger(s) dropped: the fleet changed while we "
            "walked the pages, so a record on a page boundary was returned twice. "
            "Kept the most recently read copy of each."
        )

    _echo_summary(rows)

    if dry_run:
        typer.echo(f"DRY-RUN: {len(rows)} row(s) not written to `{emabler_chargers.TABLE}`.")
        return

    try:
        written = replace_table_rows(
            settings.target_db,
            emabler_chargers.TABLE,
            emabler_chargers.COLUMNS,
            rows,
        )
    except Exception as exc:  # noqa: BLE001 - report the failure and keep the old snapshot
        typer.echo(f"FAIL writing `{emabler_chargers.TABLE}`: {exc}")
        typer.echo("The previous snapshot was rolled back into place; nothing was lost.")
        raise typer.Exit(code=1) from exc

    typer.echo(f"OK   wrote {written} row(s) to `{emabler_chargers.TABLE}`.")


def _echo_summary(rows: list[tuple[object, ...]]) -> None:
    """Echo what was fetched, highlighting the OCPP version spread.

    ``ocpp_version`` is the reason this extract exists — it is the only source
    for the per-charger protocol that `target.charge_points` currently
    hardcodes — so its distribution is worth surfacing on every run.
    """
    index = emabler_chargers.COLUMNS.index
    version_col = index("ocpp_version")
    site_col = index("site_id")

    typer.echo(f"Fetched {len(rows)} charger(s) across {len({r[site_col] for r in rows})} site(s).")
    typer.echo("OCPP version distribution:")
    versions = Counter(str(r[version_col]) for r in rows)
    for value, count in versions.most_common():
        typer.echo(f"  {value:<12} {OCPP_VERSION_LABELS.get(value, value):<12} {count}")
