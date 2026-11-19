"""Vendor-specific NGS policies — extracted from release notes 2026.1/2026.2.

Real driver quirks that are bug-prone and must be modeled to catch ordering/syntax bugs:

- Arista EOS (LP 2146511): VLAN must be created before configuring VXLAN/BGP-EVPN mappings
  → FakeSwitch should enforce vlan_before_vxlan for this vendor.
- SONiC (LP 2148349): same VLAN-before-member constraint for trunk subports.
- Arista unplug syntax (LP 2146508): `no vxlan vlan {vlan} vni {vni}` vs wrong `no vxlan vlan {vlan}`
  → unplug must pass VNI.
- Arista cleanup (LP 2146507): Vxlan1/Vx1 interfaces must be excluded from physical-port VLAN cleanup.
- NX-OS: opposite — no vlan_before_vxlan requirement, different NVE handling.

Model: per-device-type policy dict, checked in FakeSwitch boundary.
"""

from __future__ import annotations

VENDOR_POLICIES: dict[str, dict[str, object]] = {
    "netmiko_arista_eos": {
        "vlan_before_vxlan": True,
        "unplug_needs_vni": True,
        "cleanup_excludes": {"Vxlan1", "Vx1", "Vx1.0"},
    },
    "netmiko_sonic": {
        "vlan_before_vxlan": True,
        "unplug_needs_vni": False,
        "cleanup_excludes": set(),
    },
    "netmiko_cisco_nxos": {
        "vlan_before_vxlan": False,
        "unplug_needs_vni": False,
        "cleanup_excludes": set(),
    },
    "netmiko_juniper_junos": {
        "vlan_before_vxlan": False,
        "unplug_needs_vni": False,
        "cleanup_excludes": set(),
    },
    "netmiko_cumulus_nvue": {
        "vlan_before_vxlan": False,
        "unplug_needs_vni": False,
        "cleanup_excludes": set(),
    },
    "netmiko_dell_os10": {
        "vlan_before_vxlan": False,
        "unplug_needs_vni": False,
        "cleanup_excludes": set(),
    },
    "_default": {
        "vlan_before_vxlan": False,
        "unplug_needs_vni": False,
        "cleanup_excludes": set(),
    },
}

ALL_VENDORS = [k for k in VENDOR_POLICIES if k != "_default"]


def policy_for(device_type: str) -> dict[str, object]:
    return VENDOR_POLICIES.get(device_type, VENDOR_POLICIES["_default"])
