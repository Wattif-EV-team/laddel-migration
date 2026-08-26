"""Tests for the `ladmig sitetracker` command group."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from laddel_migration.cli import app
from laddel_migration.cli_sitetracker import (
    _append_limit,
    _records_to_table,
    _require_sitetracker,
)
from laddel_migration.config import DatabaseSettings, Settings

runner = CliRunner()

_DB = DatabaseSettings(host="db", port=3306, user="u", password="p", database="d")


def test_sitetracker_group_is_registered() -> None:
    result = runner.invoke(app, ["sitetracker", "--help"])
    assert result.exit_code == 0
    assert "soql" in result.output
    assert "describe" in result.output
    assert "list" in result.output


def test_soql_command_is_registered() -> None:
    result = runner.invoke(app, ["sitetracker", "soql", "--help"])
    assert result.exit_code == 0
    assert "--limit" in result.output
    assert "--csv" in result.output


def test_describe_command_is_registered() -> None:
    result = runner.invoke(app, ["sitetracker", "describe", "--help"])
    assert result.exit_code == 0
    assert "--json" in result.output
    assert "--save" in result.output
    assert "--diff" in result.output


def test_list_command_is_registered() -> None:
    result = runner.invoke(app, ["sitetracker", "list", "--help"])
    assert result.exit_code == 0
    assert "--csv" in result.output


def test_append_limit_returns_query_unchanged_when_no_limit() -> None:
    assert _append_limit("SELECT Id FROM Account", None) == "SELECT Id FROM Account"


def test_append_limit_appends_clause() -> None:
    assert _append_limit("SELECT Id FROM Account", 5) == "SELECT Id FROM Account LIMIT 5"


def test_append_limit_errors_when_query_already_has_limit() -> None:
    with pytest.raises(typer.Exit):
        _append_limit("SELECT Id FROM Account LIMIT 10", 5)


def test_records_to_table_drops_attributes_and_orders_by_first_record() -> None:
    records = [
        {"attributes": {"type": "Account"}, "Id": "001A", "Name": "Acme"},
        {"attributes": {"type": "Account"}, "Id": "001B", "Name": "Globex"},
    ]

    columns, rows = _records_to_table(records)

    assert columns == ["Id", "Name"]
    assert rows == [("001A", "Acme"), ("001B", "Globex")]


def test_records_to_table_stringifies_nested_values() -> None:
    records = [
        {
            "attributes": {"type": "Account"},
            "Id": "001A",
            "Contacts": {"totalSize": 1, "records": [{"Id": "003A"}]},
        }
    ]

    columns, rows = _records_to_table(records)

    assert columns == ["Id", "Contacts"]
    assert isinstance(rows[0][1], str)
    assert "003A" in rows[0][1]


def test_records_to_table_empty_returns_empty() -> None:
    assert _records_to_table([]) == ([], [])


def test_require_sitetracker_fails_when_unconfigured(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(source_db=_DB, target_db=_DB, ampeco=None, sitetracker=None)

    with pytest.raises(typer.Exit):
        _require_sitetracker(settings)

    assert "FAIL" in capsys.readouterr().out
