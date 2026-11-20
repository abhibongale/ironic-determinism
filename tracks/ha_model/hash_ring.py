"""Deterministic hash ring — port of ironic/common/hash_ring.py for HA sim.

Real ironic uses tooz.hashring.HashRing with murmur + 2**hash_partition_exponent partitions.
This is a minimal deterministic stand-in that preserves the properties needed for invariant tests:

  - Maps (node_uuid, driver) → conductor_id via stable hash (sha256, not murmur — deterministic and pure Python)
  - Filters to conductors supporting the driver (d2c: driver → [hosts])
  - Respects hash_ring_reset_interval staleness window via per-manager cached rings + reset()
  - Deterministic: same seed + same ring contents → same owner

For realistic interop with ironic, swap hash_function to murmur via mmh3; for now sha256 is fine.
See vault 08 §5: stale window where conductors disagree is the key injection point.
"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional


def _hash_node(node_uuid: str, driver: str, partitions: int = 1024) -> int:
    """Stable hash for (node, driver) — deterministic across runs."""
    h = hashlib.sha256(f"{driver}:{node_uuid}".encode()).hexdigest()
    return int(h[:8], 16) % partitions


class FakeHashRing:
    """Single ring for one driver — list of hosts, get_hosts(node_uuid) → [owner]."""

    def __init__(self, hosts: List[str], partitions: int = 1024) -> None:
        self.hosts = hosts
        self.partitions = partitions

    def get_hosts(self, node_uuid: str) -> List[str]:
        if not self.hosts:
            return []
        idx = _hash_node(node_uuid, "", self.partitions) % len(self.hosts)
        # Consistent order: rotate list starting at idx
        return self.hosts[idx:] + self.hosts[:idx]


class FakeHashRingManager:
    """Manages rings per (conductor_group:driver) → FakeHashRing, with staleness window."""

    def __init__(self, hash_ring_reset_interval: int = 180, partitions: int = 1024) -> None:
        self.hash_ring_reset_interval = hash_ring_reset_interval
        self.partitions = partitions
        self._rings: Dict[str, FakeHashRing] = {}
        self._updated_at: float = 0.0
        self._cached_d2c: Optional[Dict[str, List[str]]] = None

    def load(self, d2c: Dict[str, List[str]]) -> None:
        """Rebuild rings from driver→[hosts] dict (from InMemoryDb live conductors)."""
        self._cached_d2c = d2c
        self._rings = {}
        for driver, hosts in d2c.items():
            self._rings[driver] = FakeHashRing(hosts, self.partitions)
        self._updated_at = time.monotonic()

    def is_stale(self) -> bool:
        return (time.monotonic() - self._updated_at) >= self.hash_ring_reset_interval

    def get_owner(self, node_uuid: str, driver: str, conductor_group: str = "") -> Optional[str]:
        key = f"{conductor_group}:{driver}" if conductor_group else driver
        ring = self._rings.get(key)
        if not ring:
            return None
        hosts = ring.get_hosts(node_uuid)
        return hosts[0] if hosts else None

    def reset(self) -> None:
        self._rings = {}
        self._cached_d2c = None
        self._updated_at = 0.0
