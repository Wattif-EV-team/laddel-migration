"""Tests for step resolution, orchestration aggregation, and reporting."""

from __future__ import annotations

import pytest

from laddel_migration.runner import orchestrator, registry
from laddel_migration.runner.context import RunContext, StepResult


class TestResolveSteps:
    def test_default_selects_all_profile(self) -> None:
        names = [s.name for s in registry.resolve_steps()]
        assert names == list(registry.PROFILES["all"])

    def test_profile_selection(self) -> None:
        assert [s.name for s in registry.resolve_steps(profile="partners")] == ["partners"]

    def test_explicit_names(self) -> None:
        assert [s.name for s in registry.resolve_steps(names=("partners",))] == ["partners"]

    def test_profile_and_name_deduplicate(self) -> None:
        steps = registry.resolve_steps(profile="partners", names=("partners",))
        assert [s.name for s in steps] == ["partners"]

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown profile"):
            registry.resolve_steps(profile="does-not-exist")

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown step"):
            registry.resolve_steps(names=("nope",))


def _ctx() -> RunContext:
    return RunContext(settings=None, client=None, dry_run=True)  # type: ignore[arg-type]


def test_run_steps_runs_resolved_steps_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def make(name: str, errors: list[str] | None = None):
        def _run(ctx: RunContext) -> StepResult:
            calls.append(name)
            return StepResult(step=name, total=1, created=1, errors=errors or [])

        return registry.Step(name=name, run=_run)

    fake_steps = [make("alpha"), make("beta")]
    monkeypatch.setattr(orchestrator, "resolve_steps", lambda **kw: fake_steps)

    results = orchestrator.run_steps(_ctx(), profile="all")

    assert calls == ["alpha", "beta"]
    assert [r.step for r in results] == ["alpha", "beta"]


def test_report_includes_totals_and_errors() -> None:
    results = [
        StepResult(
            step="partners", total=3, created=2, updated=0, skipped=0, errors=["Acme: boom"]
        ),
    ]
    text = orchestrator.report(results)

    assert "partners" in text
    assert "created=2" in text
    assert "TOTAL" in text
    assert "[partners] Acme: boom" in text


def test_has_errors_reflects_row_errors() -> None:
    assert orchestrator.has_errors([StepResult(step="a", errors=["x"])]) is True
    assert orchestrator.has_errors([StepResult(step="a")]) is False


class TestClientFor:
    def test_returns_matching_client(self) -> None:
        ctx = RunContext(settings=None, client="ampeco-client", sitetracker="st-client")  # type: ignore[arg-type]
        assert ctx.client_for("ampeco") == "ampeco-client"
        assert ctx.client_for("sitetracker") == "st-client"

    def test_raises_when_client_unconfigured(self) -> None:
        ctx = RunContext(settings=None, client=None, sitetracker=None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="not configured for this run"):
            ctx.client_for("sitetracker")

    def test_raises_on_unknown_target_system(self) -> None:
        ctx = RunContext(settings=None, client=None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="unknown target system"):
            ctx.client_for("nope")
