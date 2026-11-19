"""P0 NGS scenario — basic/idempotent/refcount/trunk on FakeSwitch.

Each function is (seed, switch) -> CheckResult so campaign runner can sweep it.
"""

from __future__ import annotations

from boundaries.fake_switch import FakeSwitch
from oracle.invariants import CheckResult, check_all


def scenario_basic(seed: int = 0) -> CheckResult:
    sw = FakeSwitch("brbm")
    sw.add_network(100, "net-1")
    sw.plug_port_to_network("eth0", 100)
    assert sw.vlan_has_ports(100)
    sw.delete_port("eth0", 100)
    sw.del_network(100)
    return check_all(switches=[sw])


def scenario_refcount_stale_vlan(seed: int = 0) -> CheckResult:
    """N1: VLAN surviving after last port removed — bug if del_network skipped."""
    sw = FakeSwitch("brbm")
    sw.add_network(200, "net-2")
    sw.plug_port_to_network("p1", 200)
    sw.plug_port_to_network("p2", 200)
    sw.delete_port("p1", 200)
    # still one port → del should be no-op (port still needs vlan)
    sw.del_network(200)
    if 200 not in sw.state.vlans:
        return CheckResult(ok=False, violated=["N1"], detail="vlan removed while refcount>0")
    sw.delete_port("p2", 200)
    sw.del_network(200)
    if 200 in sw.state.vlans:
        return CheckResult(ok=False, violated=["N1"], detail="vlan leaked after refcount 0")
    return check_all(switches=[sw])


def scenario_trunk_subport(seed: int = 0) -> CheckResult:
    """N2: trunk bind must include subport VNIs (LP 2144396 class)."""
    sw = FakeSwitch("brbm")
    sw.add_network(300, "net-3")
    sw.plug_switch_to_network(vni=5000, segmentation_id=300)
    sw.add_subports_on_trunk("trunk0", [301, 302])
    # simulate NGS expecting VNI mapped for each subport vlan
    for vid in [301, 302]:
        if sw.state.vlan_refcount.get(vid, 0) == 0:
            return CheckResult(ok=False, violated=["N2"], detail=f"subport vlan {vid} not counted")
    return check_all(switches=[sw])
