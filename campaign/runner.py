"""Campaign runner — sweep seeds, replay from seed."""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass

from boundaries.db import InMemoryDb
from faults.injector import FaultConfig
from oracle.invariants import check_all
from runtime.clock import VirtualClock
from runtime.rng import SeededRng
from runtime.scheduler import Scheduler


@dataclass
class RunResult:
    seed: int
    ok: bool
    failed: bool
    violated: list[str]
    steps: int


def run_one(seed: int, faults: FaultConfig, max_vtime: int = 10_000) -> RunResult:
    """Minimal P0 run: N conductors heartbeating + DB. Deterministic via seed."""
    rng = SeededRng(seed)
    clock = VirtualClock()
    sched = Scheduler(rng, clock)
    db = InMemoryDb()

    # setup 3 conductors
    from boundaries.db import ConductorRecord, NodeRecord

    for i in range(3):
        db.upsert_conductor(ConductorRecord(id=f"c{i}", online=True, updated_at=0))
        db.add_node(NodeRecord(uuid=f"node-{i}", provision_state="available"))

    # schedule heartbeats every 20 ticks
    def make_hb(cid: str) -> None:
        def hb() -> None:
            db.heartbeat(cid, clock.now)
            # re-schedule
            sched.schedule_after(20, f"hb-{cid}", hb)

        sched.schedule_after(rng.randint(0, 5), f"hb-{cid}", hb)

    for cid in list(db.conductors.keys()):
        make_hb(cid)

    # faults as scheduled jitter
    def tick_faults() -> None:
        from faults.injector import maybe_inject

        maybe_inject(rng, faults, {"vtime": clock.now})
        sched.schedule_after(10, "fault-tick", tick_faults)

    sched.schedule_after(10, "fault-tick", tick_faults)

    # run loop
    violated: list[str] = []
    while sched.has_events() and clock.now <= max_vtime:
        # peek next vtime
        nxt = sched._heap[0].vtime if sched._heap else max_vtime + 1
        if nxt > max_vtime:
            break
        ev = sched.pop_next()
        ev.fn()
        res = check_all(db=db, now=clock.now)
        if res.failed:
            violated = res.violated
            break

    ok = len(violated) == 0
    return RunResult(seed=seed, ok=ok, failed=not ok, violated=violated, steps=len(sched.trace))


def sweep(seeds: int, max_vtime: int = 10_000) -> list[RunResult]:
    faults = FaultConfig.default()
    results: list[RunResult] = []
    for s in range(seeds):
        r = run_one(s, faults, max_vtime=max_vtime)
        results.append(r)
        if r.failed:
            print(f"[FAIL] seed={r.seed} violated={r.violated} steps={r.steps}")
        else:
            # quiet ok
            pass
    fails = sum(1 for r in results if r.failed)
    print(f"sweep {seeds} seeds: {fails} failures")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=100)
    p.add_argument("--max-vtime", type=int, default=10_000)
    args = p.parse_args()
    sweep(args.seeds, max_vtime=args.max_vtime)
