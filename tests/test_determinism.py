"""P0 gate: same seed -> identical trace. If this breaks, nondeterminism leaked."""

from campaign.runner import run_one
from faults.injector import FaultConfig


def _trace_ids(seed: int) -> list[str]:
    from boundaries.db import ConductorRecord, NodeRecord
    from boundaries.db import InMemoryDb
    from runtime.clock import VirtualClock
    from runtime.rng import SeededRng
    from runtime.scheduler import Scheduler

    rng = SeededRng(seed)
    clock = VirtualClock()
    sched = Scheduler(rng, clock)
    db = InMemoryDb()
    for i in range(3):
        db.upsert_conductor(ConductorRecord(id=f"c{i}", online=True, updated_at=0))

    for cid in list(db.conductors.keys()):
        sched.schedule_after(rng.randint(0, 5), f"hb-{cid}", lambda c=cid: db.heartbeat(c, clock.now))

    # deterministic workload: N events with jitter
    for n in range(10):
        sched.schedule_after(rng.randint(1, 10), f"work-{n}", lambda: None)

    out = []
    while sched.has_events() and len(out) < 20:
        ev = sched.pop_next()
        out.append(f"{ev.vtime}:{ev.name}")
        ev.fn()
    return out


def test_same_seed_identical_trace():
    assert _trace_ids(42) == _trace_ids(42)


def test_different_seed_differs():
    # not guaranteed for all seeds, but with jitter it should for these
    assert _trace_ids(42) != _trace_ids(7)


def test_run_one_deterministic():
    faults = FaultConfig.default()
    r1 = run_one(123, faults, max_vtime=500)
    r2 = run_one(123, faults, max_vtime=500)
    assert r1.ok == r2.ok
    assert r1.violated == r2.violated
    assert r1.steps == r2.steps
