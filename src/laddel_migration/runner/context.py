"""Shared run context and per-step result types for the migration runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..clients.ampeco import AmpecoClient
from ..clients.sitetracker import SiteTrackerClient
from ..config import Settings


@dataclass(frozen=True)
class RunContext:
    """Everything a step needs to run.

    A step declares which target system it writes to (``Resource.target_system``)
    and the loop resolves the matching client via :meth:`client_for`. Both
    clients are ``None`` in a dry run, where steps build and log payloads but
    make no API calls and write no mappings.
    """

    settings: Settings
    client: AmpecoClient | None
    sitetracker: SiteTrackerClient | None = None
    dry_run: bool = False

    def client_for(self, target_system: str) -> Any:
        """Return the configured client for ``target_system``.

        Raises :class:`RuntimeError` if the step needs a client that was not
        built for this run (e.g. its credentials are unset), so the failure is
        clear rather than an attribute error mid-loop.
        """
        clients = {"ampeco": self.client, "sitetracker": self.sitetracker}
        if target_system not in clients:
            raise ValueError(f"unknown target system: {target_system!r}")
        client = clients[target_system]
        if client is None:
            raise RuntimeError(
                f"the {target_system!r} client is not configured for this run; "
                "check the corresponding credentials are set."
            )
        return client


@dataclass
class StepResult:
    """Mutable tally of what a single step did, plus collected row errors."""

    step: str
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors)
