"""Pure helpers for working with Salesforce ``describe`` metadata.

No CLI/typer imports here on purpose: everything is a plain function operating
on the JSON shape returned by ``GET /sobjects/{ApiName}/describe/``, so it is
unit-testable without a live SiteTracker org or a CLI harness. Generalizes the
NEW/REMOVED/CHANGED diff that used to live (print-as-you-go) in
``scratch/refresh_sitetracker_describe.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def field_map(describe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``describe["fields"]`` indexed by field API name."""
    return {f["name"]: f for f in describe["fields"]}


def picklist_values(field_: dict[str, Any]) -> list[str]:
    """Return the active picklist values for one field (``[]`` if not a picklist)."""
    return [pv["value"] for pv in field_.get("picklistValues", []) if pv.get("active", True)]


@dataclass(frozen=True, slots=True)
class ChangedField:
    """What changed for one field present in both the old and new describe."""

    name: str
    old_type: str
    new_type: str
    old_label: str
    new_label: str
    picklist_added: list[str] = field(default_factory=list)
    picklist_removed: list[str] = field(default_factory=list)

    @property
    def type_changed(self) -> bool:
        return self.old_type != self.new_type

    @property
    def label_changed(self) -> bool:
        return self.old_label != self.new_label


@dataclass(frozen=True, slots=True)
class DescribeDiff:
    """The result of comparing two describe payloads for the same sObject."""

    new_fields: list[str]
    removed_fields: list[str]
    changed_fields: list[ChangedField]

    @property
    def has_changes(self) -> bool:
        return bool(self.new_fields or self.removed_fields or self.changed_fields)


def diff_describes(old: dict[str, Any], new: dict[str, Any]) -> DescribeDiff:
    """Compare two ``describe`` payloads for the same sObject.

    Returns a :class:`DescribeDiff` describing fields added, removed, or
    changed (type, label, or picklist values) between ``old`` and ``new``.
    Purely data-returning (no printing) so it can be unit-tested directly.
    """
    old_fields = field_map(old)
    new_fields = field_map(new)

    new_names = sorted(set(new_fields) - set(old_fields))
    removed_names = sorted(set(old_fields) - set(new_fields))
    common_names = sorted(set(new_fields) & set(old_fields))

    changed: list[ChangedField] = []
    for name in common_names:
        of, nf = old_fields[name], new_fields[name]
        old_pv, new_pv = picklist_values(of), picklist_values(nf)
        if (of["type"], of["label"]) != (nf["type"], nf["label"]) or old_pv != new_pv:
            changed.append(
                ChangedField(
                    name=name,
                    old_type=of["type"],
                    new_type=nf["type"],
                    old_label=of["label"],
                    new_label=nf["label"],
                    picklist_added=sorted(set(new_pv) - set(old_pv)),
                    picklist_removed=sorted(set(old_pv) - set(new_pv)),
                )
            )

    return DescribeDiff(new_fields=new_names, removed_fields=removed_names, changed_fields=changed)
