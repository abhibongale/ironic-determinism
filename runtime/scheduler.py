"""Cooperative scheduler — (vtime, seq) heap. Single-threaded.

Actors yield by scheduling callbacks; scheduler picks next event deterministically.
Clock jumps to event's vtime on pop. If multiple events share vtime, seq breaks ties.
RNG is available for tie-randomization variants later (currently strict seq for full replay).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .clock import VirtualClock
from .rng import SeededRng


@dataclass(order=True)
class Event:
    vtime: int
    seq: int
    name: str = field(compare=False)
    fn: Callable[[], Any] = field(compare=False, repr=False)
    meta: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


class Scheduler:
    def __init__(self, rng: SeededRng, clock: VirtualClock) -> None:
        self.rng = rng
        self.clock = clock
        self._heap: list[Event] = []
        self._seq = 0
        self.trace: list[Event] = []  # popped events in order

    # enqueues

    def schedule_at(self, vtime: int, name: str, fn: Callable[[], Any], **meta: Any) -> None:
        if vtime < self.clock.now:
            raise ValueError(f"cannot schedule in the past: vtime={vtime} now={self.clock.now}")
        self._seq += 1
        heapq.heappush(self._heap, Event(vtime=vtime, seq=self._seq, name=name, fn=fn, meta=meta))

    def schedule_after(self, delay: int, name: str, fn: Callable[[], Any], **meta: Any) -> None:
        self.schedule_at(self.clock.now + delay, name, fn, **meta)

    def schedule_now(self, name: str, fn: Callable[[], Any], **meta: Any) -> None:
        self.schedule_at(self.clock.now, name, fn, **meta)

    # stepping

    def has_events(self) -> bool:
        return bool(self._heap)

    def pop_next(self) -> Event:
        ev = heapq.heappop(self._heap)
        self.clock.jump_to(ev.vtime)
        self.trace.append(ev)
        return ev

    def run_until(self, max_vtime: int, on_event: Optional[Callable[[Event], None]] = None) -> None:
        """Run until vtime exceeds max_vtime or heap empty. on_event called after each pop before fn."""
        while self._heap and self._heap[0].vtime <= max_vtime:
            ev = self.pop_next()
            if on_event:
                on_event(ev)
            ev.fn()

    def drain(self) -> None:
        while self._heap:
            ev = self.pop_next()
            ev.fn()
