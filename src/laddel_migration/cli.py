"""Command-line interface for laddel-migration."""

from __future__ import annotations

import typer

from . import __version__
from .config import load_settings
from .db import check_connection
from .logging import configure_logging, get_logger

app = typer.Typer(
    name="ladmig",
    help="Migrate data from the source system to the target system.",
    no_args_is_help=True,
)

logger = get_logger(__name__)


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


if __name__ == "__main__":
    app()
