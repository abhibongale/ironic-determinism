"""VxLAN / Geneve L2VNI segment model — mirrors networking-baremetal baremetal-l2vni.

Upstream flow (gap analysis §1):
  1. Tenant creates network type=vxlan (or geneve) → top segment with VNI (e.g. 5000)
     Regular user — no admin needed.
  2. baremetal-l2vni detects baremetal VNIC (vnic_type=baremetal + local_link_connection),
     finds the VXLAN segment, allocates/reuses a dynamic local VLAN segment on that physnet
     from segment-range pool → hierarchical binding (top=VNI, bottom=local VLAN).
  3. Genericswitch programs the ToR VTEP: VNI→VLAN on the overlay interface + port into VLAN.
     Core invariant: same VNI → different local VLANs per physnet
     (VNI 10000 → VLAN 100 on physnet1, VLAN 105 on physnet2).

This module provides a minimal deterministic model of that allocation + binding so NGS
scenarios can be written in VxLAN terms (top/bottom), not just raw FakeSwitch calls.
Reuse: `networking_generic_switch/devices/netmiko_devices/ovs.py` external_ids:vni-5000=42
is the real control-plane backing; we model it as FakeSwitch.vni_map.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Segment:
    """Neutron segment — mirrors neutron.db.models.segment.Segment."""
    id: str
    network_id: str
    network_type: str  # "vxlan" | "geneve" | "vlan"
    physical_network: str | None  # None for top overlay segment
    segmentation_id: int  # VNI for vxlan/geneve, VLAN ID for vlan
    is_top: bool = False


@dataclass
class SegmentRange:
    """Pool for dynamic local VLAN allocation per physnet."""
    physical_network: str
    network_type: str = "vlan"
    vlan_min: int = 100
    vlan_max: int = 200
    _next: int = field(init=False)

    def __post_init__(self) -> None:
        self._next = self.vlan_min

    def alloc(self) -> int:
        if self._next > self.vlan_max:
            raise RuntimeError(f"segment range exhausted on {self.physical_network}")
        v = self._next
        self._next += 1
        return v

    def release(self, vlan: int) -> None:
        # simple model: no reuse tracking needed for sweep
        pass


class BaremetalL2VNISim:
    """Deterministic stand-in for networking-baremetal baremetal-l2vni driver.

    Responsibilities modeled:
    - Find the overlay segment (vxlan/geneve) on the network.
    - Allocate/reuse a local VLAN segment on the port's physnet.
    - Create hierarchical binding (top+VNI, bottom+VLAN).
    """

    def __init__(self, ranges: dict[str, SegmentRange] | None = None) -> None:
        self.ranges: dict[str, SegmentRange] = ranges or {}
        self.segments: list[Segment] = []
        # key: (network_id, physnet) -> bottom vlan
        self._alloc_map: dict[tuple[str, str], int] = {}
        # key: physnet -> vlan -> refcount (for reuse/cleanup semantics)
        self._bottom_ref: dict[tuple[str, int], int] = {}
        self._seg_counter = 0

    def ensure_top_segment(self, network_id: str, vni: int, network_type: str = "vxlan") -> Segment:
        for s in self.segments:
            if s.network_id == network_id and s.network_type == network_type and s.is_top:
                return s
        seg = Segment(
            id=f"seg-{self._seg_counter}",
            network_id=network_id,
            network_type=network_type,
            physical_network=None,
            segmentation_id=vni,
            is_top=True,
        )
        self._seg_counter += 1
        self.segments.append(seg)
        return seg

    def get_top_segment(self, network_id: str) -> Segment | None:
        for s in self.segments:
            if s.network_id == network_id and s.is_top:
                return s
        return None

    def ensure_bottom_segment(self, network_id: str, physnet: str) -> Segment:
        """Allocate or reuse bottom VLAN for (network, physnet)."""
        key = (network_id, physnet)
        if key in self._alloc_map:
            vlan = self._alloc_map[key]
        else:
            rng = self.ranges.get(physnet)
            if not rng:
                raise KeyError(f"no segment range for physnet {physnet}")
            vlan = rng.alloc()
            self._alloc_map[key] = vlan
        # refcount
        self._bottom_ref[(physnet, vlan)] = self._bottom_ref.get((physnet, vlan), 0) + 1
        top = self.get_top_segment(network_id)
        assert top is not None, "top segment must exist before bottom"
        seg = Segment(
            id=f"seg-{self._seg_counter}",
            network_id=network_id,
            network_type="vlan",
            physical_network=physnet,
            segmentation_id=vlan,
            is_top=False,
        )
        self._seg_counter += 1
        self.segments.append(seg)
        return seg

    def release_bottom(self, network_id: str, physnet: str) -> None:
        key = (network_id, physnet)
        vlan = self._alloc_map.get(key)
        if vlan is None:
            return
        ref = self._bottom_ref.get((physnet, vlan), 0)
        if ref <= 1:
            self._bottom_ref.pop((physnet, vlan), None)
            self._alloc_map.pop(key, None)
            # remove bottom segment records for this (network, physnet)
            self.segments = [s for s in self.segments if not (s.network_id == network_id and s.physical_network == physnet)]
        else:
            self._bottom_ref[(physnet, vlan)] = ref - 1

    def hierarchical_binding(self, network_id: str, physnet: str) -> tuple[Segment, Segment]:
        """Return (top, bottom) for a baremetal port on (network, physnet)."""
        top = self.get_top_segment(network_id)
        assert top is not None
        # find existing bottom or create via ensure
        bottom = next((s for s in self.segments if s.network_id == network_id and s.physical_network == physnet and not s.is_top), None)
        if bottom is None:
            bottom = self.ensure_bottom_segment(network_id, physnet)
        return top, bottom
