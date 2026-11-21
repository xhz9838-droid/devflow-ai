from devflow.planning.policy import DecisionPolicy, classify_change_risk
from devflow.planning.models import ChangeRisk


def test_clear_and_ordinary_work_is_auto_decided():
    policy = DecisionPolicy()
    result = policy.evaluate(
        [
            {"question": "Use snake_case?", "level": "L1", "reason": "style"},
            {"question": "Split module?", "level": "L2", "recommended": "keep"},
            {"question": "Follow the README?", "level": "L0"},
        ],
        objective="add a function",
    )
    assert result.clarifications == []
    assert len(result.decisions) == 3


def test_valid_l3_question_is_accepted():
    result = DecisionPolicy().evaluate(
        [
            {
                "question": "Keep the old API compatible?",
                "level": "L3",
                "reason": "two callers exist",
                "research": "found legacy callers in src/legacy.py",
                "impact": "breaking them affects production callers",
                "options": [
                    {"id": "compatible", "label": "Keep compatible"},
                    {"id": "break", "label": "Break old API"},
                ],
                "recommended": "compatible",
            }
        ],
        objective="change the API",
    )
    assert len(result.clarifications) == 1
    assert result.clarifications[0].recommended == "compatible"


def test_l3_without_research_is_not_asked():
    result = DecisionPolicy().evaluate(
        [
            {
                "question": "Which database?",
                "level": "L3",
                "reason": "unclear",
                "impact": "schema choice",
                "options": [{"id": "a"}, {"id": "b"}],
                "recommended": "a",
            }
        ],
        objective="add storage",
    )
    assert result.clarifications == []
    assert result.suppressed[0]["reason"].startswith("research-first")


def test_l3_without_impact_is_not_asked():
    result = DecisionPolicy().evaluate(
        [
            {
                "question": "Choose an approach",
                "level": "L3",
                "research": "checked docs",
                "options": [{"id": "a"}, {"id": "b"}],
                "recommended": "a",
            }
        ],
        objective="do work",
    )
    assert result.clarifications == []


def test_single_option_is_auto_decided():
    result = DecisionPolicy().evaluate(
        [
            {
                "question": "Use the only option?",
                "level": "L3",
                "research": "checked",
                "impact": "low",
                "options": [{"id": "only"}],
                "recommended": "only",
            }
        ],
        objective="do work",
    )
    assert result.clarifications == []
    assert result.decisions


def test_invalid_recommended_option_is_suppressed():
    result = DecisionPolicy().evaluate(
        [
            {
                "question": "Pick one",
                "level": "L3",
                "research": "checked",
                "impact": "some impact",
                "options": [{"id": "a"}, {"id": "b"}],
                "recommended": "c",
            }
        ],
        objective="do work",
    )
    assert result.clarifications == []


def test_duplicates_and_resolved_questions_are_dropped():
    candidate = {
        "question": "Keep compatibility?",
        "level": "L3",
        "research": "evidence",
        "impact": "compatibility risk",
        "options": [{"id": "a"}, {"id": "b"}],
        "recommended": "a",
    }
    result = DecisionPolicy().evaluate(
        [candidate, dict(candidate), dict(candidate, question="keep  compatibility?")],
        objective="work",
        resolved_questions={"keep compatibility?"},
    )
    assert result.clarifications == []


def test_batch_limit_is_enforced():
    candidates = [
        {
            "question": f"Question {index}?",
            "level": "L3",
            "research": "evidence",
            "impact": "impact",
            "options": [{"id": "a"}, {"id": "b"}],
            "recommended": "a",
        }
        for index in range(8)
    ]
    result = DecisionPolicy(max_questions=3).evaluate(candidates, objective="work")
    assert len(result.clarifications) == 3
    assert len(result.suppressed) == 5


def test_authorization_demotes_non_risky_l3_but_not_risky_l3():
    result = DecisionPolicy().evaluate(
        [
            {
                "question": "Choose a helper name?",
                "level": "L3",
                "research": "checked",
                "impact": "style only",
                "options": [{"id": "a"}, {"id": "b"}],
                "recommended": "a",
            },
            {
                "question": "Drop the old database columns?",
                "level": "L3",
                "research": "checked",
                "impact": "irreversible data loss",
                "options": [{"id": "drop"}, {"id": "keep"}],
                "recommended": "keep",
            },
        ],
        objective="work",
        authorizations=["自行决定普通技术细节"],
    )
    assert len(result.clarifications) == 1
    assert result.clarifications[0].question.startswith("Drop")


def test_change_risk_classification():
    assert (
        classify_change_risk(
            declared="MAJOR",
            title="x",
            reason="",
            impact="",
            proposed_changes={},
            completed_node_ids=set(),
        )
        is ChangeRisk.MAJOR
    )
    assert (
        classify_change_risk(
            declared=None,
            title="Add docs",
            reason="small",
            impact="none",
            proposed_changes={"add_nodes": []},
            completed_node_ids=set(),
        )
        is ChangeRisk.LOW
    )
    assert (
        classify_change_risk(
            declared=None,
            title="Database migration",
            reason="",
            impact="",
            proposed_changes={},
            completed_node_ids=set(),
        )
        is ChangeRisk.MAJOR
    )
    assert (
        classify_change_risk(
            declared=None,
            title="Rework",
            reason="",
            impact="",
            proposed_changes={"affected_nodes": ["done"]},
            completed_node_ids={"done"},
        )
        is ChangeRisk.MAJOR
    )
    assert (
        classify_change_risk(
            declared=None,
            title="Re-add a cancelled node",
            reason="small",
            impact="low",
            proposed_changes={"add_nodes": [{"id": "docs"}]},
            completed_node_ids=set(),
            terminal_node_ids={"docs"},
        )
        is ChangeRisk.MAJOR
    )
