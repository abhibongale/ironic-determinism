"""Phase A — deterministic NGS scenarios + campaign determinism."""

from campaign.ngs_runner import run_one_ngs, SCENARIOS
from tracks.ngs_sim.scenario import (
    scenario_basic,
    scenario_concurrent_plug,
    scenario_concurrent_with_faults,
    scenario_multi_switch,
    scenario_multi_switch_ref_bug,
    scenario_refcount_stale_vlan,
    scenario_trunk_subport,
)


def test_basic():
    assert scenario_basic().ok


def test_refcount():
    assert scenario_refcount_stale_vlan().ok


def test_trunk():
    assert scenario_trunk_subport().ok


def test_multi_switch():
    assert scenario_multi_switch().ok


def test_multi_switch_ref_bug():
    assert scenario_multi_switch_ref_bug().ok


def test_concurrent_plug():
    assert scenario_concurrent_plug(seed=0).ok
    # deterministic across seeds
    assert scenario_concurrent_plug(seed=42).ok


def test_concurrent_with_faults():
    assert scenario_concurrent_with_faults(seed=0).ok
    assert scenario_concurrent_with_faults(seed=7).ok


def test_ngs_runner_deterministic():
    r1 = run_one_ngs(123, "multi_switch")
    r2 = run_one_ngs(123, "multi_switch")
    assert r1.ok == r2.ok and r1.violated == r2.violated


def test_all_scenarios_ok_seeded():
    for name in SCENARIOS:
        r = run_one_ngs(0, name)
        assert r.ok, f"{name} failed: {r.violated} {r.detail}"
