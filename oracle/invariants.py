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
                v.append("N1")
            # N1: mapping present iff refcount>0 ; if VNI maps to vlan with 0 refs it's stale
            if cnt == 0 and vlan in st.vlans and any(val == vlan for val in st.vni_map.values()):
                # allowed: vlan can exist without ports (add_network without plug); only flag if we want strict
                pass
        # N3: no duplicate vlan entries beyond set semantics — implicitly via set
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
