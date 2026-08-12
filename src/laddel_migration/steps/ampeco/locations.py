"""Create-or-update step for Ampeco locations.

Source of truth is the ``target.location`` view (``sql/304_target_location.sql``),
which already encodes the SQL-side business rules (batch gate, derived externalId,
per-locale translation columns). This module's only job is to turn each view row
into the Ampeco location payload: apply field datatypes the view cannot carry
(``geoposition`` as floats, ``workingHours.isAlwaysOpen`` as a JSON boolean), fold
the ``<field>_<locale>`` columns into ``[{locale, translation}]`` arrays, and parse
the ``tags`` JSON-array string. Locations have no reliable natural key, so there is
no lookup/adopt: every unmapped row is a fresh create.
"""

from __future__ import annotations

import json
from typing import Any

from ...payload import coerce, nest, prune_none, translation_list
from ...runner.context import RunContext, StepResult
from ..base import run_create_or_update

# Ampeco "create location" endpoint (POST); update is PATCH `{PATH}/{id}`.
_LOCATIONS_PATH = "/public-api/resources/locations/v2.0"

# Scalar payload columns emitted by `target.location`. Nested Ampeco objects use
# `_` as the separator (e.g. `geoposition_latitude`) and are re-nested by nest().
_STRING_FIELDS: tuple[str, ...] = (
    "externalId",
    "city",
    "postCode",
    "country",
    "region",
)

_FLOAT_FIELDS: tuple[str, ...] = (
    "geoposition_latitude",
    "geoposition_longitude",
)

_BOOL_FIELDS: tuple[str, ...] = ("workingHours_isAlwaysOpen",)

# Ampeco translated fields (arrays of {locale, translation}) and the view's
# per-locale column prefix that feeds each one.
_TRANSLATED_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "name_"),
    ("address", "address_"),
    ("streetAddress", "streetAddress_"),
    ("shortDescription", "shortDescription_"),
    ("description", "description_"),
)


class LocationsResource:
    """Resource description consumed by :func:`run_create_or_update`."""

    name = "locations"
    view = "location"
    mapping_table = "location_mapping"
    key_column = "mapping_key"
    id_column = "target_location_id"
    path = _LOCATIONS_PATH
    target_system = "ampeco"

    def build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for field in _STRING_FIELDS:
            flat[field] = coerce(row.get(field), str)
        for field in _FLOAT_FIELDS:
            flat[field] = coerce(row.get(field), float)
        for field in _BOOL_FIELDS:
            flat[field] = coerce(row.get(field), bool)
        payload = nest(flat)

        for api_field, prefix in _TRANSLATED_FIELDS:
            values = translation_list(row, prefix)
            if values:
                payload[api_field] = values

        raw_tags = row.get("tags")
        if raw_tags:
            payload["tags"] = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags

        return prune_none(payload)

    def mapping_values(self, row: dict[str, Any], target_id: object) -> dict[str, object]:
        return {
            "mapping_key": row["mapping_key"],
            "target_location_id": target_id,
        }


def run(ctx: RunContext) -> StepResult:
    """Entry point registered in the runner's step registry."""
    return run_create_or_update(ctx, LocationsResource())
