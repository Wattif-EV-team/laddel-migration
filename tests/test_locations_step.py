"""Test the location payload builder: translation folding, nesting, tags, types."""

from __future__ import annotations

from typing import Any

from laddel_migration.steps.ampeco.locations import LocationsResource


def _view_row() -> dict[str, Any]:
    """A row shaped like `target.location` output (per-locale columns, 0/1 bool)."""
    return {
        "mapping_key": "Laddel|Location|123",
        "source_label": "Fac A (fac=123)",
        "target_location_id": None,
        "name_en": "Fac A",
        "name_nb-NO": "Fac A",
        "externalId": "W047L0123",
        "geoposition_latitude": 59.9,
        "geoposition_longitude": 10.7,
        "address_en": "Road 1, 0123 Oslo",
        "address_nb-NO": "Road 1, 0123 Oslo",
        "streetAddress_en": "Road 1",
        "streetAddress_nb-NO": "Road 1",
        "city": "Oslo",
        "postCode": "0123",
        "country": "NO",
        "region": "",
        "shortDescription_en": "Road 1, 0123 Oslo",
        "shortDescription_nb-NO": "Road 1, 0123 Oslo",
        "description_en": None,
        "description_nb-NO": None,
        "workingHours_isAlwaysOpen": 1,
        "tags": '["Owner:Customer","Source:Laddel"]',
    }


def test_build_payload_matches_ampeco_shape() -> None:
    payload = LocationsResource().build_payload(_view_row())

    assert payload == {
        "externalId": "W047L0123",
        "geoposition": {"latitude": 59.9, "longitude": 10.7},
        "city": "Oslo",
        "postCode": "0123",
        "country": "NO",
        "region": "",
        "workingHours": {"isAlwaysOpen": True},
        "name": [
            {"locale": "en", "translation": "Fac A"},
            {"locale": "nb-NO", "translation": "Fac A"},
        ],
        "address": [
            {"locale": "en", "translation": "Road 1, 0123 Oslo"},
            {"locale": "nb-NO", "translation": "Road 1, 0123 Oslo"},
        ],
        "streetAddress": [
            {"locale": "en", "translation": "Road 1"},
            {"locale": "nb-NO", "translation": "Road 1"},
        ],
        "shortDescription": [
            {"locale": "en", "translation": "Road 1, 0123 Oslo"},
            {"locale": "nb-NO", "translation": "Road 1, 0123 Oslo"},
        ],
        "tags": ["Owner:Customer", "Source:Laddel"],
    }


def test_empty_description_is_omitted() -> None:
    # Both description locales are NULL in the source → no `description` key at all.
    payload = LocationsResource().build_payload(_view_row())
    assert "description" not in payload


def test_description_is_folded_when_present() -> None:
    row = _view_row()
    row["description_en"] = "Site info"
    row["description_nb-NO"] = "Nettstedinfo"
    payload = LocationsResource().build_payload(row)
    assert payload["description"] == [
        {"locale": "en", "translation": "Site info"},
        {"locale": "nb-NO", "translation": "Nettstedinfo"},
    ]


def test_working_hours_is_a_real_bool_not_int() -> None:
    payload = LocationsResource().build_payload(_view_row())
    # Distinguishes True from 1 — Ampeco rejects integers for boolean fields.
    assert payload["workingHours"]["isAlwaysOpen"] is True


def test_tags_are_parsed_to_a_list() -> None:
    payload = LocationsResource().build_payload(_view_row())
    assert payload["tags"] == ["Owner:Customer", "Source:Laddel"]


def test_mapping_values_uses_key_and_returned_id() -> None:
    values = LocationsResource().mapping_values(_view_row(), 5001)
    assert values == {"mapping_key": "Laddel|Location|123", "target_location_id": 5001}
