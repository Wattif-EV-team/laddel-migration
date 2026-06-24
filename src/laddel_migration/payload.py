"""Reusable, side-effect-free helpers for shaping target-system payloads.

These are the mechanical building blocks shared by every create-or-update step.
They deliberately do **not** know about any specific resource: each resource's
``build_payload`` composes them and owns its own field datatypes and business
rules (see the migration pattern guide). The functions here are pure and easy
to unit-test in isolation.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any

_TRUE_STRINGS = frozenset({"1", "true", "t", "y", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "f", "n", "no", "off"})

_WHITESPACE_RE = re.compile(r"\s+")

# Unicode general categories to strip when cleaning free text:
#   So = other symbol (most emoji/pictographs), Sk = modifier symbol,
#   Cf = format char (zero-width joiner/space, bidi marks, ...).
_DROP_CATEGORIES = frozenset({"So", "Sk", "Cf"})


def clean_text(value: str) -> str:
    """Normalise free text: drop control/emoji/format chars, collapse whitespace.

    Control characters (and astral-plane code points such as emoji) become a
    space so words are not silently glued together, then runs of whitespace are
    collapsed and the result is trimmed. European accented letters and ordinary
    punctuation are preserved.
    """
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if code < 32:
            out.append(" ")
            continue
        if code > 0xFFFF:  # emoji and other astral-plane symbols
            continue
        if unicodedata.category(ch) in _DROP_CATEGORIES:
            continue
        out.append(ch)
    return _WHITESPACE_RE.sub(" ", "".join(out)).strip()


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise ValueError(f"cannot interpret {value!r} as a boolean")


def coerce(
    value: object,
    type_: type,
    default: Any = None,
    *,
    decimals: int | None = None,
    clean: bool = False,
) -> Any:
    """Coerce a raw database value to ``type_``, with a typed default.

    Only ``None`` is treated as "missing" for strings (so an intentional ``''``
    from a view survives). For numeric/boolean targets an empty string is also
    treated as missing, since it cannot be parsed.

    Parameters
    ----------
    type_:
        One of ``str``, ``int``, ``float`` or ``bool``.
    default:
        Returned when the value is missing.
    decimals:
        When coercing to ``float``, round to this many decimal places.
    clean:
        When coercing to ``str``, pass the result through :func:`clean_text`.
    """
    if value is None:
        return default

    if type_ is str:
        text = str(value)
        return clean_text(text) if clean else text

    if isinstance(value, str) and value.strip() == "":
        return default

    if type_ is bool:
        return _to_bool(value)
    if type_ is int:
        return int(value)  # type: ignore[call-overload]
    if type_ is float:
        number = float(value)  # type: ignore[arg-type]
        return round(number, decimals) if decimals is not None else number

    raise ValueError(f"unsupported coercion target type: {type_!r}")


def nest(flat: dict[str, Any]) -> dict[str, Any]:
    """Expand ``_``-separated keys into nested dicts.

    ``{"contactDetails_billing_email": x}`` becomes
    ``{"contactDetails": {"billing": {"email": x}}}``. Target API field names are
    camelCase, so ``_`` is unambiguous as the nesting separator.

    Raises
    ------
    ValueError:
        If a key is used both as a leaf and as a branch (e.g. ``a`` and ``a_b``).
    """
    result: dict[str, Any] = {}
    for key, value in flat.items():
        parts = key.split("_")
        cursor = result
        for part in parts[:-1]:
            existing = cursor.get(part)
            if existing is None:
                existing = {}
                cursor[part] = existing
            elif not isinstance(existing, dict):
                raise ValueError(f"key conflict at {part!r} while nesting {key!r}")
            cursor = existing
        leaf = parts[-1]
        if isinstance(cursor.get(leaf), dict):
            raise ValueError(f"key conflict at {leaf!r} while nesting {key!r}")
        cursor[leaf] = value
    return result


def prune_none(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively drop ``None`` values, keeping falsey values (``''``/``0``/``False``).

    Nested dicts are pruned too; a dict that becomes empty as a result is itself
    dropped so empty objects are not sent to the API.
    """
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, dict):
            pruned = prune_none(value)
            if pruned:
                cleaned[key] = pruned
            continue
        cleaned[key] = value
    return cleaned


def translations(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Fold ``prefix``-suffixed columns into a ``{locale: value}`` map.

    ``translations(row, "name_")`` turns ``name_en``/``name_sv`` columns into
    ``{"en": ..., "sv": ...}``. Columns whose value is ``None`` are skipped.
    """
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key.startswith(prefix) and value is not None:
            result[key[len(prefix) :]] = value
    return result
