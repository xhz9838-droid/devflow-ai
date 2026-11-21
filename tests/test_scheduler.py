from concurrent.futures import ThreadPoolExecutor

import pytest

from devflow.errors import DAGValidationError
from devflow.models import NodeStatus
from devflow.scheduler import DAGScheduler, topological_order


def test_topological_order():
    specs = [
        {"id": "c", "dependencies": ["b"]},
        {"id": "a"},
        {"id": "b", "dependencies": ["a"]},
    ]
    assert topological_order(specs) == ["a", "b", "c"]


def test_cycle_is_rejected():
    with pytest.raises(DAGValidationError, match="cycle"):
        DAGScheduler.validate(
            [
                {"id": "a", "dependencies": ["c"]},
                {"id": "b", "dependencies": ["a"]},
                {"id": "c", "dependencies": ["b"]},
            ]
        )


def test_unknown_dependency_is_rejected():
    with pytest.raises(DAGValidationError, match="unknown"):
        DAGScheduler.validate([{"id": "a", "dependencies": ["ghost"]}])


def test_duplicate_and_self_dependency_are_rejected():
    with pytest.raises(DAGValidationError, match="duplicate"):
        DAGScheduler.validate([{"id": "a"}, {"id": "a"}])
    with pytest.raises(DAGValidationError, match="itself"):
        DAGScheduler.validate([{"id": "a", "dependencies": ["a"]}])


def test_only_root_nodes_are_ready_initially(store, create_workflow):
    workflow = create_workflow(
        [{"id": "a"}, {"id": "b", "dependencies": ["a"]}, {"id": "c", "dependencies": ["a"]}]
    )
    scheduler = DAGScheduler(store)
    ready = scheduler.refresh(workflow.id)
    assert [n.id for n in ready] == ["a"]
    assert store.get_node("b").status is NodeStatus.PENDING


def test_dependents_become_ready_after_success(store, create_workflow):
    workflow = create_workflow([{"id": "a"}, {"id": "b", "dependencies": ["a"]}])
    scheduler = DAGScheduler(store)
    scheduler.refresh(workflow.id)
    store.update_node_status("a", NodeStatus.RUNNING)
    store.update_node_status("a", NodeStatus.SUCCEEDED)
    ready = scheduler.refresh(workflow.id)
    assert [n.id for n in ready] == ["b"]


def test_failure_blocks_downstream_transitively(store, create_workflow):
    workflow = create_workflow(
        [
            {"id": "a"},
            {"id": "b", "dependencies": ["a"]},
            {"id": "c", "dependencies": ["b"]},
            {"id": "d"},
        ]
    )
    scheduler = DAGScheduler(store)
    scheduler.refresh(workflow.id)
    store.update_node_status("a", NodeStatus.RUNNING)
    store.update_node_status("a", NodeStatus.FAILED)
    ready = scheduler.refresh(workflow.id)
    assert [n.id for n in ready] == ["d"]
    assert store.get_node("b").status is NodeStatus.BLOCKED
    assert store.get_node("c").status is NodeStatus.BLOCKED


def test_blocked_node_becomes_ready_when_upstream_recovers(store, create_workflow):
    workflow = create_workflow([{"id": "a"}, {"id": "b", "dependencies": ["a"]}])
    scheduler = DAGScheduler(store)
    scheduler.refresh(workflow.id)
    store.update_node_status("a", NodeStatus.RUNNING)
    store.update_node_status("a", NodeStatus.FAILED)
    scheduler.refresh(workflow.id)
    assert store.get_node("b").status is NodeStatus.BLOCKED

    store.update_node_status("a", NodeStatus.READY)
    store.update_node_status("a", NodeStatus.RUNNING)
    store.update_node_status("a", NodeStatus.SUCCEEDED)
    ready = scheduler.refresh(workflow.id)
    assert [n.id for n in ready] == ["b"]
    assert store.get_node("b").status is NodeStatus.READY


def test_claim_is_race_safe(store, create_workflow):
    create_workflow([{"id": "a"}])
    store.update_node_status("a", NodeStatus.READY)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.claim_node("a"), range(8)))
    assert sum(1 for r in results if r is not None) == 1


def test_waiting_human_node_does_not_block_independent_node(store, create_workflow):
    workflow = create_workflow(
        [{"id": "human"}, {"id": "independent"}, {"id": "downstream", "dependencies": ["human"]}]
    )
    scheduler = DAGScheduler(store)
    scheduler.refresh(workflow.id)
    store.update_node_status("human", NodeStatus.RUNNING)
    store.update_node_status("human", NodeStatus.WAITING_HUMAN)
    ready = scheduler.refresh(workflow.id)
    assert "independent" in [n.id for n in ready]
    assert "downstream" not in [n.id for n in ready]
