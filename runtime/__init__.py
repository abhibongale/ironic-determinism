"""Deterministic runtime: VirtualClock + SeededRng + Scheduler.

Single-threaded, cooperative. A run is a pure function of (seed, FaultConfig, workload).
All nondeterminism (interleaving, latency, fault choice) flows through SeededRng.
"""

from .clock import VirtualClock
from .rng import SeededRng
from .scheduler import Event, Scheduler

__all__ = ["VirtualClock", "SeededRng", "Event", "Scheduler"]
