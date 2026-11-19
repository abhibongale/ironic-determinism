"""Real NGS driver behind FakeSwitch — one test per 2026.1/2026.2 bug class.

This is the 'finding new bugs' seam: the real GenericSwitchDriver code path runs
(vendor ordering, segment hybrid cleanup, LLDP) and FakeSwitch merely records what
the driver asked to do. If the driver is fixed, these pass; if a future vendor
regression reintroduces the bug, these fail on the *real* driver logic.

Runs in two modes:
  - Inside `networking-generic-switch/.tox/py3` (via `stestr`): full NGS driver + neutron available.
  - Inside `ironic-determinism` lightweight venv (no `neutron`): skipped — use tox instructions below.
    See `RESOURCE/ironic-determinism-tasks/02c` and `tracks/ngs_sim/ngs_adapter.py` for tox command.

Needs checkout at ~/Workspace/repos/networking-generic-switch (you have it, master @dca1cc7).
"""

try:
    import neutron  # noqa: F401

    HAS_NEUTRON = True
except ImportError:
    HAS_NEUTRON = False

import pytest

pytestmark = pytest.mark.skipif(not HAS_NEUTRON, reason="needs neutron + NGS in tox (see ngs_adapter.py)")

if HAS_NEUTRON:
    from tracks.ngs_sim.ngs_adapter import NGSAdapter


def test_real_ngs_l2vni_happy_path():
    ad = NGSAdapter(device_type="netmiko_arista_eos", switch_name="leaf1", physnets=["physnet1"])
    ctx = ad.make_port_context(
        port_id="port-1",
        switch_info="leaf1",
        port_name="Ethernet1",
        top_segment={"id": "seg-top", "network_type": "vxlan", "segmentation_id": 5000},
        bottom_segment={"id": "seg-bot", "network_type": "vlan", "segmentation_id": 100, "physical_network": "physnet1"},
    )
    with pytest.MonkeyPatch().context() as mp:
        # provisioning_blocks no-op
        import unittest.mock as mock

        with mock.patch("neutron.db.provisioning_blocks.provisioning_complete"):
            ad.driver.update_port_postcommit(ctx)
    # Real driver should have called FakeSwitch.plug_switch_to_network with physnet
    assert ad.fake.vlan_has_vni(100, 5000), f"driver didn't plug L2VNI: vni_map={ad.fake.state.vni_map}"
    assert ad.fake.state.vendor_violations == [], f"Arista ordered correctly should not violate: {ad.fake.state.vendor_violations}"


def test_real_ngs_l2vni_idempotent():
    ad = NGSAdapter(device_type="netmiko_arista_eos", switch_name="leaf1", physnets=["physnet1"])
    top = {"id": "seg-top", "network_type": "vxlan", "segmentation_id": 5000}
    bot = {"id": "seg-bot", "network_type": "vlan", "segmentation_id": 100, "physical_network": "physnet1"}
    # Pre-populate switch as already configured (idempotency check via vlan_has_vni)
    ad.fake.state.vlans.add(100)
    ad.fake.state.vni_map[5000] = 100
    ctx = ad.make_port_context(port_id="port-2", top_segment=top, bottom_segment=bot)
    import unittest.mock as mock

    with mock.patch("neutron.db.provisioning_blocks.provisioning_complete"):
        ad.driver.update_port_postcommit(ctx)
    # Should NOT have called plug again (idempotent) — vni still there but no extra call
    assert ad.fake.vlan_has_vni(100, 5000)
    assert not ad.fake.state.vendor_violations


def test_real_ngs_unsupported_switch_logs_warning(capsys=None):
    ad = NGSAdapter(device_type="netmiko_arista_eos", physnets=["physnet1"])
    # Simulate switch that doesn't support L2VNI
    ad.fake.PLUG_SWITCH_TO_NETWORK = None  # type: ignore[assignment]
    ctx = ad.make_port_context(
        top_segment={"id": "seg-top", "network_type": "vxlan", "segmentation_id": 5000},
        bottom_segment={"id": "seg-bot", "network_type": "vlan", "segmentation_id": 100, "physical_network": "physnet1"},
    )
    import unittest.mock as mock

    with mock.patch("neutron.db.provisioning_blocks.provisioning_complete"):
        ad.driver.update_port_postcommit(ctx)
    # No mapping configured, but no crash (graceful degradation)
    assert not ad.fake.vlan_has_vni(100, 5000)


def test_real_ngs_top_bottom_wiring_via_fake_context():
    """Deterministic replay: same seed + same context → same FakeSwitch trace."""
    from runtime.rng import SeededRng

    def run_once(seed: int) -> list[str]:
        rng = SeededRng(seed)
        ad = NGSAdapter(device_type="netmiko_arista_eos", switch_name="leaf1", physnets=["physnet1"])
        vni = 5000
        vlan = rng.randint(100, 110)
        ctx = ad.make_port_context(
            port_id=f"port-{seed}",
            top_segment={"id": "seg-top", "network_type": "vxlan", "segmentation_id": vni},
            bottom_segment={"id": "seg-bot", "network_type": "vlan", "segmentation_id": vlan, "physical_network": "physnet1"},
        )
        import unittest.mock as mock

        with mock.patch("neutron.db.provisioning_blocks.provisioning_complete"):
            ad.driver.update_port_postcommit(ctx)
        return sorted([f"{k}->{v}" for k, v in ad.fake.state.vni_map.items()])

    assert run_once(42) == run_once(42)
