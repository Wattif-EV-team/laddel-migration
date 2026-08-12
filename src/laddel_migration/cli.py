"""Command-line interface for laddel-migration."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from . import __version__
from .config import Settings, load_settings
from .db import check_connection, execute_script, run_query
from .logging import configure_logging, get_logger, render_banner, supports_unicode

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
    "sitetracker_accounts",
    "tariff",
    "tariff_groups_and_base_tariff",
    "user_group_members",
    "user_groups",
    "users",
)

# A cheap authenticated GET used by ``ladmig test`` to confirm the Ampeco API is
# reachable and the token is accepted. The Ampeco API has no dedicated ping/health
# endpoint, so we reuse the partners listing (returns 200 even with zero partners).
_AMPECO_PING_PATH = "/public-api/resources/partners/v2.0"


# The logging level chosen by the root callback, reused by commands that add a
# file handler (e.g. ``run``) so they don't downgrade an explicit ``--verbose``.
_LOG_LEVEL: str = "INFO"

# Whether output should be ASCII-only with no colour/emoji, set by ``--plain``.
# Reused by commands for the banner, option echo, logging and the run report.
_PLAIN: bool = False


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="ASCII-only output: no colour, emoji or banners. For CI, agents and tests.",
    ),
) -> None:
    """Initialise logging for all commands."""
    global _LOG_LEVEL, _PLAIN
    _LOG_LEVEL = "DEBUG" if verbose else "INFO"
    # Force plain output when the console codec can't encode our glyphs (e.g. a
    # Windows cp1252 terminal), so we degrade to ASCII instead of crashing.
    _PLAIN = plain or not supports_unicode(sys.stdout)
    configure_logging(_LOG_LEVEL, plain=_PLAIN)


@app.command()
def status() -> None:
    """Print the application status and version."""
    typer.echo(f"laddel-migration {__version__} - ready")


@app.command()
def test() -> None:
    """Check connectivity to the configured MySQL databases (and SiteTracker)."""
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

    # When SiteTracker is configured, confirm the OAuth password grant works by
    # fetching a token. Skipped silently when its credentials are not set.
    if settings.sitetracker is not None:
        from .clients.sitetracker import SiteTrackerClient

        st = settings.sitetracker
        try:
            SiteTrackerClient(st).authenticate()
            typer.echo(f"OK   sitetracker: {st.safe_instance_url}")
        except Exception as exc:  # noqa: BLE001 - report any auth error to the user
            typer.echo(f"FAIL sitetracker: {st.safe_instance_url} -> {exc}")
            failures += 1

    # When Ampeco is configured, confirm the API is reachable and the bearer
    # token is accepted by making one cheap authenticated GET. Skipped silently
    # when its credentials are not set.
    if settings.ampeco is not None:
        from .clients.ampeco import AmpecoClient

        am = settings.ampeco
        try:
            AmpecoClient(am).get(_AMPECO_PING_PATH)
            typer.echo(f"OK   ampeco: {am.safe_base_url}")
        except Exception as exc:  # noqa: BLE001 - report any API error to the user
            typer.echo(f"FAIL ampeco: {am.safe_base_url} -> {exc}")
            failures += 1

    if failures:
        raise typer.Exit(code=1)
    typer.echo("All connections succeeded.")


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
def run(
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Named step profile to run (e.g. 'all', 'ampeco', 'partners'). Defaults to 'all'.",
    ),
    step: list[str] | None = typer.Option(
        None,
        "--step",
        "-s",
        help="Run a specific step by name; repeatable. Combines with --profile.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build and log payloads without calling the API or writing mappings.",
    ),
) -> None:
    """Run create-or-update migration steps against the target system.

    Steps are run in dependency order. A mapping-write failure aborts the run
    immediately (to avoid orphaned remote resources); per-row business errors
    are collected and reported, and make the command exit non-zero.
    """
    from .clients.ampeco import AmpecoClient
    from .clients.sitetracker import SiteTrackerClient
    from .runner.context import RunContext
    from .runner.orchestrator import has_errors, report, run_steps

    _print_banner()
    _echo_run_options(profile, step, dry_run=dry_run)

    log_path = configure_logging(_LOG_LEVEL, log_to_file=True, script="run", plain=_PLAIN)
    if log_path is not None:
        typer.echo(f"Logging to {log_path}")

    settings = load_settings()
    names = tuple(step) if step else None

    # Build whichever target-system clients are configured; a step that needs an
    # unconfigured client fails clearly via RunContext.client_for. In a dry run
    # no clients are built (and none are used).
    client = None
    sitetracker = None
    if dry_run:
        typer.echo("DRY-RUN: no API calls or mapping writes will be made.")
    else:
        if settings.ampeco is not None:
            client = AmpecoClient(settings.ampeco)
        if settings.sitetracker is not None:
            sitetracker = SiteTrackerClient(settings.sitetracker)

    ctx = RunContext(settings=settings, client=client, sitetracker=sitetracker, dry_run=dry_run)
    try:
        results = run_steps(ctx, profile=profile, names=names)
    except KeyError as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(code=2) from exc

    typer.echo(report(results, plain=_PLAIN))
    if has_errors(results):
        raise typer.Exit(code=1)


def _print_banner() -> None:
    """Print the start-up banner (coloured unless ``--plain``)."""
    art = render_banner("LADMIG")
    subtitle = "laddel -> Ampeco migration" if _PLAIN else "laddel \u2192 Ampeco migration"
    if _PLAIN:
        typer.echo(art)
        typer.echo(f"{subtitle}  v{__version__}")
    else:
        typer.secho(art, fg=typer.colors.CYAN, bold=True)
        typer.secho(f"{subtitle}  v{__version__}", fg=typer.colors.BRIGHT_BLACK)


def _echo_run_options(profile: str | None, step: list[str] | None, *, dry_run: bool) -> None:
    """Echo the effective run options so a run is self-documenting."""
    options = {
        "profile": profile or "all",
        "steps": ", ".join(step) if step else "(from profile)",
        "dry-run": str(dry_run),
        "verbose": str(_LOG_LEVEL == "DEBUG"),
        "plain": str(_PLAIN),
    }
    heading = "Run options:"
    if _PLAIN:
        typer.echo(heading)
        for key, value in options.items():
            typer.echo(f"  {key}: {value}")
    else:
        typer.secho(heading, fg=typer.colors.CYAN, bold=True)
        for key, value in options.items():
            typer.echo(f"  {typer.style(key, fg=typer.colors.BRIGHT_BLACK)}: {value}")


@app.command()
def steps() -> None:
    """List the registered migration steps and the available run profiles."""
    from .runner.registry import PROFILES, STEPS

    typer.echo("Steps (in run order):")
    for item in STEPS:
        suffix = f" - {item.description}" if item.description else ""
        typer.echo(f"  {item.name}{suffix}")

    typer.echo("\nProfiles:")
    for name, members in PROFILES.items():
        typer.echo(f"  {name}: {', '.join(members)}")


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
