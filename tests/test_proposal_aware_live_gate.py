"""Offline contracts for the honest proposal-aware live acceptance gate.

These tests never create a FastAPI task and never call a model provider.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.evaluate_proposal_aware_live import (
    CASE_BY_LABEL,
    CASES,
    _parse_answers,
    classify_artifact_provenance,
    evaluate_state_pair,
)


def _deliverable(role: str, requirement: str, selected: bool) -> dict:
    return {
        "deliverable_id": role,
        "artifact_role": role,
        "filename": f"{role}.md",
        "kind": "artifact",
        "requirement": requirement,
        "required": requirement == "required",
        "selected": selected,
    }


def _passing_state(label: str) -> dict:
    spec = CASE_BY_LABEL[label]
    rows = [
        _deliverable(role, "required", True)
        for role in sorted(spec.required_roles)
    ]
    rows.extend(
        _deliverable(
            role,
            "recommended",
            role in spec.selected_recommended_roles,
        )
        for role in sorted(spec.recommended_roles)
    )
    executions = [
        {
            "status": "success",
            "affected_resources": [f"C:/lab/outputs/{row['filename']}"],
            # A blank submode is the API's honest serialization for a clean,
            # retained live artifact with no fallback explanation.
            "school_generation_submode": "",
        }
        for row in rows if row["selected"] is True
    ]
    return {
        "task_id": f"task_{label}",
        "parent_task_id": None,
        "status": "done",
        "error": None,
        "final_route": "BLUE",
        "planner_mode": "live",
        "live_provider": "openai",
        "live_model": "gpt-test",
        "generation_mode": "live_api_verified",
        "verification": {"pass": True},
        "school_semantics": {"case_relation": "new_case"},
        "response_pack": {
            "input_governance": {"decision": "NO_OVERRIDE"},
            "critical_question": None,
            "deliverables": rows,
        },
        "executions": executions,
    }


def test_all_case_specs_are_internally_consistent() -> None:
    assert len(CASES) == 10
    assert len(CASE_BY_LABEL) == len(CASES)
    for spec in CASES:
        assert spec.selected_recommended_roles <= spec.recommended_roles
        assert not (
            spec.expected_selected_roles & spec.forbidden_selected_roles
        )
        assert spec.recommended_roles <= spec.permitted_recommended_roles


@pytest.mark.parametrize(
    ("task_mode", "status", "submode", "expected"),
    [
        ("live_api_verified", "success", "", "live"),
        (
            "hybrid_live_with_deterministic_repair", "success",
            "deterministic_role_fallback_after_live_repair", "deterministic",
        ),
        (
            "live_api_verified", "success", "per_action_scoped_fallback",
            "live",
        ),
        (
            "failed_closed", "success", "per_action_scoped_fallback",
            "failed",
        ),
        ("live_api_verified", "failed", "", "failed"),
        ("not_applicable", "success", "", "unknown"),
    ],
)
def test_provenance_classification_never_overclaims(
    task_mode: str, status: str, submode: str, expected: str,
) -> None:
    assert classify_artifact_provenance(
        task_generation_mode=task_mode,
        execution_status=status,
        submode=submode,
    ) == expected


def test_clean_live_state_separates_all_three_passes() -> None:
    spec = CASE_BY_LABEL["safe_bus_breakdown"]
    state = _passing_state(spec.label)

    row = evaluate_state_pair(state, state, spec)

    assert row["pack_pass"] is True
    assert row["safety_pass"] is True
    assert row["provenance_pass"] is True
    assert row["live_retained"] is True
    assert row["contract_pass"] is True


def test_wrong_selected_pack_fails_even_when_verification_passed() -> None:
    spec = CASE_BY_LABEL["staff_conflict_resolved"]
    state = _passing_state(spec.label)
    state["response_pack"]["deliverables"].append(
        _deliverable("emergency_contact_script", "recommended", True)
    )
    state["executions"].append({
        "status": "success",
        "affected_resources": [
            "C:/lab/outputs/emergency_contact_script.md",
        ],
        "school_generation_submode": "",
    })

    row = evaluate_state_pair(state, state, spec)

    assert state["verification"]["pass"] is True
    assert row["pack_pass"] is False
    assert row["contract_pass"] is False
    assert any("forbidden_selected" in issue for issue in row["pack_issues"])


def test_missing_written_file_fails_provenance_not_pack_or_safety() -> None:
    spec = CASE_BY_LABEL["safe_bus_breakdown"]
    state = _passing_state(spec.label)
    state["executions"].pop()

    row = evaluate_state_pair(state, state, spec)

    assert row["pack_pass"] is True
    assert row["safety_pass"] is True
    assert row["provenance_pass"] is False
    assert any(
        issue.startswith("selected_files_not_written")
        for issue in row["provenance_issues"]
    )


def test_unexpected_clarification_is_not_auto_answered_or_hidden() -> None:
    spec = CASE_BY_LABEL["canteen_illness_stable"]
    state = _passing_state(spec.label)
    state["status"] = "awaiting_clarification"
    state["final_route"] = ""
    state["verification"] = None
    state["generation_mode"] = "not_applicable"
    state["executions"] = []
    state["response_pack"]["critical_question"] = {
        "question_id": "immediate_danger",
    }

    row = evaluate_state_pair(state, state, spec)

    assert row["clarification_pass"] is False
    assert row["safety_pass"] is False
    assert row["expected_pause"] is False


def test_safeguarding_expected_human_pause_is_an_honest_contract_pass() -> None:
    spec = CASE_BY_LABEL["confidential_safeguarding"]
    state = _passing_state(spec.label)
    state["status"] = "awaiting_clarification"
    state["final_route"] = ""
    state["verification"] = None
    state["generation_mode"] = "not_applicable"
    state["executions"] = []
    state["response_pack"]["critical_question"] = {
        "question_id": "immediate_danger",
    }

    row = evaluate_state_pair(state, state, spec)

    assert row["clarification_pass"] is True
    assert row["safety_pass"] is True
    assert row["pack_pass"] is True
    assert row["live_retained"] is None
    assert row["expected_pause"] is True
    assert row["contract_pass"] is True


def test_safeguarding_answer_must_be_explicit_and_lineaged() -> None:
    spec = CASE_BY_LABEL["confidential_safeguarding"]
    initial = _passing_state(spec.label)
    initial["status"] = "awaiting_clarification"
    initial["final_route"] = ""
    initial["verification"] = None
    initial["generation_mode"] = "not_applicable"
    initial["executions"] = []
    initial["response_pack"]["critical_question"] = {
        "question_id": "immediate_danger",
    }
    final = _passing_state(spec.label)
    final["task_id"] = "task_safeguarding_continuation"
    final["parent_task_id"] = initial["task_id"]
    final["school_semantics"]["case_relation"] = "follow_up"

    row = evaluate_state_pair(
        initial, final, spec, explicit_answer="No",
    )

    assert row["clarification_pass"] is True
    assert row["case_relation_pass"] is True
    assert row["contract_pass"] is True


def test_answer_parser_rejects_implicit_or_unsupported_branches() -> None:
    assert _parse_answers(["confidential_safeguarding=No"]) == {
        "confidential_safeguarding": "No",
    }
    with pytest.raises(SystemExit):
        _parse_answers(["confidential_safeguarding=Yes"])
    with pytest.raises(SystemExit):
        _parse_answers(["safe_bus_breakdown=No"])
