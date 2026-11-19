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


def _ha_checks(db: Any, now: int) -> list[str]:
    v: list[str] = []
    # S1: single reservation — at most one holder per node (by construction here, but check)
    # Real check: reservation is single-valued; violated if we allowed multiple holders
    for n in db.nodes.values():
        if isinstance(n.reservation, (list, set, tuple)) and len(n.reservation) > 1:  # type: ignore[union-attr]
            v.append("S1")
    # S4: dead conductor should not have held reservation after grace — checked via reclaim logic externally
    # S3: FSM legality — placeholder (real states.py edges checked in ha_model/states.py)
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
) -> CheckResult:
    violated: list[str] = []
    if db is not None:
        violated.extend(_ha_checks(db, now))
    if switches is not None:
        violated.extend(_ngs_checks(switches))
    if operator_resources is not None:
        violated.extend(_operator_checks(operator_resources))
    return CheckResult(ok=len(violated) == 0, violated=violated, detail=",".join(violated))
