import sqlite3

import pytest

from devflow.errors import ValidationError
from devflow.planning.models import ClarificationStatus, PlanStatus
from devflow.planning.planner import PlannerError
from devflow.planning.validator import PlanValidationError
from planning_helpers import valid_plan, make_manager, plan_node


def l3_candidate():
    return {
        "question": "Keep backward compatibility?",
        "level": "L3",
        "reason": "legacy callers",
        "research": "found legacy.py",
        "impact": "compatibility risk",
        "options": [{"id": "keep"}, {"id": "break"}],
        "recommended": "keep",
    }


def test_clear_plan_reaches_plan_ready_without_questions(tmp_path):
    manager, store, planner = make_manager(tmp_path, [valid_plan()])
    plan = manager.create("implement a feature")
    generated = manager.generate(plan.id)
    assert generated.status is PlanStatus.PLAN_READY
    assert store.list_clarifications(plan.id) == []
    assert generated.plan_json["title"] == "Test plan"


def test_l3_question_parks_plan_and_answer_refines(tmp_path):
    with_question = valid_plan()
    with_question["clarification_candidates"] = [l3_candidate()]
    refined = valid_plan()
    manager, store, planner = make_manager(tmp_path, [with_question, refined])
    plan = manager.create("change API")
    generated = manager.generate(plan.id)
    assert generated.status is PlanStatus.NEEDS_CLARIFICATION
    clarifications = store.list_clarifications(plan.id, ClarificationStatus.PENDING)
    assert len(clarifications) == 1
    assert clarifications[0].recommended == "keep"

    manager.answer(plan.id, clarifications[0].id, "keep compatibility", refine=True)
    final = store.get_plan(plan.id)
    assert final.status is PlanStatus.PLAN_READY
    assert store.list_clarifications(plan.id, ClarificationStatus.PENDING) == []
    assert planner.calls[-1].resolved_answers[0]["answer"] == "keep compatibility"


def test_l1_l2_candidates_become_decisions_not_questions(tmp_path):
    plan_json = valid_plan()
    plan_json["clarification_candidates"] = [
        {"question": "Use snake_case?", "level": "L1", "reason": "style"},
        {"question": "Split the module?", "level": "L2", "recommended": "keep"},
    ]
    manager, store, _ = make_manager(tmp_path, [plan_json])
    plan = manager.create("implement")
    generated = manager.generate(plan.id)
    assert generated.status is PlanStatus.PLAN_READY
    assert store.list_clarifications(plan.id) == []
    subjects = {decision["subject"] for decision in generated.plan_json["decisions"]}
    assert {"Use snake_case?", "Split the module?"} <= subjects


def test_confirm_creates_immutable_snapshot_and_version(tmp_path):
    manager, store, _ = make_manager(tmp_path, [valid_plan()])
    plan = manager.create("implement")
    manager.generate(plan.id)
    snapshot = manager.confirm(plan.id, confirmed_by="tester")
    assert snapshot.version == 1
    assert snapshot.content_hash
    confirmed = store.get_plan(plan.id)
    assert confirmed.status is PlanStatus.CONFIRMED
    assert confirmed.version == 1
    assert store.list_versions(plan.id)[0].snapshot_id == snapshot.id

    with pytest.raises(sqlite3.IntegrityError):
        with store.db.transaction() as conn:
            conn.execute(
                "UPDATE plan_snapshots SET plan_json = '{}' WHERE id = ?", (snapshot.id,)
            )
    with pytest.raises(sqlite3.IntegrityError):
        with store.db.transaction() as conn:
            conn.execute("DELETE FROM plan_snapshots WHERE id = ?", (snapshot.id,))


def test_pending_clarification_blocks_confirmation(tmp_path):
    plan_json = valid_plan()
    plan_json["clarification_candidates"] = [l3_candidate()]
    manager, store, _ = make_manager(tmp_path, [plan_json])
    plan = manager.create("change API")
    manager.generate(plan.id)
    with pytest.raises(ValidationError):
        manager.confirm(plan.id)


def test_planner_failure_is_retried_then_plan_fails(tmp_path):
    manager, store, planner = make_manager(
        tmp_path, [PlannerError("bad output"), PlannerError("bad output again")]
    )
    plan = manager.create("implement")
    with pytest.raises(PlannerError):
        manager.generate(plan.id)
    assert len(planner.calls) == 2
    failed = store.get_plan(plan.id)
    assert failed.status is PlanStatus.FAILED
    assert "bad output" in failed.error


def test_invalid_plan_is_retried_and_can_recover(tmp_path):
    broken = valid_plan()
    broken["nodes"] = []
    manager, store, planner = make_manager(tmp_path, [broken, valid_plan()])
    plan = manager.create("implement")
    generated = manager.generate(plan.id)
    assert generated.status is PlanStatus.PLAN_READY
    assert len(planner.calls) == 2


def test_missing_top_level_acceptance_is_derived(tmp_path):
    plan_json = valid_plan()
    plan_json["acceptance_criteria"] = []
    manager, store, _ = make_manager(tmp_path, [plan_json])
    plan = manager.create("implement")
    generated = manager.generate(plan.id)
    assert generated.status is PlanStatus.PLAN_READY
    assert generated.plan_json["acceptance_criteria"]
    assert manager.validate(plan.id)["ok"]


def test_history_and_changes_views(tmp_path):
    manager, store, _ = make_manager(tmp_path, [valid_plan()])
    plan = manager.create("implement")
    manager.generate(plan.id)
    manager.confirm(plan.id)
    history = manager.history(plan.id)
    assert len(history["versions"]) == 1
    assert len(history["snapshots"]) == 1
    assert manager.changes(plan.id) == []


def test_malformed_plan_output_marks_failed_not_analyzing(tmp_path):
    manager, store, planner = make_manager(
        tmp_path, [{"nodes": ["garbage"]}, {"nodes": ["garbage"]}]
    )
    plan = manager.create("implement")
    with pytest.raises(PlannerError):
        manager.generate(plan.id)
    assert len(planner.calls) == 2
    assert store.get_plan(plan.id).status is PlanStatus.FAILED


def test_cancelled_question_can_be_asked_again(tmp_path):
    with_question = valid_plan()
    with_question["clarification_candidates"] = [l3_candidate()]
    without_question = valid_plan()
    with_question_again = valid_plan()
    with_question_again["clarification_candidates"] = [l3_candidate()]
    manager, store, _ = make_manager(
        tmp_path, [with_question, without_question, with_question_again]
    )
    plan = manager.create("change API")
    manager.generate(plan.id)
    assert store.list_clarifications(plan.id, ClarificationStatus.PENDING)
    manager.generate(plan.id, refine=True)
    assert store.list_clarifications(plan.id, ClarificationStatus.PENDING) == []
    manager.generate(plan.id, refine=True)
    pending = store.list_clarifications(plan.id, ClarificationStatus.PENDING)
    assert len(pending) == 1
    assert store.get_plan(plan.id).status is PlanStatus.NEEDS_CLARIFICATION


def test_answer_rejects_clarification_from_another_plan(tmp_path):
    first = valid_plan()
    first["clarification_candidates"] = [l3_candidate()]
    second = valid_plan()
    second["clarification_candidates"] = [
        dict(l3_candidate(), question="A different question?")
    ]
    manager, store, _ = make_manager(tmp_path, [first, second])
    plan_a = manager.create("A")
    manager.generate(plan_a.id)
    plan_b = manager.create("B")
    manager.generate(plan_b.id)
    other = store.list_clarifications(plan_b.id, ClarificationStatus.PENDING)[0]
    with pytest.raises(ValidationError):
        manager.answer(plan_a.id, other.id, "answer")
    assert store.get_clarification(other.id).status is ClarificationStatus.PENDING


def test_confirm_uses_current_plan_json_and_revalidates(tmp_path):
    manager, store, _ = make_manager(tmp_path, [valid_plan()])
    plan = manager.create("implement")
    manager.generate(plan.id)
    broken = store.get_plan(plan.id).plan_json
    broken["nodes"] = []
    store.save_plan_json(plan.id, broken)
    with pytest.raises(PlanValidationError):
        manager.confirm(plan.id)
    assert store.list_snapshots(plan.id) == []
    assert store.get_plan(plan.id).status is PlanStatus.PLAN_READY
