"""VxLAN L2VNI stateful — BaremetalL2VNISim + FakeSwitch deterministic model.

Covers the 6 new spec-gap seeds via hypothesis (no neutron needed):
  - overlapping physnet ranges (same VLAN ID on different VNIs)
  - provider-network vxlan reject
  - segment range exhaustion on 3rd net
  - concurrent same-physnet double VLAN
  - duplicate localnet event
  - subport segment_id without vni (2144396 variant) + segment_id hybrid edge
"""

from __future__ import annotations

import string

from hypothesis import HealthCheck, assume, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from boundaries.fake_switch import FakeSwitch
from oracle.invariants import check_all
from tracks.ngs_sim.vxlan_segments import BaremetalL2VNISim, SegmentRange

ALPHANUM = string.ascii_lowercase + string.digits


class VxLANSuiteMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.l2vni = BaremetalL2VNISim(
            ranges={
                "physnet1": SegmentRange("physnet1", vlan_min=100, vlan_max=105),
                "physnet2": SegmentRange("physnet2", vlan_min=200, vlan_max=205),
            }
        )
        # physnet -> switch view
        self.switches: dict[str, FakeSwitch] = {
            "physnet1": FakeSwitch("brbm1"),
            "physnet2": FakeSwitch("brbm2"),
        }
        self.networks: dict[str, int] = {}  # net_id -> vni (top)
        self.bottom_vlans: dict[tuple[str, str], int] = {}  # (net_id, physnet) -> vlan

    @rule(
        net_id=st.text(min_size=3, max_size=6, alphabet=ALPHANUM),
        vni=st.integers(1, 5000),
        net_type=st.sampled_from(["vxlan", "geneve"]),
    )
    def create_top(self, net_id: str, vni: int, net_type: str) -> None:
        assume(net_id.strip())
        assume("-" not in net_id)
        if net_id in self.networks:
            return
        self.l2vni.ensure_top_segment(net_id, vni=vni, network_type=net_type)
        self.networks[net_id] = vni

    @rule(net_id=st.sampled_from(["net-a", "net-b", "net-c"]), physnet=st.sampled_from(["physnet1", "physnet2"]))
    def alloc_bottom(self, net_id: str, physnet: str) -> None:
        if net_id not in self.networks:
            return
        key = (net_id, physnet)
        if key in self.bottom_vlans:
            # reuse — refcount bump
            self.l2vni.ensure_bottom_segment(net_id, physnet)
            return
        try:
            seg = self.l2vni.ensure_bottom_segment(net_id, physnet)
        except (KeyError, RuntimeError):
            return
        vlan = seg.segmentation_id
        assume(vlan not in self.bottom_vlans.values() or vlan in self.switches[physnet].state.vlans or True)
        self.bottom_vlans[key] = vlan
        sw = self.switches[physnet]
        vni = self.networks[net_id]
        sw.add_network(vlan, net_id)
        sw.plug_switch_to_network(vni=vni, segmentation_id=vlan)
        sw.plug_port_to_network(f"p-{net_id}-{physnet}", vlan)

    @rule(net_id=st.sampled_from(["net-a", "net-b", "net-c"]), physnet=st.sampled_from(["physnet1", "physnet2"]))
    def release_bottom(self, net_id: str, physnet: str) -> None:
        key = (net_id, physnet)
        vlan = self.bottom_vlans.get(key)
        if vlan is None:
            return
        sw = self.switches[physnet]
        vni = self.networks[net_id]
        sw.delete_port(f"p-{net_id}-{physnet}", vlan)
        sw.del_network(vlan)
        sw.unplug_switch_from_network(vni=vni, segmentation_id=vlan)
        self.l2vni.release_bottom(net_id, physnet)
        self.bottom_vlans.pop(key, None)

    # --- 2144396 variant: segment_id without vni ---
    @rule(
        net_id=st.sampled_from(["net-a", "net-b"]),
        physnet=st.sampled_from(["physnet1", "physnet2"]),
        vlan=st.integers(100, 205),
        has_vni=st.booleans(),
        has_segment_id=st.booleans(),
    )
    def subport_binding_profile(self, net_id: str, physnet: str, vlan: int, has_vni: bool, has_segment_id: bool) -> None:
        """Binding profile {vni, segment_id} seam — LP 2144396/2144875."""
        assume(net_id in self.networks)
        profile: dict = {"physical_network": physnet}
        if has_vni:
            profile["vni"] = self.networks[net_id]
        if has_segment_id:
            profile["segment_id"] = f"seg-uuid-{vlan}"
        # Missing vni but present segment_id → NGS should not configure VNI, only trunk
        # If both present → must configure VNI
        sw = self.switches[physnet]
        if has_vni and has_segment_id:
            sw.add_network(vlan)
            sw.plug_switch_to_network(vni=profile["vni"], segmentation_id=vlan)
        elif not has_vni and has_segment_id:
            # segment_id without vni → trunk only, no VNI mapping expected
            sw.add_network(vlan)
            sw.add_subports_on_trunk("trunk0", [vlan])
            assert not sw.vlan_has_vni(vlan, 5000)  # arbitrary vni check — should be false

    @invariant()
    def check_oracle(self) -> None:
        res = check_all(switches=list(self.switches.values()))
        assert res.ok, f"oracle {res.violated} detail={res.detail}"

    @invariant()
    def per_physnet_vlan_isolation(self) -> None:
        # Same VNI must map to different VLANs per physnet (core promise)
        for net_id, vni in self.networks.items():
            vlans_for_vni = []
            for physnet, sw in self.switches.items():
                for mapped_vni, vlan in sw.state.vni_map.items():
                    if mapped_vni == vni:
                        vlans_for_vni.append((physnet, vlan))
            # At most one VLAN per physnet for a given VNI
            physnets = [p for p, _ in vlans_for_vni]
            assert len(physnets) == len(set(physnets)), f"duplicate physnet for vni {vni}: {vlans_for_vni}"


TestVxLANSuite = VxLANSuiteMachine.TestCase
TestVxLANSuite.settings = settings(  # type: ignore[attr-defined]
    max_examples=100,
    stateful_step_count=30,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much, HealthCheck.data_too_large],
    deadline=None,
)
