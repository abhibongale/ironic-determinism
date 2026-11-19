"""VxLAN / Geneve L2VNI — explicit top/bottom hierarchical binding tests."""

from campaign.ngs_runner import run_one_ngs, SCENARIOS
from tracks.ngs_sim.vxlan import scenario_vxlan_geneve_parity, scenario_vxlan_top_bottom
from tracks.ngs_sim.vxlan_segments import BaremetalL2VNISim, SegmentRange


def test_vxlan_top_bottom():
    assert scenario_vxlan_top_bottom().ok


def test_vxlan_geneve_parity():
    assert scenario_vxlan_geneve_parity().ok


def test_segment_range_alloc_isolation():
    l2vni = BaremetalL2VNISim(
        ranges={
            "physnet1": SegmentRange("physnet1", vlan_min=100, vlan_max=110),
            "physnet2": SegmentRange("physnet2", vlan_min=200, vlan_max=210),
        }
    )
    l2vni.ensure_top_segment("net-1", vni=5000, network_type="vxlan")
    b1 = l2vni.ensure_bottom_segment("net-1", "physnet1")
    b2 = l2vni.ensure_bottom_segment("net-1", "physnet2")
    assert b1.segmentation_id != b2.segmentation_id
    assert b1.physical_network == "physnet1"
    assert b2.physical_network == "physnet2"


def test_vxlan_runner_deterministic():
    r1 = run_one_ngs(42, "vxlan_top_bottom")
    r2 = run_one_ngs(42, "vxlan_top_bottom")
    assert r1.ok == r2.ok and r1.violated == r2.violated
