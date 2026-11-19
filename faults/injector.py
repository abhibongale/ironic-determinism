"""FaultConfig + maybe_inject — central seeded fault menu.

All faults are opt-in via FaultConfig; injector uses SeededRng so same seed -> same injection points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from runtime.rng import SeededRng


class FaultKind(str, Enum):
    crash = "crash"  # hard kill + restart
    stall = "stall"  # GC pause → split-brain (miss heartbeat, keep working)
    db_latency = "db_latency"
    stale_ring = "stale_ring"
    clock_skew = "clock_skew"
    mid_takeover_crash = "mid_takeover_crash"
    switch_ssh_fail = "switch_ssh_fail"
    ironic_500 = "ironic_500"


@dataclass
class FaultConfig:
    enabled: set[FaultKind] = field(default_factory=set)
    prob: dict[FaultKind, float] = field(default_factory=dict)  # per-tick trigger prob
    # per-kind knobs
    stall_duration: int = 200  # vtime ticks
    db_latency_max: int = 50
    clock_skew_max: int = 30

    @classmethod
    def default(cls) -> FaultConfig:
        return cls(
            enabled={FaultKind.db_latency, FaultKind.stale_ring, FaultKind.ironic_500},
            prob={
                FaultKind.crash: 0.02,
                FaultKind.stall: 0.03,
                FaultKind.db_latency: 0.20,
                FaultKind.stale_ring: 0.10,
                FaultKind.ironic_500: 0.08,
            },
        )

    def should(self, kind: FaultKind, rng: SeededRng) -> bool:
        if kind not in self.enabled:
            return False
        return rng.random() < self.prob.get(kind, 0.0)


def maybe_inject(
    rng: SeededRng,
    cfg: FaultConfig,
    ctx: dict[str, Any],
) -> list[str]:
    """Return list of triggered fault names this tick (caller acts on them)."""
    fired: list[str] = []
    for kind in cfg.enabled:
        if cfg.should(kind, rng):
            fired.append(kind.value)
            ctx.setdefault("fault_trace", []).append((kind.value, ctx.get("vtime", 0)))
    return fired
