"""SEC2 + S4 — stale token after steal"""

from boundaries.db import ConductorRecord, InMemoryDb, NodeRecord


def test_sec2_stale_token_after_steal():
    db = InMemoryDb()
    db.upsert_conductor(ConductorRecord(id="c0", online=True, updated_at=0))
    db.upsert_conductor(ConductorRecord(id="c1", online=True, updated_at=0))
    db.add_node(NodeRecord(uuid="n1", provision_state="available"))
    assert db.reserve("n1", "c0")
    n = db.nodes["n1"]
    old_token = n.ipa_token
    assert old_token is not None
    assert db.validate_token("n1", old_token)
    # c0 dies, c1 steals
    db.steal("n1", "c1")
    new_token = db.nodes["n1"].ipa_token
    assert new_token != old_token
    assert not db.validate_token("n1", old_token), "old token must be rejected (SEC2)"
    assert db.validate_token("n1", new_token)
    # Simulate stale holder trying to use old token to mutate — should be rejected
    assert not db.validate_token("n1", old_token)


def test_sec2_oracle_fires_on_token_divergence():
    from oracle.invariants import check_all

    db = InMemoryDb()
    db.upsert_conductor(ConductorRecord(id="c0", online=True, updated_at=0))
    db.add_node(NodeRecord(uuid="n1", provision_state="available", reservation="c0", token_holder="c1", ipa_token="token-n1-c1-2"))
    db.nodes["n1"].token_holder = "c1"  # divergence: holder c0 but token for c1
    res = check_all(db=db, now=0)
    assert "SEC2" in res.violated
