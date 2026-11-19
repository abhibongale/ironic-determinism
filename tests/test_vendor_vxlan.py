"""Vendor VxLAN ordering + LLDP + segment-id cleanup — Phase A strength."""

import pytest

from tracks.ngs_sim.vendor_policies import ALL_VENDORS
from tracks.ngs_sim.vendor_vxlan import (
    scenario_arista_cleanup_filter,
    scenario_arista_unplug_needs_vni,
    scenario_arista_vxlan_order,
    scenario_ovn_lldp_bridge_filter,
    scenario_segment_id_cleanup,
    scenario_sonic_vlan_before_trunk,
)


@pytest.mark.parametrize("device_type", ALL_VENDORS)
def test_vendor_policy_known(device_type: str) -> None:
    from tracks.ngs_sim.vendor_policies import policy_for

    p = policy_for(device_type)
    assert "vlan_before_vxlan" in p
    assert "unplug_needs_vni" in p


def test_arista_vxlan_order():
    assert scenario_arista_vxlan_order().ok


def test_sonic_vlan_before_trunk():
    assert scenario_sonic_vlan_before_trunk().ok


def test_arista_cleanup_filter():
    assert scenario_arista_cleanup_filter().ok


def test_arista_unplug_needs_vni():
    assert scenario_arista_unplug_needs_vni().ok


def test_segment_id_cleanup():
    assert scenario_segment_id_cleanup().ok


def test_ovn_lldp_bridge_filter():
    assert scenario_ovn_lldp_bridge_filter().ok
