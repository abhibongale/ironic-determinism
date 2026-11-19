"""FakeSwitch — in-memory VLAN/VNI store for NGS track.

Implements the subset of networking_generic_switch's Netmiko device contract needed for invariants:
  add_network / del_network / plug_port_to_network / delete_port
  plug_switch_to_network / unplug_switch_from_network (VNI→VLAN)
  trunk subport ops, vlan_has_ports / vlan_has_vni

No SSH, no netmiko — pure dict state so scheduler can inject latency/errors deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SwitchState:
    vlans: set[int] = field(default_factory=set)  # local VLAN IDs present
    vni_map: dict[int, int] = field(default_factory=dict)  # vni -> vlan
    port_vlan: dict[str, int] = field(default_factory=dict)  # port -> vlan
    trunk_vlans: dict[str, set[int]] = field(default_factory=dict)  # trunk port -> set(vlan)
    vlan_refcount: dict[int, int] = field(default_factory=dict)  # vlan -> ports needing it


class FakeSwitch:
    """One switch = one physnet's view. Multi-switch tests use multiple instances."""

    def __init__(self, name: str = "fake-ovs") -> None:
        self.name = name
        self.state = SwitchState()
        # injectable fault: next N calls raise
        self.fail_next: int = 0
        self.fail_exc: type[Exception] = RuntimeError

    # fault helper

    def _maybe_fail(self, op: str) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise self.fail_exc(f"injected {op} failure on {self.name}")

    # VLAN lifecycle

    def add_network(self, segmentation_id: int, network_id: str = "") -> None:
        self._maybe_fail("add_network")
        self.state.vlans.add(segmentation_id)
        self.state.vlan_refcount.setdefault(segmentation_id, 0)

    def del_network(self, segmentation_id: int, network_id: str = "") -> None:
        self._maybe_fail("del_network")
        # refcount-aware: real NGS checks vlan_has_ports before delete
        if self.state.vlan_refcount.get(segmentation_id, 0) > 0:
            return
        self.state.vlans.discard(segmentation_id)
        self.state.vni_map = {k: v for k, v in self.state.vni_map.items() if v != segmentation_id}
        self.state.vlan_refcount.pop(segmentation_id, None)

    # port ops

    def plug_port_to_network(self, port: str, segmentation_id: int) -> None:
        self._maybe_fail("plug_port")
        self.state.port_vlan[port] = segmentation_id
        self.state.vlan_refcount[segmentation_id] = self.state.vlan_refcount.get(segmentation_id, 0) + 1
        self.state.vlans.add(segmentation_id)

    def delete_port(self, port: str, segmentation_id: int) -> None:
        self._maybe_fail("delete_port")
        self.state.port_vlan.pop(port, None)
        cnt = self.state.vlan_refcount.get(segmentation_id, 0)
        if cnt > 0:
            self.state.vlan_refcount[segmentation_id] = cnt - 1

    # trunk subports

    def add_subports_on_trunk(self, port: str, segmentation_ids: list[int]) -> None:
        self._maybe_fail("add_subports")
        s = self.state.trunk_vlans.setdefault(port, set())
        for vid in segmentation_ids:
            s.add(vid)
            self.state.vlan_refcount[vid] = self.state.vlan_refcount.get(vid, 0) + 1
            self.state.vlans.add(vid)

    def del_subports_on_trunk(self, port: str, segmentation_ids: list[int]) -> None:
        self._maybe_fail("del_subports")
        s = self.state.trunk_vlans.get(port, set())
        for vid in segmentation_ids:
            s.discard(vid)
            cnt = self.state.vlan_refcount.get(vid, 0)
            if cnt > 0:
                self.state.vlan_refcount[vid] = cnt - 1

    # VNI → VLAN (L2VNI)

    def plug_switch_to_network(self, vni: int, segmentation_id: int) -> None:
        self._maybe_fail("plug_switch_to_network")
        self.state.vni_map[vni] = segmentation_id
        self.state.vlans.add(segmentation_id)

    def unplug_switch_from_network(self, vni: int, segmentation_id: int) -> None:
        self._maybe_fail("unplug_switch_from_network")
        self.state.vni_map.pop(vni, None)

    # queries (for N1/N2 checks)

    def vlan_has_ports(self, segmentation_id: int) -> bool:
        return self.state.vlan_refcount.get(segmentation_id, 0) > 0

    def vlan_has_vni(self, segmentation_id: int, vni: int) -> bool:
        return self.state.vni_map.get(vni) == segmentation_id
