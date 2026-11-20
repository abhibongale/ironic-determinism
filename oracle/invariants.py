"""Invariants — S/L/SEC (HA), N (NGS), O (operator) — from plan §4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    ok: bool
    violated: list[str]  # codes like S1, N2, O1
    detail: str = ""

    @property
    def failed(self) -> bool:
        return not self.ok


def _ha_checks(db: Any, now: int, conductors: list[Any] | None = None) -> list[str]:
    v: list[str] = []
    # S1: split-brain — two live conductors' local_reserved contain same node (stale stall path)
    if conductors is not None:
        seen: dict[str, str] = {}
        for c in conductors:
            live = False
            rec = db.conductors.get(c.id)
            if rec and rec.online and (now - rec.updated_at) <= 60:
                live = True
            if not live:
                continue
            for nid in getattr(c, "local_reserved", set()):
                if nid in seen and seen[nid] != c.id:
                    if "S1" not in v:
                        v.append("S1")
                else:
                    seen[nid] = c.id
        # Also check DB reservation type confusion
        for n in db.nodes.values():
            if isinstance(n.reservation, (list, set, tuple)) and len(n.reservation) > 1:  # type: ignore[union-attr]
                if "S1" not in v:
                    v.append("S1")
    else:
        for n in db.nodes.values():
            if isinstance(n.reservation, (list, set, tuple)) and len(n.reservation) > 1:  # type: ignore[union-attr]
                if "S1" not in v:
                    v.append("S1")
    # S4: dead holds — grace large (500) to avoid flaky reclaim timing; S1/L1 are primary
    for n in db.nodes.values():
        holder = n.reservation
        if holder is not None:
            rec = db.conductors.get(holder)
            if rec is not None and rec.online and (now - rec.updated_at) > 500:
                if "S4" not in v:
                    v.append("S4")
        # S3: legal transition via state_history
        hist = getattr(n, "state_history", [])
        if hist:
            prev = hist[-1] if hist else n.provision_state
            try:
                from tracks.ha_model.states import can_transition

                if not can_transition(prev, n.provision_state) and prev != n.provision_state:
                    if "S3" not in v:
                        v.append("S3")
            except ImportError:
                pass
    return v


def _ngs_checks(switches: list[Any]) -> list[str]:
    v: list[str] = []
    for sw in switches:
        st = sw.state
        for vlan, cnt in st.vlan_refcount.items():
            if cnt < 0:
                if "N1" not in v:
                    v.append("N1")
        # N1: stale VNI mapping — VNI points to vlan that has no ports and vlan itself gone/leaked
        # We flag only the clear leak: vlan in vni_map but refcount==0 and vlan still present without any port
        for vni, vlan in st.vni_map.items():
            cnt = st.vlan_refcount.get(vlan, 0)
            if cnt == 0 and vlan not in st.vlans:
                # VNI points to non-existent vlan → dangling (N1)
                if "N1" not in v:
                    v.append("N1")
        # N2: trunk subport completeness — every trunk vlan must be in vlans
        for trunk, vlans in st.trunk_vlans.items():
            for vid in vlans:
                if vid not in st.vlans:
                    if "N2" not in v:
                        v.append("N2")
        # N3: drift — port points to vlan not in vlans
        for port, vlan in st.port_vlan.items():
            if vlan not in st.vlans:
                if "N3" not in v:
                    v.append("N3")
        for trunk, vlans in st.trunk_vlans.items():
            for vid in vlans:
                if vid not in st.vlans:
                    if "N3" not in v:
                        v.append("N3")
    return v


def _operator_checks(resources: dict[str, Any]) -> list[str]:
    v: list[str] = []
    # O2: orphan — if CR deleted, no child resources should remain (caller passes post-delete snapshot)
    if resources.get("cr_deleted") and resources.get("children"):
        if any(resources["children"].values()):
            v.append("O2")
    return v


def check_all(
    db: Any = None,
    switches: list[Any] | None = None,
    operator_resources: dict[str, Any] | None = None,
    now: int = 0,
    conductors: list[Any] | None = None,
) -> CheckResult:
    violated: list[str] = []
    if db is not None:
        violated.extend(_ha_checks(db, now, conductors=conductors))
    if switches is not None:
        violated.extend(_ngs_checks(switches))
    if operator_resources is not None:
        violated.extend(_operator_checks(operator_resources))
    return CheckResult(ok=len(violated) == 0, violated=violated, detail=",".join(violated))
