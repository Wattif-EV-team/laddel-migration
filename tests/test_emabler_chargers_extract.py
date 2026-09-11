"""Tests for flattening eMabler ``chargerDto`` items into emabler_charger rows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from laddel_migration.extract import emabler_chargers
from laddel_migration.extract.emabler_chargers import COLUMNS, to_row

_EXTRACTED_AT = datetime(2026, 9, 10, 12, 0, 0)


def _item(**overrides: Any) -> dict[str, Any]:
    """A representative chargerDto item, overridable per test."""
    item: dict[str, Any] = {
        "id": 4711,
        "chargerId": "NOR12345",
        "ocppVersion": "Version16",
        "name": "Garasje 1",
        "siteId": 900,
        "siteName": "Orebakken Borettslag",
        "evseId": "NO*WAT*E12345",
        "manufacturer": "Zaptec",
        "model": "Pro",
        "firmware": "3.2.1",
        "serial": "ZAP001",
        "chargerType": "AC",
        "ocpiIntegrationEnabled": False,
        "splitEvseBySocket": True,
        "state": "Online",
        "lastSeen": "2026-09-10T09:15:30Z",
        "activeConnection": True,
        "sockets": [{"socketId": 1}, {"socketId": 2}],
        "installationDate": "2021-04-01T00:00:00Z",
        "createdAt": "2021-04-01T08:30:00Z",
        "updatedAt": "2026-09-01T10:00:00Z",
    }
    item.update(overrides)
    return item


def _field(row: tuple[object, ...], name: str) -> object:
    return row[COLUMNS.index(name)]


def test_row_matches_the_column_order() -> None:
    row = to_row(_item(), _EXTRACTED_AT)

    assert len(row) == len(COLUMNS)
    assert _field(row, "emabler_id") == 4711
    assert _field(row, "charger_id") == "NOR12345"
    assert _field(row, "ocpp_version") == "Version16"
    assert _field(row, "site_id") == 900
    assert _field(row, "manufacturer") == "Zaptec"
    assert _field(row, "extracted_at") == _EXTRACTED_AT


def test_ocpp_version_is_stored_verbatim_as_the_api_sends_it() -> None:
    """The spec says int enum 0-3; the live API sends the enum names as strings.

    We store whatever arrives rather than coercing, so a spec/API disagreement
    cannot silently null out the one field this whole extract exists for.
    """
    assert _field(to_row(_item(ocppVersion="Unknown"), _EXTRACTED_AT), "ocpp_version") == "Unknown"
    assert _field(to_row(_item(ocppVersion=1), _EXTRACTED_AT), "ocpp_version") == "1"


def test_booleans_become_mysql_ints_and_none_stays_null() -> None:
    row = to_row(_item(activeConnection=None), _EXTRACTED_AT)

    assert _field(row, "ocpi_integration_enabled") == 0
    assert _field(row, "split_evse_by_socket") == 1
    assert _field(row, "active_connection") is None


def test_socket_count_is_derived_and_sockets_stay_in_raw_json() -> None:
    row = to_row(_item(), _EXTRACTED_AT)

    assert _field(row, "socket_count") == 2
    assert "socket_count" not in json.loads(str(_field(row, "raw_json")))
    assert json.loads(str(_field(row, "raw_json")))["sockets"] == [
        {"socketId": 1},
        {"socketId": 2},
    ]


def test_raw_json_preserves_fields_that_have_no_column() -> None:
    row = to_row(_item(iccid="8947", location={"city": "Oslo"}), _EXTRACTED_AT)

    raw = json.loads(str(_field(row, "raw_json")))
    assert raw["iccid"] == "8947"
    assert raw["location"] == {"city": "Oslo"}


def test_timestamps_are_converted_to_naive_utc() -> None:
    row = to_row(_item(lastSeen="2026-09-10T11:15:30+02:00"), _EXTRACTED_AT)

    assert _field(row, "last_seen") == datetime(2026, 9, 10, 9, 15, 30)


def test_dotnet_seven_digit_fractional_seconds_are_parsed() -> None:
    """.NET emits one more fractional digit than `fromisoformat` accepts."""
    row = to_row(_item(createdAt="2021-04-01T08:30:00.1234567Z"), _EXTRACTED_AT)

    assert _field(row, "created_at") == datetime(2021, 4, 1, 8, 30, 0)


def test_unparseable_timestamp_becomes_null_without_failing_the_row() -> None:
    row = to_row(_item(updatedAt="not a date"), _EXTRACTED_AT)

    assert _field(row, "updated_at") is None
    assert _field(row, "emabler_id") == 4711


def test_dotnet_min_value_sentinel_becomes_null() -> None:
    """eMabler sends year 1 as a "never seen" marker; MySQL DATETIME starts at 1000."""
    row = to_row(_item(lastSeen="0001-01-01T00:00:00Z"), _EXTRACTED_AT)

    assert _field(row, "last_seen") is None


def test_missing_optional_fields_become_null() -> None:
    row = to_row({"id": 1}, _EXTRACTED_AT)

    assert _field(row, "charger_id") is None
    assert _field(row, "ocpp_version") is None
    assert _field(row, "socket_count") == 0


def test_over_long_text_is_truncated_to_the_ddl_width() -> None:
    row = to_row(_item(state="Online" * 20), _EXTRACTED_AT)

    assert len(str(_field(row, "state"))) == emabler_chargers._WIDTHS["state"]


def test_missing_id_raises_because_it_is_the_primary_key() -> None:
    with pytest.raises(ValueError, match="no numeric 'id'"):
        to_row(_item(id=None), _EXTRACTED_AT)


def test_dedupe_keeps_the_last_copy_of_a_repeated_charger() -> None:
    """Offset pagination re-sends boundary rows when the fleet changes mid-walk."""
    first = to_row(_item(id=7, state="Offline"), _EXTRACTED_AT)
    second = to_row(_item(id=7, state="Available"), _EXTRACTED_AT)
    other = to_row(_item(id=8), _EXTRACTED_AT)

    rows, dropped = emabler_chargers.dedupe([first, other, second])

    assert dropped == 1
    assert [_field(r, "emabler_id") for r in rows] == [7, 8]
    assert _field(rows[0], "state") == "Available"


def test_dedupe_is_a_no_op_without_duplicates() -> None:
    rows = [to_row(_item(id=i), _EXTRACTED_AT) for i in (1, 2, 3)]

    deduped, dropped = emabler_chargers.dedupe(rows)

    assert dropped == 0
    assert deduped == rows
