"""VirtualClock — logical time only.

All timeouts / heartbeats / backoffs are expressed in virtual time so waits cost µs.
Never call time.time() / time.sleep() in simulated code; use clock.now / clock.advance.
"""

from __future__ import annotations


class VirtualClock:
    """Monotonic logical clock. Only Scheduler may advance it."""

    def __init__(self, start: int = 0) -> None:
        self._now: int = start  # virtual ticks (interpret as ms, caller decides unit)

    @property
    def now(self) -> int:
        return self._now

    def advance(self, delta: int) -> int:
        if delta < 0:
            raise ValueError(f"clock cannot go backwards: delta={delta}")
        self._now += delta
        return self._now

    def jump_to(self, target: int) -> int:
        if target < self._now:
            raise ValueError(f"clock cannot go backwards: now={self._now} target={target}")
        self._now = target
        return self._now
