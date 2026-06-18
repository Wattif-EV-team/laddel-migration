"""Command-line interface for laddel-migration."""

from __future__ import annotations

from pathlib import Path

import typer

from . import __version__
from .config import Settings, load_settings
from .db import check_connection, execute_script, run_query
from .logging import configure_logging, get_logger

app = typer.Typer(
    name="ladmig",
    help="Migrate data from the source system to the target system.",
    no_args_is_help=True,
)

logger = get_logger(__name__)

# Default directory (relative to the current working directory) holding the
# numbered DDL files that build the migration database.
DEFAULT_SQL_DIR = Path("sql")

# Key target views used by ``ladmig verify`` as a sanity check. Each is counted
# with ``SELECT COUNT(*)`` after a build to confirm it is rebuildable and
# queryable. Names are unqualified (resolved against the target database).
KEY_VIEWS: tuple[str, ...] = (
    "charge_points",
    "charging_zones",
    "id_tags",
    "location",
    "partner_admins",
    "partner_contracts",
    "partners",
    "subscription_plan",
    "tariff",
    "tariff_groups_and_base_tariff",
    "user_group_members",
    "user_groups",
    "users",
)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Initialise logging for all commands."""
    configure_logging("DEBUG" if verbose else "INFO")


@app.command()
def status() -> None:
    """Print the application status and version."""
    typer.echo(f"laddel-migration {__version__} — ready")


@app.command()
def test() -> None:
    """Check connectivity to the configured MySQL databases."""
    settings = load_settings()
    failures = 0
    for label, db in (("source", settings.source_db), ("target", settings.target_db)):
        try:
            if check_connection(db):
                typer.echo(f"OK   {label}: {db.safe_dsn}")
            else:
                typer.echo(f"FAIL {label}: unexpected response from {db.safe_dsn}")
                failures += 1
        except Exception as exc:  # noqa: BLE001 - report any connection error to the user
            typer.echo(f"FAIL {label}: {db.safe_dsn} -> {exc}")
            failures += 1

    if failures:
        raise typer.Exit(code=1)
    typer.echo("All database connections succeeded.")


def _discover_sql_files(sql_dir: Path, file: str | None) -> list[Path]:
    """Return the DDL file(s) to execute, sorted by their numeric prefix."""
    if not sql_dir.is_dir():
        typer.echo(f"FAIL sql directory not found: {sql_dir}")
        raise typer.Exit(code=1)

    if file is not None:
        path = sql_dir / file
        if not path.is_file():
            typer.echo(f"FAIL DDL file not found: {path}")
            raise typer.Exit(code=1)
        return [path]

    return sorted(sql_dir.glob("*.sql"))


@app.command()
def build(
    file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Run a single DDL file by name (e.g. 301_target_users.sql). "
        "Omit to run every *.sql file in numeric order.",
    ),
    sql_dir: Path = typer.Option(
        DEFAULT_SQL_DIR, "--sql-dir", help="Directory containing the numbered DDL files."
    ),
) -> None:
    """Rebuild the migration database by executing DDL files in order.

    Views are dropped and recreated by their own DDL. Source tables (in the
    read-only ``laddel`` database) and any future mapping tables are NEVER
    dropped — that guarantee lives in how each SQL file is written, not here.
    """
    settings = load_settings()
    sql_files = _discover_sql_files(sql_dir, file)

    if not sql_files:
        typer.echo(f"No .sql files found in {sql_dir}")
        return

    typer.echo(f"Executing {len(sql_files)} DDL file(s) against {settings.target_db.safe_dsn}")
    for path in sql_files:
        try:
            execute_script(settings.target_db, path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - report which file failed and stop
            typer.echo(f"FAIL {path.name}: {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo(f"OK   {path.name}")

    typer.echo("All DDL files executed successfully.")


@app.command()
def verify() -> None:
    """Sanity-check the build by running COUNT(*) on each key target view."""
    settings = load_settings()
    if not KEY_VIEWS:
        typer.echo("No key views configured (KEY_VIEWS is empty).")
        return

    failures = 0
    for view in KEY_VIEWS:
        try:
            _, rows = run_query(settings.target_db, f"SELECT COUNT(*) FROM `{view}`")
            count = rows[0][0] if rows else 0
            typer.echo(f"OK   {view}: {count}")
        except Exception as exc:  # noqa: BLE001 - report any view that fails to query
            typer.echo(f"FAIL {view}: {exc}")
            failures += 1

    if failures:
        raise typer.Exit(code=1)
    typer.echo("All key views verified.")


@app.command()
def sql(
    query: str = typer.Argument(..., help="The SQL query to run."),
    database: str = typer.Option(
        "target", "--database", "-d", help="Which database to query: 'source' or 'target'."
    ),
    csv: bool = typer.Option(False, "--csv", help="Output as CSV instead of an aligned table."),
) -> None:
    """Run an ad-hoc read query for schema/data inspection.

    Reusable helper for inspecting the production databases (e.g. SHOW CREATE
    VIEW, DESCRIBE, SELECT). For anything beyond a quick look, write a
    throwaway script under scratch/ instead.
    """
    settings = load_settings()
    db = _select_db(settings, database)

    columns, rows = run_query(db, query)
    _print_result(columns, rows, csv=csv)


def _select_db(settings: Settings, database: str):
    """Resolve the ``--database`` option to a configured database."""
    key = database.lower()
    if key == "source":
        return settings.source_db
    if key == "target":
        return settings.target_db
    typer.echo(f"FAIL unknown database '{database}' (expected 'source' or 'target')")
    raise typer.Exit(code=1)


def _print_result(columns: list[str], rows: list[tuple[object, ...]], *, csv: bool) -> None:
    """Render a query result as CSV or an aligned text table."""
    if not columns:
        typer.echo("(no result set)")
        return

    if csv:
        import csv as _csv
        import io

        buffer = io.StringIO()
        writer = _csv.writer(buffer)
        writer.writerow(columns)
        writer.writerows(rows)
        typer.echo(buffer.getvalue().rstrip("\n"))
        return

    widths = [len(c) for c in columns]
    str_rows = [[("" if v is None else str(v)) for v in row] for row in rows]
    for row in str_rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    header = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    typer.echo(header)
    typer.echo("  ".join("-" * widths[i] for i in range(len(columns))))
    for row in str_rows:
        typer.echo("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))
    typer.echo(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


if __name__ == "__main__":
    app()
