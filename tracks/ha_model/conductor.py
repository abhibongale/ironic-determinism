"""Conductor actor — heartbeat, ring-rebuild, reclaim, stall injection.

All vtime-driven via Scheduler; never real time/sleep. See 08 §5:
  heartbeat touches conductors.updated_at → dead if now - updated_at > timeout
  ring rebuilt every hash_ring_reset_interval → staleness window where owners disagree
  reclaim steals nodes whose reservation holder is dead
  stall (FaultKind.stall) pauses heartbeat but holder keeps local work → split-brain → S1
"""

from __future__ import annotations

from dataclasses import dataclass, field

from boundaries.db import InMemoryDb
from runtime.clock import VirtualClock
from runtime.rng import SeededRng
from runtime.scheduler import Scheduler

from .hash_ring import FakeHashRingManager
from .states import can_transition


@dataclass
class Conductor:
    id: str
    db: InMemoryDb
    clock: VirtualClock
    rng: SeededRng
    ring_mgr: FakeHashRingManager | None = None
    heartbeat_interval: int = 20
    heartbeat_timeout: int = 60
    hash_ring_reset_interval: int = 180

    # local cache of last-seen reservations (for S4 check: dead still mutating)
    local_reserved: set[str] = field(default_factory=set)
    stalled_until: int = 0

    def install(self, sched: Scheduler) -> None:
        sched.schedule_after(self.rng.randint(0, 5), f"hb-{self.id}", self._heartbeat(sched))
        sched.schedule_after(self.hash_ring_reset_interval, f"ring-{self.id}", self._rebuild_ring(sched))
        sched.schedule_after(40, f"reclaim-{self.id}", self._reclaim(sched))

    def stall(self, duration: int) -> None:
        self.stalled_until = self.clock.now + duration

    def _heartbeat(self, sched: Scheduler):  # type: ignore[no-untyped-def]
        def fn() -> None:
            if self.clock.now < self.stalled_until:
                # stalled — skip heartbeat, still reschedule
                sched.schedule_after(self.heartbeat_interval, f"hb-{self.id}", self._heartbeat(sched))
                return
            self.db.heartbeat(self.id, self.clock.now)
            sched.schedule_after(self.heartbeat_interval, f"hb-{self.id}", self._heartbeat(sched))

        return fn

    def _rebuild_ring(self, sched: Scheduler):  # type: ignore[no-untyped-def]
        def fn() -> None:
            if self.ring_mgr is not None:
                # rebuild from current live conductors (per this conductor's view of DB)
                live = self.db.live_conductors(self.clock.now, self.heartbeat_timeout)
                d2c = {"fake-hardware": live}
                self.ring_mgr.load(d2c)
            sched.schedule_after(self.hash_ring_reset_interval, f"ring-{self.id}", self._rebuild_ring(sched))

        return fn

    def _reclaim(self, sched: Scheduler):  # type: ignore[no-untyped-def]
        def fn() -> None:
            if self.clock.now < self.stalled_until:
                sched.schedule_after(20, f"reclaim-{self.id}", self._reclaim(sched))
                return
            live = set(self.db.live_conductors(self.clock.now, self.heartbeat_timeout))
            for node in list(self.db.nodes.values()):
                holder = node.reservation
                if holder and holder not in live:
                    # any live conductor can reclaim orphan — ring ownership is advisory, not strict for liveness
                    self.db.steal(node.uuid, self.id)
                    self.local_reserved.add(node.uuid)
            sched.schedule_after(20, f"reclaim-{self.id}", self._reclaim(sched))

        return fn

    def try_reserve_and_mutate(self, node_uuid: str, new_state: str) -> bool:
        """Attempt reserve → mutate, respecting S1/S2. Returns True if mutated."""
        if self.clock.now < self.stalled_until:
            # stalled conductor still tries to mutate with stale reservation — S2/S4 path
            node = self.db.nodes[node_uuid]
            old = node.provision_state
            # Buggy path: mutate without re-reserving (to surface S2)
            if not can_transition(old, new_state):
                return False
            node.provision_state = new_state
            return True
        ok = self.db.reserve(node_uuid, self.id)
        if not ok:
            # already held — try steal if holder dead (handled by reclaim), else fail
            return False
        self.local_reserved.add(node_uuid)
        node = self.db.nodes[node_uuid]
        old = node.provision_state
        if not can_transition(old, new_state):
            self.db.release(node_uuid, self.id)
            self.local_reserved.discard(node_uuid)
            return False
        node.provision_state = new_state
        return True
