"""Tests for the `ladmig emabler` command group."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from laddel_migration.cli import app
from laddel_migration.cli_emabler import (
    CHARGERS_PATH,
    OCPP_VERSION_LABELS,
    _require_emabler,
)
from laddel_migration.config import DatabaseSettings, Settings
from laddel_migration.extract import emabler_chargers

runner = CliRunner()

_DB = DatabaseSettings(host="db", port=3306, user="u", password="p", database="d")


def test_emabler_group_is_registered() -> None:
    result = runner.invoke(app, ["emabler", "--help"])
    assert result.exit_code == 0
    assert "extract" in result.output


def test_extract_chargers_command_is_registered() -> None:
    result = runner.invoke(app, ["emabler", "extract", "chargers", "--help"])
    assert result.exit_code == 0
    assert "--page-size" in result.output
    assert "--dry-run" in result.output


def test_require_emabler_exits_when_unconfigured() -> None:
    settings = Settings(source_db=_DB, target_db=_DB, ampeco=None, emabler=None)

    with pytest.raises(typer.Exit):
        _require_emabler(settings)


def test_chargers_path_has_no_leading_base_url() -> None:
    """The path is appended to EMABLER_V2_API_URL, so it must start with a slash."""
    assert CHARGERS_PATH.startswith("/")


def test_ocpp_version_labels_cover_the_live_enum_names() -> None:
    """The API sends enum names, not the ints docs/emabler-entity.json advertises."""
    assert OCPP_VERSION_LABELS["Version16"] == "ocpp 1.6"
    assert "Unknown" in OCPP_VERSION_LABELS
    assert all(isinstance(key, str) for key in OCPP_VERSION_LABELS)


def test_extract_columns_stay_in_sync_with_the_summary_lookups() -> None:
    """`_echo_summary` indexes these by name; a rename must break loudly here."""
    assert "ocpp_version" in emabler_chargers.COLUMNS
    assert "site_id" in emabler_chargers.COLUMNS
