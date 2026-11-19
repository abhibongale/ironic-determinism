"""Real NGS adapter — drives networking-generic-switch's GenericSwitchDriver
against FakeSwitch, with deterministic FaultConfig + Scheduler.

Usage:

  from tracks.ngs_sim.ngs_adapter import NGSAdapter

  adapter = NGSAdapter(
      device_type="netmiko_arista_eos",   # vendor policy flows via FakeSwitch
      switch_name="leaf1",
      physnets=["physnet1"],
  )
  # L2VNI hierarchical (top vxlan VNI 5000 → bottom vlan 100 on physnet1)
  ctx = adapter.make_port_context(
      port_id="ironic-port-1",
      switch_info="leaf1",
      port_name="Ethernet1",
      top_segment={"id": "seg-top", "network_type": "vxlan", "segmentation_id": 5000},
      bottom_segment={"id": "seg-bottom", "network_type": "vlan",
                      "segmentation_id": 100, "physical_network": "physnet1"},
  )
  adapter.driver.update_port_postcommit(ctx)   # real NGS code → FakeSwitch
  assert adapter.fake.vlan_has_vni(100, 5000)

This is the seam for finding NEW bugs: real driver templates/config handling runs,
FakeSwitch merely records what the driver asked to do. If the driver orders commands
wrong (2146511), omits VNI (2144396), or mis-filters Vxlan1 (2146507), we surface it
as N2/N1 via oracle/invariants + vendor_violations.

Design: minimal PortContext mock covering what generic_switch_mech.py actually reads
(top_bound_segment, bottom_bound_segment, current/original, provisioning_blocks, _plugin).
"""

from __future__ import annotations

from unittest import mock

from boundaries.fake_switch import FakeSwitch

# We lazily import NGS to allow pytest without full NGS installed.
# If NGS checkout is at ~/Workspace/repos/networking-generic-switch we add it to sys.path
import sys
from pathlib import Path

_NGS_PATH = Path.home() / "Workspace/repos/networking-generic-switch"
if str(_NGS_PATH) not in sys.path:
    sys.path.insert(0, str(_NGS_PATH))

from neutron_lib import constants as const

from networking_generic_switch import generic_switch_mech as gsm
from networking_generic_switch import devices


class FakePortContext:
    """Minimal PortContext seen by GenericSwitchDriver."""

    def __init__(
        self,
        port: dict,
        original: dict | None = None,
        top_segment: dict | None = None,
        bottom_segment: dict | None = None,
        network: dict | None = None,
    ) -> None:
        self.current = port
        self.original = original or {}
        self.top_bound_segment = top_segment
        self.bottom_bound_segment = bottom_segment
        self.network = mock.MagicMock()
        self.network.current = network or {"mtu": 1500}
        self._plugin: mock.MagicMock | None = None
        self._plugin_context = mock.MagicMock()
        self.plugin_context = mock.MagicMock()
        # segments_to_bind used in bind_port
        self.segments_to_bind: list[dict] = [bottom_segment] if bottom_segment else []

    def make_plugin(self, get_port_return: dict | None = None) -> None:
        p = mock.MagicMock()
        if get_port_return is not None:
            p.get_port.return_value = get_port_return
        p.get_subnet.return_value = {}
        p.update_port_status = mock.MagicMock()
        self._plugin = p
        # also expose as private expected by driver: context._plugin
        # already set


class NGSAdapter:
    """Factory: real driver + FakeSwitch behind deterministic seams."""

    def __init__(
        self,
        device_type: str = "netmiko_arista_eos",
        switch_name: str = "leaf1",
        physnets: list[str] | None = None,
        ngs_config_overrides: dict | None = None,
    ) -> None:
        self.device_type = device_type
        self.switch_name = switch_name
        self.fake = FakeSwitch(name=switch_name, device_type=device_type)
        # NGSwitchDevice contract bits the driver reads
        # GenericSwitchDriver expects DEVICES[switch_name] → device object with specific attrs
        # We bypass get_devices() and patch the driver directly.
        self.fake.config = {"device_type": device_type}
        self.fake.device_name = switch_name
        self.fake.ngs_config = {
            "ngs_physical_networks": ",".join(physnets or ["physnet1"]),
            "ngs_manage_vlans": "True",
            "ngs_save_configuration": "False",
            "ngs_network_name_format": "{network_id}",
            "ngs_manage_mtu": "False",
            "ngs_trunk_ports": "",
            **(ngs_config_overrides or {}),
        }
        # Device attributes accessed via properties/methods on FakeSwitch
        self.fake.get_physical_networks = lambda: physnets or ["physnet1"]  # type: ignore[method-assign]
        self.fake._manage_mtu = lambda: False  # type: ignore[method-assign]
        self.fake._get_port_default_mtu = lambda: None  # type: ignore[method-assign]
        self.fake._get_trunk_port_mtu = lambda: None  # type: ignore[method-assign]
        self.fake._manage_mtu = lambda: False  # type: ignore[method-assign]
        self.fake.is_allowed = lambda port_id, seg_id: True  # type: ignore[method-assign]
        self.fake.get_trunk_ports = lambda: []  # type: ignore[method-assign]
        self.fake._get_port_default_vlan = lambda: None  # type: ignore[method-assign]
        self.fake._bounce_ports_on_plug = lambda: False  # type: ignore[method-assign]
        self.fake._port_stp_edge = lambda: False  # type: ignore[method-assign]
        self.fake._port_bpdu_guard = lambda: False  # type: ignore[method-assign]
        self.fake._get_save_configuration = lambda: False  # type: ignore[method-assign]
        self.fake._reuse_connection = lambda: False  # type: ignore[method-assign]
        self.fake._batch_requests = lambda: False  # type: ignore[method-assign]
        # Contract attrs used as booleans
        self.fake.PLUG_SWITCH_TO_NETWORK = ["fake-plug-cmd"]  # truthy → supports L2VNI
        self.fake.support_trunk_on_ports = True  # type: ignore[assignment]
        self.fake.support_trunk_on_bond_ports = True  # type: ignore[assignment]

        # Build real driver, patching config.get_devices to return our fake
        self.driver = gsm.GenericSwitchDriver()
        with mock.patch(
            "networking_generic_switch.config.get_devices",
            return_value={switch_name: {"device_type": device_type, "ip": "10.0.0.1", "username": "admin", "password": "pass", "ngs_physical_networks": ",".join(physnets or ["physnet1"])}},
        ):
            self.driver.initialize()
        # Replace the driver's switch registry with our fake
        devices.DEVICES[switch_name] = self.fake
        self.driver.switches = {switch_name: self.fake}

    def make_port_context(
        self,
        port_id: str = "port-1",
        switch_info: str | None = None,
        port_name: str = "Ethernet1",
        top_segment: dict | None = None,
        bottom_segment: dict | None = None,
        trunk_details: dict | None = None,
        status: str = const.PORT_STATUS_DOWN,
    ) -> FakePortContext:
        si = switch_info or self.switch_name
        port: dict = {
            "id": port_id,
            "binding:profile": {
                "local_link_information": [
                    {"port_id": port_name, "switch_info": si, "switch_id": f"aa:bb:cc:dd:ee:ff:{port_id[-2:]}"},
                ]
            },
            "binding:host_id": "host1",
            "status": status,
            "fixed_ips": [],
        }
        if trunk_details is not None:
            port["trunk_details"] = trunk_details
        ctx = FakePortContext(port=port, top_segment=top_segment, bottom_segment=bottom_segment)
        # provisioning_blocks is a no-op in tests — patch it
        return ctx

    def make_subports_context(
        self,
        trunk_id: str = "trunk-1",
        trunk_parent_port_id: str = "Ethernet1",
        subports: list[dict] | None = None,
    ) -> dict:
        return {
            "id": trunk_id,
            "port_id": trunk_parent_port_id,
            "sub_ports": subports or [],
        }
