"""Conductor actor — heartbeat, ring-rebuild, reclaim, work loop hooks.

P0: stub that exercises S1/S3 path; real hash_ring port in next iteration.
"""

from __future__ import annotations

from dataclasses import dataclass

from boundaries.db import InMemoryDb
from runtime.clock import VirtualClock
from runtime.rng import SeededRng
from runtime.scheduler import Scheduler


@dataclass
class Conductor:
    id: str
    db: InMemoryDb
    clock: VirtualClock
    rng: SeededRng
    heartbeat_interval: int = 20
    heartbeat_timeout: int = 60
    hash_ring_reset_interval: int = 180

    def install(self, sched: Scheduler) -> None:
        sched.schedule_after(self.rng.randint(0, 5), f"hb-{self.id}", self._heartbeat(sched))
        sched.schedule_after(self.hash_ring_reset_interval, f"ring-{self.id}", self._rebuild_ring(sched))

    def _heartbeat(self, sched: Scheduler):  # type: ignore[no-untyped-def]
        def fn() -> None:
            self.db.heartbeat(self.id, self.clock.now)
            sched.schedule_after(self.heartbeat_interval, f"hb-{self.id}", self._heartbeat(sched))

        return fn

    def _rebuild_ring(self, sched: Scheduler):  # type: ignore[no-untyped-def]
        def fn() -> None:
            # TODO: rebuild hash ring from live conductors; for now just reschedule
            sched.schedule_after(self.hash_ring_reset_interval, f"ring-{self.id}", self._rebuild_ring(sched))

        return fn
