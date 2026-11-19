"""FakeIronicAPI — stub Ironic REST for operator track.

Minimal: nodes CRUD + provision_state transitions + injectable faults (500/409 on Nth call).
No HTTP — direct method calls so Go/scheduler can drive it deterministically.
Python mirror used for HA/NGS; Go operator_sim has its own fake (kept in sync).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class FakeIronicError(RuntimeError):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(f"{status}: {msg}")
        self.status = status


@dataclass
class IronicNode:
    uuid: str
    name: str
    provision_state: str = "available"
    target_state: str | None = None
    driver: str = "fake-hardware"
    instance_uuid: str | None = None


class FakeIronicAPI:
    def __init__(self) -> None:
        self.nodes: dict[str, IronicNode] = {}
        self.call_count = 0
        # fail_on: set of call numbers that raise
        self.fail_on: set[int] = set()
        self.fail_status: int = 500

    def _tick(self, op: str) -> None:
        self.call_count += 1
        if self.call_count in self.fail_on:
            raise FakeIronicError(self.fail_status, f"injected {op} failure #{self.call_count}")

    def create_node(self, node: IronicNode) -> IronicNode:
        self._tick("create_node")
        self.nodes[node.uuid] = node
        return node

    def get_node(self, uuid: str) -> IronicNode:
        self._tick("get_node")
        if uuid not in self.nodes:
            raise FakeIronicError(404, f"node {uuid} not found")
        return self.nodes[uuid]

    def set_provision_state(self, uuid: str, target: str) -> None:
        self._tick("set_provision_state")
        n = self.get_node(uuid)
        n.target_state = target
        # simplified FSM advance (real states.py is richer — HA track ports it)
        n.provision_state = f"{n.provision_state}->{target}"

    def delete_node(self, uuid: str) -> None:
        self._tick("delete_node")
        self.nodes.pop(uuid, None)

    def list_nodes(self) -> list[IronicNode]:
        self._tick("list_nodes")
        return list(self.nodes.values())
