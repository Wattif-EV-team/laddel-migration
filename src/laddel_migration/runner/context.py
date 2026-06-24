"""Shared run context and per-step result types for the migration runner."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clients.ampeco import AmpecoClient
from ..config import Settings


@dataclass(frozen=True)
class RunContext:
    """Everything a step needs to run.

    ``client`` is ``None`` in a dry run, where steps build and log payloads but
    make no API calls and write no mappings.
    """

    settings: Settings
    client: AmpecoClient | None
    dry_run: bool = False


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
