"""HA states — S3 legal edges + S4 fencing, deterministic replay."""

from boundaries.db import InMemoryDb, NodeRecord
from oracle.invariants import check_all
from tracks.ha_model.states import can_transition, LEGAL_EDGES


def test_s3_legal_edges():
    assert can_transition("available", "deploying")
    assert can_transition("deploying", "active")
    assert not can_transition("available", "active")  # must go via deploying


def test_s3_takeover_illegal_rejected():
    db = InMemoryDb()
    from boundaries.db import ConductorRecord
    db.upsert_conductor(ConductorRecord(id="c0", online=True, updated_at=0))
    n = NodeRecord(uuid="n1", provision_state="available", reservation="c0", state_history=["available"])
    db.add_node(n)
    # c0 tries AVAILABLE→ACTIVE directly — illegal, should be rejected via can_transition
    assert not can_transition(n.provision_state, "active")
    # S3 not yet violated because we haven't mutated; mutate via illegal path to trigger S3
    n.state_history.append(n.provision_state)
    n.provision_state = "active"  # illegal edge
    res = check_all(db=db, now=0)
    assert "S3" in res.violated


def test_s3_takeover_legal_recovery():
    db = InMemoryDb()
    from boundaries.db import ConductorRecord
    db.upsert_conductor(ConductorRecord(id="c0", online=True, updated_at=0))
    n = NodeRecord(uuid="n1", provision_state="deploying", reservation="c0", state_history=["available", "deploying"])
    db.add_node(n)
    # Takeover: new conductor moves DEPLOYING→deploy failed (legal recovery)
    assert can_transition("deploying", "deploy failed")
    n.state_history.append(n.provision_state)
    n.provision_state = "deploy failed"
    res = check_all(db=db, now=0)
    assert res.ok
