"""Fault injection — all seeded via SeededRng."""

from .injector import FaultConfig, FaultKind, maybe_inject

__all__ = ["FaultConfig", "FaultKind", "maybe_inject"]
