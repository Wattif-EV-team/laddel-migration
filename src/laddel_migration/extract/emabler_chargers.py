"""Flatten eMabler ``chargerDto`` items into `target.emabler_charger` rows.

Pure functions only — no HTTP, no database. The column list and the widths
below MUST stay in step with ``sql/010_emabler_charger.sql``; that file is the
authority, this module just has to agree with it.

Only the scalar fields of ``chargerDto`` become columns. Nested arrays and
objects (``sockets``, ``location``, ``chargerConfigurations``, ...) are left in
the ``raw_json`` passthrough so they stay reachable via MySQL JSON functions
without re-running the extract.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from ..logging import get_logger

logger = get_logger(__name__)

TABLE = "emabler_charger"

# Column order for the INSERT. Must match the tuple built by :func:`to_row`.
COLUMNS: tuple[str, ...] = (
    "emabler_id",
    "charger_id",
    "ocpp_version",
    "name",
    "site_id",
    "site_name",
    "evse_id",
    "manufacturer",
    "model",
    "firmware",
    "serial",
    "charger_type",
    "ocpi_integration_enabled",
    "split_evse_by_socket",
    "state",
    "last_seen",
    "active_connection",
    "socket_count",
    "installation_date",
    "created_at",
    "updated_at",
    "raw_json",
    "extracted_at",
)

# VARCHAR widths from the DDL, so an over-long API value is truncated here (with
# a warning) instead of failing the INSERT under MySQL strict mode.
_WIDTHS: dict[str, int] = {
    "charger_id": 255,
    "ocpp_version": 32,
    "name": 255,
    "site_name": 255,
    "evse_id": 64,
    "manufacturer": 255,
    "model": 255,
    "firmware": 128,
    "serial": 128,
    "charger_type": 16,
    "state": 32,
}

# .NET serialises timestamps with 7 fractional digits, which is one more than
# `datetime.fromisoformat` accepts. Trim the excess before parsing.
_LONG_FRACTION_RE = re.compile(r"(\.\d{6})\d+")

# MySQL's DATETIME range starts at year 1000. eMabler reports .NET's
# DateTime.MinValue ('0001-01-01T00:00:00Z') as a "never" sentinel, which would
# otherwise fail the INSERT under strict mode.
_MIN_MYSQL_YEAR = 1000


def to_row(item: dict[str, Any], extracted_at: datetime) -> tuple[object, ...]:
    """Convert one ``chargerDto`` item into a row tuple ordered like :data:`COLUMNS`.

    Raises ``ValueError`` if the item has no usable ``id``. That is the table's
    primary key, so its absence means the API shape changed and the whole
    extract should stop loudly rather than load a partial snapshot.
    """
    emabler_id = _int(item.get("id"), "id")
    if emabler_id is None:
        raise ValueError(f"eMabler charger item has no numeric 'id': {item!r}")

    sockets = item.get("sockets") or []

    return (
        emabler_id,
        _text(item.get("chargerId"), "charger_id"),
        _text(item.get("ocppVersion"), "ocpp_version"),
        _text(item.get("name"), "name"),
        _int(item.get("siteId"), "site_id"),
        _text(item.get("siteName"), "site_name"),
        _text(item.get("evseId"), "evse_id"),
        _text(item.get("manufacturer"), "manufacturer"),
        _text(item.get("model"), "model"),
        _text(item.get("firmware"), "firmware"),
        _text(item.get("serial"), "serial"),
        _text(item.get("chargerType"), "charger_type"),
        _bool(item.get("ocpiIntegrationEnabled")),
        _bool(item.get("splitEvseBySocket")),
        _text(item.get("state"), "state"),
        _dt(item.get("lastSeen"), "lastSeen"),
        _bool(item.get("activeConnection")),
        len(sockets) if isinstance(sockets, list) else 0,
        _dt(item.get("installationDate"), "installationDate"),
        _dt(item.get("createdAt"), "createdAt"),
        _dt(item.get("updatedAt"), "updatedAt"),
        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
        extracted_at,
    )


def _text(value: object, column: str) -> str | None:
    """Return ``value`` as text, truncated to the column's DDL width."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    width = _WIDTHS[column]
    if len(text) > width:
        logger.warning(
            "Truncating %s: %d chars exceeds the column width of %d (%r)",
            column,
            len(text),
            width,
            text[:60] + "...",
        )
        return text[:width]
    return text


def _int(value: object, field: str) -> int | None:
    """Return ``value`` as an int, or ``None`` if it is absent or not numeric."""
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        logger.warning("Ignoring non-numeric %s: %r", field, value)
        return None


def _bool(value: object) -> int | None:
    """Return ``value`` as MySQL's 0/1, preserving a nullable tri-state."""
    if value is None:
        return None
    return 1 if value else 0


def _dt(value: object, field: str) -> datetime | None:
    """Parse an ISO 8601 timestamp into a naive UTC ``datetime`` for MySQL.

    Offsets are normalised to UTC and dropped (MySQL ``DATETIME`` is
    timezone-naive), and sub-second precision is discarded. An unparseable
    value becomes ``NULL`` with a warning rather than failing the extract — a
    bad timestamp is not worth losing the whole snapshot over.

    A year before :data:`_MIN_MYSQL_YEAR` is also ``NULL``: eMabler sends .NET's
    ``DateTime.MinValue`` as a "never" sentinel, and MySQL cannot store it.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _LONG_FRACTION_RE.sub(r"\1", str(value).strip())
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            logger.warning("Ignoring unparseable %s timestamp: %r", field, value)
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    if parsed.year < _MIN_MYSQL_YEAR:
        # A routine "never happened" sentinel, so debug rather than warn.
        logger.debug("Treating out-of-range %s timestamp as NULL: %r", field, value)
        return None
    return parsed.replace(microsecond=0)
