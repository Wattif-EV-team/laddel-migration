"""Smoke tests for the CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from laddel_migration import __version__
from laddel_migration.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ladmig" in result.output or "Usage" in result.output


def test_status() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_test_command_is_registered() -> None:
    result = runner.invoke(app, ["test", "--help"])
    assert result.exit_code == 0
    assert "connectivity" in result.output.lower()
