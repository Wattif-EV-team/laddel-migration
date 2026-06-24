"""The ordered step registry and named run profiles.

Adding a resource = import its ``run`` function, wrap it in a :class:`Step`,
place it in ``STEPS`` at the right point in dependency order, and add its name to
the relevant profiles. The runner resolves a requested profile or explicit step
names against this registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..steps.ampeco import partners
from ..steps.sitetracker import accounts as sitetracker_accounts
from .context import RunContext, StepResult


@dataclass(frozen=True)
class Step:
    """A named, runnable migration step in dependency order."""

    name: str
    run: Callable[[RunContext], StepResult]
    description: str = ""


# Ordered so that a resource is created before anything that references it.
STEPS: tuple[Step, ...] = (
    Step("partners", partners.run, "Create or update Ampeco partners"),
    Step(
        "sitetracker_accounts",
        sitetracker_accounts.run,
        "Create or update SiteTracker (Salesforce) accounts",
    ),
)

# Named subsets, selected with `ladmig run --profile <name>`. "all" always means
# every registered step in order.
PROFILES: dict[str, tuple[str, ...]] = {
    "all": tuple(step.name for step in STEPS),
    "ampeco": ("partners",),
    "partners": ("partners",),
    "sitetracker": ("sitetracker_accounts",),
    "sitetracker_accounts": ("sitetracker_accounts",),
}

_STEPS_BY_NAME: dict[str, Step] = {step.name: step for step in STEPS}


def resolve_steps(
    *,
    profile: str | None = None,
    names: tuple[str, ...] | None = None,
) -> list[Step]:
    """Resolve a profile and/or explicit names to steps, in registry order.

    Passing neither selects the ``all`` profile. Unknown profile or step names
    raise :class:`KeyError` so the CLI can report them clearly.
    """
    selected: set[str] = set()

    if profile is None and not names:
        profile = "all"

    if profile is not None:
        if profile not in PROFILES:
            raise KeyError(f"unknown profile: {profile!r} (known: {sorted(PROFILES)})")
        selected.update(PROFILES[profile])

    if names:
        unknown = [n for n in names if n not in _STEPS_BY_NAME]
        if unknown:
            raise KeyError(f"unknown step(s): {unknown} (known: {sorted(_STEPS_BY_NAME)})")
        selected.update(names)

    # Preserve the canonical registry order regardless of selection order.
    return [step for step in STEPS if step.name in selected]
