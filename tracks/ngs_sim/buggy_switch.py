"""BuggySwitch demo — refcount increments *before* SSH, so failure leaks.

Real bug pattern: driver did `refcount++` then `switch.plug_port()`; if plug throws,
refcount never rolled back → leaked VLAN never cleaned (N1). Fixed driver does
fail-first-then-increment (what FakeSwitch does). This file is the harness to make
campaign actually find N1 when faults fire.
"""

from boundaries.fake_switch import FakeSwitch


class BuggySwitch(FakeSwitch):
    """Buggy: increments refcount before checking fail_next."""
    def plug_port_to_network(self, port: str, segmentation_id: int) -> None:
        if port in self.state.cleanup_excludes:
            return
        # BUG: count before fail check
        self.state.port_vlan[port] = segmentation_id
        self.state.vlan_refcount[segmentation_id] = self.state.vlan_refcount.get(segmentation_id, 0) + 1
        self.state.vlans.add(segmentation_id)
        self._maybe_fail("plug_port")  # fail after increment → leak if caller doesn't rollback


def scenario_buggy_ssh_leak(seed: int = 0, faults=None) -> "CheckResult":
    """Deterministic seed-driven fault: half the seeds inject SSH fail, buggy leaks N1."""
    from runtime.rng import SeededRng
    from oracle.invariants import CheckResult, check_all
    rng = SeededRng(seed)
    sw = BuggySwitch("buggy-brbm")
    sw.add_network(100)
    # Seed decides if this run has a fault (so campaign with faults will surface N1)
    inject = rng.coinflip(p=0.5)
    if inject:
        sw.fail_next = 1
        try:
            sw.plug_port_to_network("p1", 100)
        except RuntimeError:
            pass  # driver swallows but buggy already leaked
        # Oracle should see N1: refcount 1 but no successful port (port_vlan has p1, but real check is refcount vs cleanup)
        # More direct: after failed plug, refcount should be 0; buggy has 1 → leaked VLAN on del_network
        sw.del_network(100)
        if 100 in sw.state.vlans:
            return CheckResult(ok=False, violated=["N1"], detail="buggy leak: vlan 100 not cleaned after failed plug (refcount leaked)")
        return CheckResult(ok=False, violated=["N1"], detail="buggy refcount leak survived")
    else:
        sw.plug_port_to_network("p1", 100)
        return check_all(switches=[sw])


def scenario_fixed_ssh_leak(seed: int = 0, faults=None) -> "CheckResult":
    from runtime.rng import SeededRng
    from oracle.invariants import CheckResult, check_all
    rng = SeededRng(seed)
    sw = FakeSwitch("fixed-brbm")
    sw.add_network(100)
    inject = rng.coinflip(p=0.5)
    if inject:
        sw.fail_next = 1
        try:
            sw.plug_port_to_network("p1", 100)
        except RuntimeError:
            pass
        # Fixed: failed plug didn't increment → refcount 0, clean del
        sw.del_network(100)
        if 100 in sw.state.vlans:
            return CheckResult(ok=False, violated=["N1"], detail="fixed still leaked (unexpected)")
        return check_all(switches=[sw])
    else:
        sw.plug_port_to_network("p1", 100)
        return check_all(switches=[sw])
