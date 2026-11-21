import copy

import pytest

from devflow.planning.validator import (
    PlanValidationError,
    assert_plan_valid,
    plan_content_hash,
    to_workflow_nodes,
    validate_plan,
)
from planning_helpers import plan_node, valid_plan


def test_valid_plan_has_no_errors():
    assert validate_plan(valid_plan()) == []


def test_cycle_is_rejected():
    plan = valid_plan()
    plan["nodes"][1]["dependencies"] = ["implement"]
    plan["nodes"][0]["dependencies"] = ["verify"]
    assert any("cycle" in error for error in validate_plan(plan))


def test_unknown_dependency_is_rejected():
    plan = valid_plan()
    plan["nodes"][1]["dependencies"] = ["missing"]
    assert any("unknown" in error for error in validate_plan(plan))


def test_missing_acceptance_is_rejected():
    plan = valid_plan()
    plan["nodes"][0]["acceptance_criteria"] = []
    assert any("acceptance_criteria" in error for error in validate_plan(plan))


def test_unsupported_condition_is_rejected():
    plan = valid_plan()
    plan["nodes"][0]["acceptance_criteria"] = [{"type": "telepathy"}]
    assert any("unsupported" in error for error in validate_plan(plan))


def test_top_level_criterion_must_be_covered_by_a_node():
    plan = valid_plan()
    plan["acceptance_criteria"] = [{"type": "artifact_exists", "path": "uncovered.txt"}]
    assert any("not verifiable" in error for error in validate_plan(plan))


def test_snapshot_hash_is_stable_and_content_sensitive():
    plan = valid_plan()
    first = plan_content_hash(plan)
    changed_metadata = copy.deepcopy(plan)
    changed_metadata["metadata"]["updated_at"] = "2099-01-01T00:00:00+00:00"
    assert plan_content_hash(changed_metadata) == first
    changed = copy.deepcopy(plan)
    changed["nodes"][0]["description"] = "different"
    assert plan_content_hash(changed) != first


def test_to_workflow_nodes_maps_acceptance_and_identity():
    plan = valid_plan()
    plan["nodes"][0]["acceptance_criteria"] = [
        {"type": "artifact_contains", "path": "artifact.txt", "value": "OK"}
    ]
    plan["acceptance_criteria"] = [
        {"type": "artifact_contains", "path": "artifact.txt", "value": "OK"}
    ]
    nodes = to_workflow_nodes(plan, default_timeout=42)
    assert nodes[0]["execution_config"]["acceptance"] == plan["nodes"][0]["acceptance_criteria"]
    assert nodes[0]["execution_config"]["timeout_seconds"] == 42
    assert nodes[0]["idempotency_key"] == "plan-node:implement"
    assert nodes[1]["dependencies"] == ["implement"]


def test_assert_plan_valid_raises_structured_error():
    plan = valid_plan()
    plan["nodes"] = []
    with pytest.raises(PlanValidationError) as error:
        assert_plan_valid(plan)
    assert error.value.errors


def test_node_inputs_and_outputs_types_are_checked():
    plan = valid_plan()
    plan["nodes"][0]["inputs"] = "not-an-object"
    plan["nodes"][1]["expected_outputs"] = "not-a-list"
    errors = validate_plan(plan)
    assert any("inputs must be an object" in error for error in errors)
    assert any("expected_outputs must be a list" in error for error in errors)
