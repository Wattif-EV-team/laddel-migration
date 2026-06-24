"""Tests for logging helpers: the mapping breadcrumb and field-diff logger."""

from __future__ import annotations

import logging

import pytest

from laddel_migration.logging import (
    _ConsoleFormatter,
    configure_logging,
    get_logger,
    log_field_diffs,
    mapping_breadcrumb,
    render_banner,
    supports_unicode,
)


def test_mapping_breadcrumb_is_machine_readable(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_logger("test.breadcrumb")
    with caplog.at_level(logging.INFO):
        mapping_breadcrumb(logger, "Laddel|Facility|42", 1007)

    assert "MAPPING_RECORD|mapping_key=Laddel|Facility|42|target_id=1007" in caplog.text


def test_log_field_diffs_reports_only_changes(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_logger("test.diffs")
    with caplog.at_level(logging.INFO):
        changes = log_field_diffs(logger, {"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})

    assert changes == {"b": (2, 3), "c": (None, 4)}
    assert "b: 2 -> 3" in caplog.text
    assert "c: None -> 4" in caplog.text
    # Unchanged field must not be logged.
    assert "a:" not in caplog.text


def test_log_field_diffs_no_changes_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_logger("test.diffs2")
    with caplog.at_level(logging.INFO):
        changes = log_field_diffs(logger, {"a": 1}, {"a": 1})

    assert changes == {}


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging("INFO")
    configure_logging("INFO")
    # No duplicate console handlers piled up by repeated configuration.
    stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(stream_handlers) <= len(before) + 1


def test_supports_unicode_rejects_cp1252_and_accepts_utf8() -> None:
    class _Stream:
        def __init__(self, encoding: str | None) -> None:
            self.encoding = encoding

    # cp1252 cannot encode emoji, utf-8 can; an encoding-less stream is unsafe.
    assert supports_unicode(_Stream("cp1252")) is False
    assert supports_unicode(_Stream("utf-8")) is True
    assert supports_unicode(_Stream(None)) is False


def test_console_formatter_plain_prefixes_level_for_warnings() -> None:
    formatter = _ConsoleFormatter(use_color=False, use_emoji=False)
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, None)
    line = formatter.format(record)

    # Plain mode carries severity as an ASCII tag and emits no emoji/colour.
    assert line == "ERROR: boom"


def test_console_formatter_emoji_uses_record_icon_override() -> None:
    formatter = _ConsoleFormatter(use_color=False, use_emoji=True)
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "created", None, None)
    record.icon = "✅"
    line = formatter.format(record)

    assert line == "✅ created"


def test_render_banner_is_five_ascii_lines() -> None:
    art = render_banner("LADMIG")
    lines = art.splitlines()

    assert len(lines) == 5
    assert art.isascii()
