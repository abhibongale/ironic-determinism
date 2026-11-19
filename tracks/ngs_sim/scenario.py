"""P0+A NGS scenarios — basic/refcount/trunk + multi-switch (N2 LP2144396) + concurrency.

Each function is (seed) -> CheckResult so campaign runner can sweep it deterministically.
"""

from __future__ import annotations

from boundaries.fake_switch import FakeSwitch
from oracle.invariants import CheckResult, check_all
from runtime.clock import VirtualClock
from runtime.rng import SeededRng
from runtime.scheduler import Scheduler


# ---------------------------------------------------------------------------
# P0 — single-switch basics
# ---------------------------------------------------------------------------


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
    for vid in [301, 302]:
        if sw.state.vlan_refcount.get(vid, 0) == 0:
            return CheckResult(ok=False, violated=["N2"], detail=f"subport vlan {vid} not counted")
    return check_all(switches=[sw])


# ---------------------------------------------------------------------------
# A1 — multi-switch (core design promise: same VNI, different local VLANs)
# ---------------------------------------------------------------------------


def scenario_multi_switch(seed: int = 0) -> CheckResult:
    """A1: Two physnets, same VNI 5000 maps to vlan 100 on brbm1 and vlan 105 on brbm2."""
    sw1 = FakeSwitch("brbm1")
    sw2 = FakeSwitch("brbm2")

    # per-physnet local VLAN allocation (simulates baremetal-l2vni dynamic segment)
    sw1.add_network(100, "net-vxlan")
    sw2.add_network(105, "net-vxlan")

    # L2VNI mapping: same VNI → different local VLANs
    sw1.plug_switch_to_network(vni=5000, segmentation_id=100)
    sw2.plug_switch_to_network(vni=5000, segmentation_id=105)
    if not sw1.vlan_has_vni(100, 5000):
        return CheckResult(ok=False, violated=["N2"], detail="brbm1 VNI 5000 not on vlan 100")
    if not sw2.vlan_has_vni(105, 5000):
        return CheckResult(ok=False, violated=["N2"], detail="brbm2 VNI 5000 not on vlan 105")

    # baremetal ports, one per switch
    sw1.plug_port_to_network("eth0", 100)
    sw2.plug_port_to_network("eth0", 105)

    # trunk subports carrying same VNI (LP 2144396: subport binding must carry VNI)
    sw1.add_subports_on_trunk("trunk0", [101])
    sw1.add_network(101, "subport-net")
    sw1.plug_switch_to_network(vni=5001, segmentation_id=101)

    sw2.add_subports_on_trunk("trunk0", [106])
    sw2.add_network(106, "subport-net")
    sw2.plug_switch_to_network(vni=5001, segmentation_id=106)

    # per-switch refcount: delete all ports on sw1 → sw1 mapping gone, sw2 survives
    sw1.delete_port("eth0", 100)
    sw1.del_subports_on_trunk("trunk0", [101])
    sw1.del_network(100)
    sw1.del_network(101)
    sw1.unplug_switch_from_network(vni=5000, segmentation_id=100)
    sw1.unplug_switch_from_network(vni=5001, segmentation_id=101)

    if 100 in sw1.state.vlans or 5000 in sw1.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="brbm1 leaked after cleanup")

    # sw2 must still have its mapping
    if 105 not in sw2.state.vlans or 5000 not in sw2.state.vni_map:
        return CheckResult(ok=False, violated=["N1"], detail="brbm2 mapping lost (cross-switch refcount bug)")

    return check_all(switches=[sw1, sw2])


def scenario_multi_switch_ref_bug(seed: int = 0) -> CheckResult:
    """Regression: shared VNI refcount must be per-switch, not global.
    A naive global counter would delete sw2's mapping when sw1's last port leaves."""
    sw1 = FakeSwitch("brbm1")
    sw2 = FakeSwitch("brbm2")
    for sw, vlan in [(sw1, 100), (sw2, 105)]:
        sw.add_network(vlan)
        sw.plug_switch_to_network(vni=5000, segmentation_id=vlan)
        sw.plug_port_to_network("p1", vlan)
    # only sw1's port leaves — sw2 must be untouched
    sw1.delete_port("p1", 100)
    sw1.del_network(100)
    # sw2 still has one port → its vlan must survive even though sw1 cleaned up
    if 105 not in sw2.state.vlans:
        return CheckResult(ok=False, violated=["N1"], detail="global refcount broke per-switch isolation")
    return check_all(switches=[sw1, sw2])


# ---------------------------------------------------------------------------
# A2 — DLM concurrency / query-then-configure race
# ---------------------------------------------------------------------------


def scenario_concurrent_plug(seed: int = 0) -> CheckResult:
    """A2: N ports binding to same VNI concurrently — scheduler interleaves them.
    Uses deterministic Scheduler to force races; checks no double-add / missed refcount."""
    rng = SeededRng(seed)
    clock = VirtualClock()
    sched = Scheduler(rng, clock)
    sw = FakeSwitch("brbm")

    sw.add_network(200, "net-concurrent")
    sw.plug_switch_to_network(vni=5000, segmentation_id=200)

    n = 5
    errors: list[str] = []

    def make_plug(port_id: int) -> None:
        def fn() -> None:
            try:
                sw.plug_port_to_network(f"eth{port_id}", 200)
            except Exception as e:  # pragma: no cover
                errors.append(str(e))

        # all at same vtime → scheduler seq decides order deterministically
        sched.schedule_after(rng.randint(0, 3), f"plug-{port_id}", fn)

    for i in range(n):
        make_plug(i)

    # drain
    while sched.has_events():
        ev = sched.pop_next()
        ev.fn()
        res = check_all(switches=[sw])
        if res.failed:
            return res

    if errors:
        return CheckResult(ok=False, violated=["N1"], detail=f"concurrent plug errors: {errors}")

    # invariants after all plugs
    if sw.state.vlan_refcount.get(200, 0) != n:
        return CheckResult(
            ok=False,
            violated=["N1"],
            detail=f"refcount {sw.state.vlan_refcount.get(200, 0)} != {n} after concurrent plug",
        )
    # now unplug concurrently
    for i in range(n):
        sw.delete_port(f"eth{i}", 200)
    sw.del_network(200)  # should succeed only when refcount 0
    # after all unplugged, vlan should be gone after del
    if 200 in sw.state.vlans:
        return CheckResult(ok=False, violated=["N1"], detail="vlan leaked after concurrent unplug")

    return check_all(switches=[sw])


def scenario_concurrent_with_faults(seed: int = 0) -> CheckResult:
    """A2 variant: concurrent plug with injected SSH failures (seeded).
    Verifies retry doesn't corrupt refcount."""
    rng = SeededRng(seed)
    sw = FakeSwitch("brbm")
    sw.add_network(300)
    sw.plug_switch_to_network(vni=6000, segmentation_id=300)

    ops = [("plug", f"p{i}", 300) for i in range(4)]
    # seed decides which ops get a transient failure that is retried once
    for op, port, vlan in ops:
        fail = rng.coinflip(p=0.25)
        if fail:
            sw.fail_next = 1
            try:
                sw.plug_port_to_network(port, vlan)
            except RuntimeError:
                # retry once
                sw.plug_port_to_network(port, vlan)
        else:
            sw.plug_port_to_network(port, vlan)

    # refcount must equal number of ports (retries shouldn't double-count on failure)
    if sw.state.vlan_refcount.get(300, 0) != len(ops):
        return CheckResult(
            ok=False,
            violated=["N1"],
            detail=f"refcount after faults {sw.state.vlan_refcount.get(300)} != {len(ops)}",
        )
    return check_all(switches=[sw])
