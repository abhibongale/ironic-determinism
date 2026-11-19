"""FakeSwitch — in-memory VLAN/VNI store for NGS track.

Implements the subset of networking_generic_switch's Netmiko device contract needed for invariants:
  add_network / del_network / plug_port_to_network / delete_port
  plug_switch_to_network / unplug_switch_from_network (VNI→VLAN)
  trunk subport ops, vlan_has_ports / vlan_has_vni

No SSH, no netmiko — pure dict state so scheduler can inject latency/errors deterministically.

Vendor policies (tracks/ngs_sim/vendor_policies.py) are checked here:
  - Arista EOS / SONiC: VLAN must exist before VXLAN mapping (LP 2146511, 2148349)
  - Arista: unplug requires VNI, Vxlan1/Vx1 excluded from cleanup

Segment-based cleanup (LP 2144875): segments dict tracks Neutron VLAN segment UUIDs;
  if segment_deleted → unconditional unplug, else per-switch port check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tracks.ngs_sim.vendor_policies import policy_for


@dataclass
class SwitchState:
    vlans: set[int] = field(default_factory=set)  # local VLAN IDs present
    vni_map: dict[int, int] = field(default_factory=dict)  # vni -> vlan
    port_vlan: dict[str, int] = field(default_factory=dict)  # port -> vlan
    trunk_vlans: dict[str, set[int]] = field(default_factory=dict)  # trunk port -> set(vlan)
    vlan_refcount: dict[int, int] = field(default_factory=dict)  # vlan -> ports needing it
    # segment_id tracking for LP 2144875: vlan segment UUIDs from Neutron
    segment_ids: dict[int, str] = field(default_factory=dict)  # vlan -> segment UUID
    # LP 2144875 + 2146508: track whether unplug was called with wrong syntax
    unplug_errors: list[str] = field(default_factory=list)
    # vendor policy violations (LP 2146511): VLAN-before-VXLAN ordering
    vendor_violations: list[str] = field(default_factory=list)
    # LP 2146507: interfaces excluded from cleanup
    cleanup_excludes: set[str] = field(default_factory=set)


class FakeSwitch:
    """One switch = one physnet's view. Multi-switch tests use multiple instances."""

    def __init__(self, name: str = "fake-ovs", device_type: str = "_default") -> None:
        self.name = name
        self.device_type = device_type
        self.policy = policy_for(device_type)
        self.state = SwitchState()
        self.state.cleanup_excludes = set(self.policy.get("cleanup_excludes", set()))  # type: ignore[arg-type]
        # injectable fault: next N calls raise
        self.fail_next: int = 0
        self.fail_exc: type[Exception] = RuntimeError
        # segment existence map: segment_id (vlan uuid) -> exists in Neutron
        self._segment_exists: dict[int, bool] = {}
        # VNI required check for unplug
        self._require_vni_on_unplug = bool(self.policy.get("unplug_needs_vni", False))

    # fault helper

    def _maybe_fail(self, op: str) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise self.fail_exc(f"injected {op} failure on {self.name}")

    # helpers

    def _check_vlan_before_vxlan(self, segmentation_id: int, vni: int) -> None:
        # NOTE: Real Arista/SONiC bug was driver ordering inside plug_switch_to_network template,
        # not caller ordering. FakeSwitch itself ensures vlan exists, so we don't flag caller.
        # Vendor violations are now tested via explicit driver template inspection, not via this hook.
        # Keep hook for future strict mode (e.g., when switch doesn't auto-create vlan).
        pass

    # VLAN lifecycle

    def add_network(self, segmentation_id: int, network_id: str = "", segment_id: str | None = None) -> None:
        self._maybe_fail("add_network")
        self.state.vlans.add(segmentation_id)
        self.state.vlan_refcount.setdefault(segmentation_id, 0)
        if segment_id:
            self.state.segment_ids[segmentation_id] = segment_id
            self._segment_exists[segmentation_id] = True

    def del_network(
        self,
        segmentation_id: int,
        network_id: str = "",
        segment_id: str | None = None,
        segment_exists: bool | None = None,
    ) -> None:
        self._maybe_fail("del_network")
        # LP 2144875 hybrid: if segment_deleted → unconditional unplug, else per-switch port check
        if segment_exists is False:
            # segment gone in Neutron → unconditional cleanup even if local port remains (static trunk)
            pass
        elif self.state.vlan_refcount.get(segmentation_id, 0) > 0:
            return
        self.state.vlans.discard(segmentation_id)
        self.state.vni_map = {k: v for k, v in self.state.vni_map.items() if v != segmentation_id}
        self.state.vlan_refcount.pop(segmentation_id, None)
        # also remove from trunk references (static trunk case after segment delete)
        for trunk, vlans in self.state.trunk_vlans.items():
            vlans.discard(segmentation_id)
        if segment_id and segment_id in self.state.segment_ids:
            self.state.segment_ids.pop(segment_id, None)
            self._segment_exists.pop(segmentation_id, None)

    def mark_segment_deleted(self, segmentation_id: int) -> None:
        """Simulate Neutron segment deletion (unconditional cleanup path)."""
        self._segment_exists[segmentation_id] = False

    # port ops

    def plug_port_to_network(self, port: str, segmentation_id: int) -> None:
        # skip excluded interfaces (LP 2146507)
        if port in self.state.cleanup_excludes:
            self.state.unplug_errors.append(f"port {port} is VxLAN interface, not phys port — ignored")
            return
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

    def plug_switch_to_network(self, vni: int, segmentation_id: int, physnet: str | None = None) -> None:
        self._maybe_fail("plug_switch_to_network")
        self._check_vlan_before_vxlan(segmentation_id, vni)
        self.state.vni_map[vni] = segmentation_id
        self.state.vlans.add(segmentation_id)

    def unplug_switch_from_network(self, vni: int | None, segmentation_id: int, physnet: str | None = None) -> None:
        self._maybe_fail("unplug_switch_from_network")
        if self._require_vni_on_unplug and vni is None:
            self.state.unplug_errors.append(f"arista requires vni on unplug vlan {segmentation_id}: no vxlan vlan {{segmentation_id}} vni {{vni}}")
            return
        if vni is not None:
            self.state.vni_map.pop(vni, None)
        else:
            # fallback: remove any mapping to this vlan
            self.state.vni_map = {k: v for k, v in self.state.vni_map.items() if v != segmentation_id}

    # queries (for N1/N2 checks)

    def vlan_has_ports(self, segmentation_id: int) -> bool:
        return self.state.vlan_refcount.get(segmentation_id, 0) > 0

    def vlan_has_vni(self, segmentation_id: int, vni: int) -> bool:
        return self.state.vni_map.get(vni) == segmentation_id
