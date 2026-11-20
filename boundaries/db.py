"""InMemoryDb — deterministic store for HA track.

Replaces MariaDB + hash ring. All reads/writes go through here so faults can inject latency/reorder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NodeRecord:
    uuid: str
    provision_state: str = "available"
    reservation: Optional[str] = None  # holder conductor_id
    conductor_affinity: Optional[str] = None
    target_state: Optional[str] = None
    owner: Optional[str] = None
    lessee: Optional[str] = None
    # history for S3 legal-edge check + S4 stale-token
    state_history: list[str] = field(default_factory=list)
    # SEC2: stale IPA token — issued on reserve, invalidated on steal/release
    ipa_token: Optional[str] = None
    token_holder: Optional[str] = None
    token_seq: int = 0


@dataclass
class ConductorRecord:
    id: str
    online: bool = True
    updated_at: int = 0  # vtime of last heartbeat
    driver: str = "fake-hardware"


class InMemoryDb:
    def __init__(self) -> None:
        self.nodes: dict[str, NodeRecord] = {}
        self.conductors: dict[str, ConductorRecord] = {}

    # conductors

    def upsert_conductor(self, rec: ConductorRecord) -> None:
        self.conductors[rec.id] = rec

    def heartbeat(self, conductor_id: str, vtime: int) -> None:
        if conductor_id in self.conductors:
            self.conductors[conductor_id].updated_at = vtime
            self.conductors[conductor_id].online = True

    def mark_offline(self, conductor_id: str) -> None:
        if conductor_id in self.conductors:
            self.conductors[conductor_id].online = False

    def live_conductors(self, now: int, heartbeat_timeout: int) -> list[str]:
        return [
            c.id
            for c in self.conductors.values()
            if c.online and (now - c.updated_at) <= heartbeat_timeout
        ]

    # nodes

    def add_node(self, rec: NodeRecord) -> None:
        self.nodes[rec.uuid] = rec

    def reserve(self, node_uuid: str, holder: str) -> bool:
        n = self.nodes[node_uuid]
        if n.reservation is None:
            n.reservation = holder
            n.token_seq += 1
            n.ipa_token = f"token-{node_uuid}-{holder}-{n.token_seq}"
            n.token_holder = holder
            return True
        return False

    def release(self, node_uuid: str, holder: str) -> bool:
        n = self.nodes[node_uuid]
        if n.reservation == holder:
            n.reservation = None
            n.ipa_token = None
            n.token_holder = None
            return True
        return False

    def steal(self, node_uuid: str, new_holder: str) -> None:
        """Forced reclaim after dead-holder detection — invalidates old token."""
        n = self.nodes[node_uuid]
        n.reservation = new_holder
        n.token_seq += 1
        n.ipa_token = f"token-{node_uuid}-{new_holder}-{n.token_seq}"
        n.token_holder = new_holder

    def validate_token(self, node_uuid: str, token: str) -> bool:
        """SEC2: stale token rejected after steal/release."""
        n = self.nodes.get(node_uuid)
        if not n or not n.ipa_token:
            return False
        return n.ipa_token == token
