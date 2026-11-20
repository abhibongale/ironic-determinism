"""HA campaign — 3 conductors, hash-ring staleness, stall → S1/S4 via Scheduler."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from boundaries.db import ConductorRecord, InMemoryDb, NodeRecord
from faults.injector import FaultConfig, FaultKind
from oracle.invariants import check_all
from runtime.clock import VirtualClock
from runtime.rng import SeededRng
from runtime.scheduler import Scheduler
from tracks.ha_model.conductor import Conductor
from tracks.ha_model.hash_ring import FakeHashRingManager


@dataclass
class HARunResult:
    seed: int
    ok: bool
    violated: list[str]
    steps: int


def run_one_ha(seed: int, faults: FaultConfig | None = None, n_nodes: int = 6, max_vtime: int = 800) -> HARunResult:
    faults = faults or FaultConfig(
        enabled={FaultKind.stall, FaultKind.stale_ring},
        prob={FaultKind.stall: 0.08, FaultKind.stale_ring: 0.15},
    )
    rng = SeededRng(seed)
    clock = VirtualClock()
    sched = Scheduler(rng, clock)
    db = InMemoryDb()

    for i in range(3):
        db.upsert_conductor(ConductorRecord(id=f"c{i}", online=True, updated_at=0))
    for i in range(n_nodes):
        db.add_node(NodeRecord(uuid=f"node-{i}", provision_state="available", state_history=["available"]))

    conductors: list[Conductor] = []
    for i in range(3):
        mgr = FakeHashRingManager(hash_ring_reset_interval=60, partitions=1024)
        mgr.load({"fake-hardware": [f"c{j}" for j in range(3)]})
        c = Conductor(id=f"c{i}", db=db, clock=clock, rng=rng, ring_mgr=mgr, heartbeat_interval=20, heartbeat_timeout=60, hash_ring_reset_interval=60)
        conductors.append(c)
        c.install(sched)

    # Work: periodic random reserve+mutate by random live conductor
    def make_work():
        def fn() -> None:
            live = db.live_conductors(clock.now, 60)
            if not live:
                sched.schedule_after(10, "ha-work", make_work())
                return
            cid = rng.choice(live)
            # pick conductor object
            cond = next(x for x in conductors if x.id == cid)
            node_id = rng.choice(list(db.nodes.keys()))
            # try active deploy
            cond.try_reserve_and_mutate(node_id, "deploying")
            # small chance deploy -> active
            if rng.coinflip(0.3):
                node = db.nodes[node_id]
                if node.provision_state == "deploying" and node.reservation == cid:
                    node.state_history.append(node.provision_state)
                    node.provision_state = "active"
                    db.release(node_id, cid)
                    cond.local_reserved.discard(node_id)
            sched.schedule_after(15, "ha-work", make_work())

        return fn

    sched.schedule_after(10, "ha-work", make_work())

    # Fault: stall → conductor misses heartbeats but keeps local work (split-brain)
    def fault_tick():
        def fn() -> None:
            for c in conductors:
                if rng.random() < faults.prob.get(FaultKind.stall, 0.0):
                    c.stall(faults.stall_duration)
            sched.schedule_after(20, "ha-fault", fault_tick())

        return fn

    sched.schedule_after(15, "ha-fault", fault_tick())

    violated: list[str] = []
    while sched.has_events() and clock.now <= max_vtime:
        nxt = sched._heap[0].vtime if sched._heap else max_vtime + 1
        if nxt > max_vtime:
            break
        ev = sched.pop_next()
        ev.fn()
        res = check_all(db=db, now=clock.now, conductors=conductors)
        if res.failed:
            violated = res.violated
            break

    return HARunResult(seed=seed, ok=len(violated) == 0, violated=violated, steps=len(sched.trace))


def sweep_ha(seeds: int = 200, max_vtime: int = 800) -> list[HARunResult]:
    results = []
    for s in range(seeds):
        r = run_one_ha(s, max_vtime=max_vtime)
        results.append(r)
        if not r.ok:
            print(f"[FAIL] seed={r.seed} violated={r.violated} steps={r.steps}")
    fails = sum(1 for r in results if not r.ok)
    print(f"HA sweep {seeds} seeds: {fails} failures")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=200)
    p.add_argument("--max-vtime", type=int, default=800)
    args = p.parse_args()
    sweep_ha(args.seeds, max_vtime=args.max_vtime)
