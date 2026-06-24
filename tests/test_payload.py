"""Tests for the payload helper toolkit (coerce / clean_text / nest / prune_none / translations)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from laddel_migration.payload import clean_text, coerce, nest, prune_none, translations


class TestCoerce:
    def test_string_truthy_values_become_true(self) -> None:
        for raw in ("1", "true", "t", "Y", "yes", "on"):
            assert coerce(raw, bool) is True, raw

    def test_string_falsy_values_become_false(self) -> None:
        for raw in ("0", "false", "f", "n", "no", "off"):
            assert coerce(raw, bool) is False, raw

    def test_tinyint_int_becomes_bool(self) -> None:
        # pymysql returns 0/1 ints for TINYINT(1) columns.
        assert coerce(1, bool) is True
        assert coerce(0, bool) is False

    def test_decimal_to_float_rounds(self) -> None:
        # pymysql returns Decimal for DECIMAL/NUMERIC columns.
        assert coerce(Decimal("1.239"), float, decimals=2) == 1.24

    def test_string_to_int(self) -> None:
        assert coerce("5", int) == 5

    def test_none_uses_default(self) -> None:
        assert coerce(None, str, default="-") == "-"
        assert coerce(None, int, default=0) == 0

    def test_empty_string_kept_for_str_but_default_for_numeric(self) -> None:
        # The 307 view emits '' as an intentional default; keep it for strings,
        # but an empty string can't be a number, so fall back to the default.
        assert coerce("", str, default="-") == ""
        assert coerce("", int, default=0) == 0

    def test_clean_strips_control_chars_and_collapses_whitespace(self) -> None:
        assert coerce("a\x00b", str, clean=True) == "a b"
        assert coerce("  two   spaces ", str, clean=True) == "two spaces"

    def test_clean_keeps_accents_but_removes_emoji(self) -> None:
        assert coerce("Café", str, clean=True) == "Café"
        assert coerce("Hi 😀 there", str, clean=True) == "Hi there"


class TestCleanText:
    def test_newline_becomes_space(self) -> None:
        assert clean_text("line1\nline2") == "line1 line2"

    def test_zero_width_joiner_removed(self) -> None:
        assert clean_text("a\u200db") == "ab"


class TestNest:
    def test_single_level(self) -> None:
        assert nest({"options_createUsers": True, "name": "x"}) == {
            "options": {"createUsers": True},
            "name": "x",
        }

    def test_multi_level_merges_siblings(self) -> None:
        flat = {
            "contactDetails_administrative_email": "a@x.no",
            "contactDetails_billing_email": "b@x.no",
        }
        assert nest(flat) == {
            "contactDetails": {
                "administrative": {"email": "a@x.no"},
                "billing": {"email": "b@x.no"},
            }
        }

    def test_conflict_between_leaf_and_branch_raises(self) -> None:
        with pytest.raises(ValueError, match="conflict"):
            nest({"a": 1, "a_b": 2})


class TestPruneNone:
    def test_drops_none_keeps_falsey(self) -> None:
        assert prune_none({"a": None, "b": "", "c": 0, "d": False, "e": 1}) == {
            "b": "",
            "c": 0,
            "d": False,
            "e": 1,
        }

    def test_recurses_into_nested_dicts(self) -> None:
        assert prune_none({"o": {"x": None, "y": 2}}) == {"o": {"y": 2}}

    def test_emptied_nested_dict_is_dropped(self) -> None:
        assert prune_none({"o": {"x": None}, "keep": 1}) == {"keep": 1}


class TestTranslations:
    def test_collects_locale_suffixes(self) -> None:
        row = {"name_en": "Hello", "name_sv": "Hej", "other": 1}
        assert translations(row, "name_") == {"en": "Hello", "sv": "Hej"}

    def test_skips_none_values(self) -> None:
        row = {"name_en": "Hello", "name_sv": None}
        assert translations(row, "name_") == {"en": "Hello"}
