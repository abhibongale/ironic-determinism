"""Fake OVN LLDP — models OVN Northbound LLDP port discovery.

Real bug: LP 2144482 — OVN LLDP unfiltered returned physnet1.br-ex for physnet2 request in multi-physnet.
This fake lets seeds inject the same class: two chassis/bridge entries, assert bridge filtering.

Also: LP 2144482 fix — filtering by bridge name before returning local_link_connection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LldpRecord:
    chassis: str
    bridge: str  # OVS bridge name (br-ex, br-bm)
    physnet: str
    switch_id: str  # tor-a
    port_id: str  # eth1/1
    switch_info: str  # ip/mgmt


class FakeOvnLldp:
    """Deterministic OVN LLDP view. Seed can inject missing bridge filter."""

    def __init__(self, records: list[LldpRecord] | None = None, filter_by_bridge: bool = True) -> None:
        self.records: list[LldpRecord] = records or []
        self.filter_by_bridge = filter_by_bridge

    def add(self, rec: LldpRecord) -> None:
        self.records.append(rec)

    def get_ports_for_physnet(self, chassis: str, physnet: str, bridge: str | None = None) -> list[LldpRecord]:
        """Mimics networking-baremetal OVN LLDP lookup. Bug path: ignores bridge."""
        out = [r for r in self.records if r.chassis == chassis and r.physnet == physnet]
        if self.filter_by_bridge and bridge:
            out = [r for r in out if r.bridge == bridge]
        return out

    def get_switch_info(self, chassis: str, physnet: str, bridge: str | None = None) -> str | None:
        ports = self.get_ports_for_physnet(chassis, physnet, bridge)
        if not ports:
            return None
        return ports[0].switch_info
