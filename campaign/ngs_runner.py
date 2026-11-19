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

SCENARIOS = {
    "basic": scenario_basic,
    "refcount": scenario_refcount_stale_vlan,
    "trunk": scenario_trunk_subport,
    "multi_switch": scenario_multi_switch,
    "multi_switch_ref": scenario_multi_switch_ref_bug,
    "concurrent": scenario_concurrent_plug,
    "concurrent_faults": scenario_concurrent_with_faults,
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
