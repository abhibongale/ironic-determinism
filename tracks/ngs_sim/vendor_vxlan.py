"""Vendor-specific VxLAN ordering + cleanup seeds (release notes 2026.1/2026.2).

Each scenario is (seed) -> CheckResult, exercised per device_type via parametrize.

Real bugs modeled:
  1. LP 2146511 — Arista vlan_before_vxlan: BGP-EVPN + VxLAN mapping without prior vlan create
     → no IMET route, remote VTEP never discovered.
  2. LP 2146507/2146508 — Arista Vxlan1 counted as phys port + unplug missing vni param → stale vni_map.
  3. LP 2148349 — SONiC vlan before member/mapping on trunk.
  4. LP 2144875 — segment_id-based cleanup: static trunk must survive del_network when segment still exists.
  5. LP 2144482 — OVN LLDP bridge filter (wrong switch_info for multi-physnet).
"""

from __future__ import annotations

from boundaries.fake_ovn_lldp import FakeOvnLldp, LldpRecord
from boundaries.fake_switch import FakeSwitch
from oracle.invariants import CheckResult, check_all


# ---------------------------------------------------------------------------
# 1. Vendor ordering — Arista/SONiC require VLAN before VXLAN
# ---------------------------------------------------------------------------


def scenario_arista_vxlan_order(seed: int = 0) -> CheckResult:
    """LP 2146511: Arista EOS L2VNI without prior vlan → IMET not advertised."""
    sw = FakeSwitch("leaf1", device_type="netmiko_arista_eos")
    # BUG path: configure VXLAN before VLAN exists
    sw.plug_switch_to_network(vni=5000, segmentation_id=100)
    if not sw.state.vendor_violations:
        return CheckResult(ok=False, violated=["N2"], detail="Arista vlan_before_vxlan not enforced")
    # FIX path: create VLAN first → no violation
    sw2 = FakeSwitch("leaf1-fixed", device_type="netmiko_arista_eos")
    sw2.add_network(100)
    sw2.plug_switch_to_network(vni=5000, segmentation_id=100)
    if sw2.state.vendor_violations:
        return CheckResult(ok=False, violated=["N2"], detail=f"fixed path wrongly flagged: {sw2.state.vendor_violations}")
    return check_all(switches=[sw2])


def scenario_sonic_vlan_before_trunk(seed: int = 0) -> CheckResult:
    """LP 2148349: SONiC add_subports_on_trunk without prior vlan → VlanXXX does not exist."""
    sw = FakeSwitch("sonic-leaf", device_type="netmiko_sonic")
    # Real SONiC bug path: vlan 301 trunk added before vlan 301 created at global level
    # Our FakeSwitch auto-adds vlan on trunk (so no failure), but vendor policy still records ordering
    # Test that explicit add_network before trunk is the clean path
    sw.add_network(301)
    sw.add_subports_on_trunk("Eth1", [301])
    if sw.state.vendor_violations:
        return CheckResult(ok=False, violated=["N2"], detail="sonic vlan_before_vxlan unexpected")
    return check_all(switches=[sw])


# ---------------------------------------------------------------------------
# 2. Cleanup filtering + unplug syntax
# ---------------------------------------------------------------------------


def scenario_arista_cleanup_filter(seed: int = 0) -> CheckResult:
    """LP 2146507: Vxlan1 treated as phys port prevents cleanup of stale VNI mappings."""
    sw = FakeSwitch("leaf1", device_type="netmiko_arista_eos")
    sw.add_network(100)
    # Simulate naive code that iterates all ports including Vxlan1 → excluded
    if "Vxlan1" not in sw.state.cleanup_excludes:
        return CheckResult(ok=False, violated=["N1"], detail="arista should exclude Vxlan1 from cleanup")
    sw.plug_switch_to_network(vni=5000, segmentation_id=100)
    sw.plug_port_to_network("Ethernet1", 100)  # real port
    sw.delete_port("Ethernet1", 100)
    sw.del_network(100)
    # After cleanup, VNI mapping should be removed; Vxlan1 must not have blocked it
    if 100 in sw.state.vlans or 5000 in sw.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="vlan/vni leaked — cleanup blocked by Vxlan1 handling")
    return check_all(switches=[sw])


def scenario_arista_unplug_needs_vni(seed: int = 0) -> CheckResult:
    """LP 2146508: `no vxlan vlan {vlan} vni {vni}` vs incomplete `no vxlan vlan {vlan}`."""
    sw = FakeSwitch("leaf1", device_type="netmiko_arista_eos")
    sw.add_network(100)
    sw.plug_switch_to_network(vni=5000, segmentation_id=100)
    # Wrong syntax: unplug without VNI → driver should reject
    sw.unplug_switch_from_network(vni=None, segmentation_id=100)  # type: ignore[arg-type]
    if not sw.state.unplug_errors:
        return CheckResult(ok=False, violated=["N1"], detail="arista unplug without vni should error")
    if 5000 not in sw.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="stale mapping removed despite syntax error — real switch rejects")
    # Correct syntax cleans up
    sw.unplug_switch_from_network(vni=5000, segmentation_id=100)
    if 5000 in sw.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="vni mapping not cleaned with correct syntax")
    return check_all(switches=[sw])


# ---------------------------------------------------------------------------
# 3. Segment-id based cleanup (LP 2144875)
# ---------------------------------------------------------------------------


def scenario_segment_id_cleanup(seed: int = 0) -> CheckResult:
    """LP 2144875: static trunk VLAN must NOT be deleted via port-parsing when segment still exists.

    New hybrid: if segment exists → per-switch port check decides; if deleted → unconditional unplug.
    """
    sw = FakeSwitch("leaf1", device_type="netmiko_arista_eos")
    seg_id = "seg-uuid-100"
    sw.add_network(100, segment_id=seg_id)
    sw.plug_switch_to_network(vni=5000, segmentation_id=100)
    # Static trunk: operator-configured trunk carrying VLAN 100, no ML2 access port
    sw.add_subports_on_trunk("Fo1/1", [100])
    # Segment still exists → cleanup must NOT remove (trunk refcount>0)
    sw.del_network(100, segment_id=seg_id, segment_exists=True)
    if 100 not in sw.state.vlans or 5000 not in sw.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="static trunk vlan unplugged while segment still exists")

    # Now segment deleted (tenant deleted network) → unconditional cleanup even though static trunk still present
    sw2 = FakeSwitch("leaf2", device_type="netmiko_arista_eos")
    sw2.add_network(100, segment_id=seg_id)
    sw2.plug_switch_to_network(vni=5000, segmentation_id=100)
    sw2.add_subports_on_trunk("Fo1/1", [100])
    sw2.mark_segment_deleted(100)
    sw2.del_network(100, segment_id=seg_id, segment_exists=False)
    if 100 in sw2.state.vlans or 5000 in sw2.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="vlan/vni leaked after segment deletion (hybrid path)")
    return check_all(switches=[sw, sw2])


# ---------------------------------------------------------------------------
# 4. OVN LLDP bridge filter (LP 2144482)
# ---------------------------------------------------------------------------


def scenario_ovn_lldp_bridge_filter(seed: int = 0) -> CheckResult:
    """LP 2144482: unfiltered LLDP returns physnet1 switch for physnet2 query."""
    lldp_buggy = FakeOvnLldp(
        records=[
            LldpRecord(chassis="ch1", bridge="br-ex", physnet="physnet1", switch_id="tor-a", port_id="Eth1/1", switch_info="10.0.0.1"),
            LldpRecord(chassis="ch1", bridge="br-bm", physnet="physnet2", switch_id="tor-b", port_id="Eth1/2", switch_info="10.0.0.2"),
        ],
        filter_by_bridge=False,  # bug
    )
    # Bug: query physnet2 on br-bm but gets physnet1's entry from br-ex if unfiltered by chassis alone
    wrong = lldp_buggy.get_switch_info("ch1", "physnet1", bridge="br-ex")
    # Fixed: with filter_by_bridge=True, correct mapping
    lldp_fixed = FakeOvnLldp(records=lldp_buggy.records, filter_by_bridge=True)
    ok1 = lldp_fixed.get_switch_info("ch1", "physnet1", bridge="br-ex")
    ok2 = lldp_fixed.get_switch_info("ch1", "physnet2", bridge="br-bm")
    if ok1 != "10.0.0.1" or ok2 != "10.0.0.2":
        return CheckResult(ok=False, violated=["N2"], detail=f"bridge-filtered LLDP wrong: {ok1}/{ok2}")
    # If bug present, unfiltered cross-physnet could alias — just ensure fixed path works
    return CheckResult(ok=True, violated=[], detail="")
