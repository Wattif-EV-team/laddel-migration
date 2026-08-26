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


class TestRegistryContents:
    """Guard the registry against accidental step loss.

    ``TestResolveSteps.test_default_selects_all_profile`` cannot catch a deleted
    step: ``PROFILES["all"]`` is *derived* from ``STEPS``, so removing a step
    quietly shrinks both sides and the assertion still passes. These tests pin
    the expected names literally. Adding a step is meant to fail here \u2014 update
    the list deliberately.
    """

    EXPECTED_STEPS = [
        "partners",
        "locations",
        "sitetracker_accounts",
        "sitetracker_sites",
        "sitetracker_site_relations",
    ]

    def test_all_steps_registered_in_dependency_order(self) -> None:
        assert [s.name for s in registry.STEPS] == self.EXPECTED_STEPS

    def test_all_profile_covers_every_step(self) -> None:
        assert list(registry.PROFILES["all"]) == self.EXPECTED_STEPS

    def test_every_step_has_a_single_step_profile(self) -> None:
        # Each step is individually runnable via `--profile <step-name>`.
        for name in self.EXPECTED_STEPS:
            assert registry.PROFILES.get(name) == (name,), f"missing profile for {name!r}"

    def test_target_system_profiles(self) -> None:
        assert registry.PROFILES["ampeco"] == ("partners", "locations")
        assert registry.PROFILES["sitetracker"] == (
            "sitetracker_accounts",
            "sitetracker_sites",
            "sitetracker_site_relations",
        )

    def test_sitetracker_dependencies_run_before_site_relations(self) -> None:
        # Site Relations resolve Site__c/Company__c from the Site and Account
        # mapping tables, so both must have run first in the same pass.
        order = [s.name for s in registry.STEPS]
        assert order.index("sitetracker_accounts") < order.index("sitetracker_site_relations")
        assert order.index("sitetracker_sites") < order.index("sitetracker_site_relations")

    def test_profiles_reference_only_known_steps(self) -> None:
        known = {s.name for s in registry.STEPS}
        for profile, names in registry.PROFILES.items():
            unknown = set(names) - known
            assert not unknown, f"profile {profile!r} references unknown step(s): {unknown}"


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
