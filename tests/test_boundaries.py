from boundaries.fake_switch import FakeSwitch
from boundaries.fake_ironic_api import FakeIronicAPI, FakeIronicError, IronicNode
from tracks.ngs_sim.scenario import scenario_basic, scenario_refcount_stale_vlan, scenario_trunk_subport


def test_fake_switch_basic():
    assert scenario_basic().ok


def test_fake_switch_refcount():
    assert scenario_refcount_stale_vlan().ok


def test_fake_switch_trunk():
    assert scenario_trunk_subport().ok


def test_fake_switch_fault_injection():
    sw = FakeSwitch()
    sw.fail_next = 1
    try:
        sw.add_network(100)
        assert False, "should have raised"
    except RuntimeError:
        pass
    sw.add_network(100)
    assert 100 in sw.state.vlans


def test_fake_ironic_api_500_on_nth():
    api = FakeIronicAPI()
    api.create_node(IronicNode(uuid="n1", name="n1"))
    api.fail_on = {2}
    api.fail_status = 500
    try:
        api.get_node("n1")
        assert False
    except FakeIronicError as e:
        assert e.status == 500
    # next call succeeds
    n = api.get_node("n1")
    assert n.uuid == "n1"
