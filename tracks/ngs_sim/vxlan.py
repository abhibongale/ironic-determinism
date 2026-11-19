"""VxLAN / Geneve — explicit wrappers over L2VNI scenarios.

These are thin aliases that make the VxLAN intent visible in tests/docs/sweeps
and add one true VxLAN top/bottom-segment scenario that exercises
BaremetalL2VNISim (the baremetal-l2vni driver's allocation model).

All underlying switch operations remain FakeSwitch (OVS fake: external_ids:vni-5000=42).
"""

from __future__ import annotations

from boundaries.fake_switch import FakeSwitch
from oracle.invariants import CheckResult, check_all

from .scenario import (
    scenario_concurrent_plug,
    scenario_multi_switch,
    scenario_multi_switch_ref_bug,
)
from .vxlan_segments import BaremetalL2VNISim, SegmentRange


# ---------------------------------------------------------------------------
# VxLAN-named aliases (discoverability for Tier-1 template copy-paste)
# ---------------------------------------------------------------------------

scenario_vxlan_multi_switch = scenario_multi_switch
"""VxLAN multi-switch: same VNI 5000 → VLAN 100 on physnet1, VLAN 105 on physnet2."""

scenario_geneve_multi_switch = scenario_multi_switch
"""Geneve uses the identical L2VNI path (gap analysis §1: geneve == vxlan here)."""


# ---------------------------------------------------------------------------
# True VxLAN top/bottom hierarchical binding scenario
# ---------------------------------------------------------------------------


def scenario_vxlan_top_bottom(seed: int = 0) -> CheckResult:
    """VxLAN hierarchical binding (gap analysis §1 steps 1-4).

    Models the real Neutron flow:
      tenant network type=vxlan vni=5000 (top) → baremetal-l2vni allocates bottom
      VLAN per physnet from segment range → NGS programs VTEP VNI→VLAN + port.

    Asserts the top/bottom pair exists and per-physnet bottom VLANs can differ.
    """
    # segment ranges per physnet (neutron network_segment_range)
    l2vni = BaremetalL2VNISim(
        ranges={
            "physnet1": SegmentRange("physnet1", vlan_min=100, vlan_max=110),
            "physnet2": SegmentRange("physnet2", vlan_min=200, vlan_max=210),
        }
    )
    net_id = "net-tenant-vxlan"
    vni = 5000

    # Step 1: tenant creates VxLAN network → top segment
    top = l2vni.ensure_top_segment(net_id, vni=vni, network_type="vxlan")
    assert top.network_type == "vxlan" and top.segmentation_id == vni and top.is_top

    # Step 2-3: two baremetal ports on different physnets → bottom VLANs allocated
    sw1 = FakeSwitch("brbm-physnet1")
    sw2 = FakeSwitch("brbm-physnet2")

    bottom1 = l2vni.ensure_bottom_segment(net_id, "physnet1")  # e.g. VLAN 100
    bottom2 = l2vni.ensure_bottom_segment(net_id, "physnet2")  # e.g. VLAN 200
    if bottom1.segmentation_id == bottom2.segmentation_id:
        return CheckResult(ok=False, violated=["N1"], detail="per-physnet bottom VLANs must differ (design promise)")

    # Step 4: Genericswitch programs VTEP VNI→VLAN + port into local VLAN
    for sw, bottom in [(sw1, bottom1), (sw2, bottom2)]:
        sw.add_network(bottom.segmentation_id, net_id)
        sw.plug_switch_to_network(vni=top.segmentation_id, segmentation_id=bottom.segmentation_id)
        sw.plug_port_to_network(f"baremetal-port-{bottom.physical_network}", bottom.segmentation_id)

    # Assertions: VNI→VLAN present per switch, correct vlan per physnet
    if not sw1.vlan_has_vni(bottom1.segmentation_id, vni):
        return CheckResult(ok=False, violated=["N2"], detail="brbm1 VTEP missing VNI→VLAN")
    if not sw2.vlan_has_vni(bottom2.segmentation_id, vni):
        return CheckResult(ok=False, violated=["N2"], detail="brbm2 VTEP missing VNI→VLAN")

    # Hierarchical binding visible: top + bottom segments exist
    bindings = [l2vni.hierarchical_binding(net_id, pn) for pn in ["physnet1", "physnet2"]]
    if any(top != b[0] for b in bindings):
        return CheckResult(ok=False, violated=["N1"], detail="hierarchical binding top mismatch")

    # Cleanup: delete ports → bottom segments released → VTEP mappings removed
    for sw, physnet in [(sw1, "physnet1"), (sw2, "physnet2")]:
        bottom = next(s for s in l2vni.segments if s.physical_network == physnet and not s.is_top)
        sw.delete_port(f"baremetal-port-{physnet}", bottom.segmentation_id)
        sw.del_network(bottom.segmentation_id)
        sw.unplug_switch_from_network(vni=vni, segmentation_id=bottom.segmentation_id)
        l2vni.release_bottom(net_id, physnet)

    # After releases, bottom segments for that network must be gone (no leak)
    bottoms_left = [s for s in l2vni.segments if s.network_id == net_id and not s.is_top]
    if bottoms_left:
        return CheckResult(ok=False, violated=["N1"], detail=f"bottom segments leaked: {bottoms_left}")

    return check_all(switches=[sw1, sw2])


def scenario_vxlan_geneve_parity(seed: int = 0) -> CheckResult:
    """Parity: VxLAN and Geneve share the same L2VNI code path (gap §1).

    Ensures the baremetal-l2vni + NGS flow works for both network_type values.
    This is the RHOSO geneve product-path check called out as undertested.
    """
    for net_type in ("vxlan", "geneve"):
        l2vni = BaremetalL2VNISim(ranges={"physnet1": SegmentRange("physnet1", vlan_min=300, vlan_max=310)})
        net_id = f"net-{net_type}"
        vni = 6000
        top = l2vni.ensure_top_segment(net_id, vni=vni, network_type=net_type)
        if top.network_type != net_type:
            return CheckResult(ok=False, violated=["N1"], detail=f"top network_type {net_type} not stored")
        bottom = l2vni.ensure_bottom_segment(net_id, "physnet1")
        sw = FakeSwitch("brbm")
        sw.add_network(bottom.segmentation_id, net_id)
        sw.plug_switch_to_network(vni=top.segmentation_id, segmentation_id=bottom.segmentation_id)
        sw.plug_port_to_network("p0", bottom.segmentation_id)
        if not sw.vlan_has_vni(bottom.segmentation_id, vni):
            return CheckResult(ok=False, violated=["N2"], detail=f"{net_type} VNI→VLAN not programmed")
        res = check_all(switches=[sw])
        if not res.ok:
            return res
    return CheckResult(ok=True, violated=[], detail="")
