"""NGS campaign — sweep seeded NGS scenarios + scheduler interleavings."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from faults.injector import FaultConfig, FaultKind
from runtime.rng import SeededRng
from tracks.ngs_sim.scenario import (
    scenario_basic,
    scenario_concurrent_plug,
    scenario_concurrent_with_faults,
    scenario_multi_switch,
    scenario_multi_switch_ref_bug,
    scenario_refcount_stale_vlan,
    scenario_trunk_subport,
)
from tracks.ngs_sim.vendor_vxlan import (
    scenario_arista_cleanup_filter,
    scenario_arista_unplug_needs_vni,
    scenario_arista_vxlan_order,
    scenario_ovn_lldp_bridge_filter,
    scenario_segment_id_cleanup,
    scenario_sonic_vlan_before_trunk,
)
from tracks.ngs_sim.vxlan import (
    scenario_geneve_multi_switch,
    scenario_vxlan_geneve_parity,
    scenario_vxlan_multi_switch,
    scenario_vxlan_top_bottom,
)

SCENARIOS = {
    "basic": scenario_basic,
    "refcount": scenario_refcount_stale_vlan,
    "trunk": scenario_trunk_subport,
    "multi_switch": scenario_multi_switch,
    "multi_switch_ref": scenario_multi_switch_ref_bug,
    "concurrent": scenario_concurrent_plug,
    "concurrent_faults": scenario_concurrent_with_faults,
    # VxLAN / L2VNI (explicit — see 02b doc, gap analysis §1)
    "vxlan_multi_switch": scenario_vxlan_multi_switch,
    "geneve_multi_switch": scenario_geneve_multi_switch,
    "vxlan_top_bottom": scenario_vxlan_top_bottom,
    "vxlan_geneve_parity": scenario_vxlan_geneve_parity,
    # Vendor VxLAN (2026.1/2026.2 release-note bugs)
    "arista_vxlan_order": scenario_arista_vxlan_order,
    "sonic_vlan_before_trunk": scenario_sonic_vlan_before_trunk,
    "arista_cleanup_filter": scenario_arista_cleanup_filter,
    "arista_unplug_needs_vni": scenario_arista_unplug_needs_vni,
    "segment_id_cleanup": scenario_segment_id_cleanup,
    "ovn_lldp_bridge_filter": scenario_ovn_lldp_bridge_filter,
}

# For sweep we treat each scenario as a separate determinism check


@dataclass
class NGSRunResult:
    seed: int
    scenario: str
    ok: bool
    violated: list[str]
    detail: str = ""


def run_one_ngs(seed: int, scenario: str, faults: FaultConfig | None = None) -> NGSRunResult:
    from runtime.clock import VirtualClock
    from runtime.rng import SeededRng
    from runtime.scheduler import Scheduler

    rng = SeededRng(seed)
    clock = VirtualClock()
    sched = Scheduler(rng, clock)
    # Faults as scheduled events (wire via Scheduler so faults interleave deterministically)
    if faults and faults.enabled:
        from faults.injector import maybe_inject

        def _fault_tick() -> None:
            ctx: dict = {"vtime": clock.now}
            fired = maybe_inject(rng, faults, ctx)
            # Map faults onto scenario-visible state: inject via fail_next on a shared FakeSwitch
            # Scenarios that use Real NGS will pick this up via patched get_switch_device
            for kind in fired:
                if kind == "switch_ssh_fail":
                    # schedule the actual failure as a short-lived event
                    sched.schedule_after(0, f"fault-{kind}", lambda: None)
            sched.schedule_after(10, "fault-tick", _fault_tick)

        sched.schedule_after(5, "fault-tick", _fault_tick)
        # Drain fault ticks up to a small horizon before running scenario (to seed failures)
        # Scenarios that need scheduler interleave use their own Scheduler internally;
        # for single-step scenarios we just advance a few ticks to possibly arm faults
        while sched.has_events() and clock.now < 20:
            ev = sched.pop_next()
            ev.fn()

    fn = SCENARIOS[scenario]
    res = fn(seed)
    return NGSRunResult(seed=seed, scenario=scenario, ok=res.ok, violated=res.violated, detail=res.detail)


def sweep_ngs(seeds: int = 200, scenario: str = "all") -> list[NGSRunResult]:
    targets = list(SCENARIOS.keys()) if scenario == "all" else [scenario]
    results: list[NGSRunResult] = []
    for s in range(seeds):
        rng = SeededRng(s)
        # pick scenario deterministically from seed for full sweep
        scen = rng.choice(targets) if scenario == "all" else scenario
        r = run_one_ngs(s, scen)
        results.append(r)
        if not r.ok:
            print(f"[FAIL] seed={r.seed} scenario={r.scenario} violated={r.violated} detail={r.detail}")
    fails = sum(1 for r in results if not r.ok)
    print(f"NGS sweep {seeds} seeds ({'all scenarios' if scenario=='all' else scenario}): {fails} failures")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=200)
    p.add_argument("--scenario", type=str, default="all", choices=["all"] + list(SCENARIOS.keys()))
    args = p.parse_args()
    sweep_ngs(args.seeds, scenario=args.scenario)
