"""Route-aware Module 110 regression tests.

These tests keep the two governance layers honest: an unsafe request/action
may be stopped while a safe sibling draft is still verified on its own merits.
"""
from __future__ import annotations

import json
from pathlib import Path

from teow_agl.models import (
    ApprovalRequest, CandidateAction, CandidatePlan, ExecutionResult,
    GovernanceDecision, TaskEnvelope,
)
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.modules.module_school_artifact_guard import (
    normalize_school_markdown_plan,
)


GOAL = (
    "Student Ali was reportedly struck by a car on school grounds. The "
    "student is receiving appropriate care. Prepare an internal incident "
    "report and a private parent notification. Exact time, location, vehicle "
    "details, witnesses, injuries and responsibility are unverified; mark "
    "them TBC. Do not assign blame and do not send anything."
)

INTERNAL = """# Internal Incident Report

> **Status:** DRAFT - NOT SENT
> **Audience:** Internal school review only

## Confirmed or reported information

- Student: Ali
- Reported event: Ali was reportedly struck by a car on school grounds.
- Current care status: The student is receiving appropriate care.

## Information not yet verified

- Exact date and time: TBC
- Exact location within the school grounds: TBC
- Vehicle and driver details: TBC
- Witnesses, injuries and responsibility: TBC

## Proposed next steps

- Verify missing facts through the authorised incident process.
- Record confirmed updates without assigning responsibility.
"""


def _rules() -> dict:
    path = Path(__file__).resolve().parents[1] / "configs" / "verifier_rules.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _case(tmp_path: Path) -> tuple[TaskEnvelope, CandidatePlan]:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    envelope = TaskEnvelope(
        task_id="route_aware_task", session_id="s1", user_id="u1",
        raw_goal=GOAL, normalized_goal=GOAL,
        workspace_roots=[str(tmp_path), str(outputs)],
        metadata={
            "school_semantics_checked": True,
            "school_semantics": {
                "checked": True, "school_domain": True,
                "case_relation": "new_case", "audience": "internal",
            },
        },
    )
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="test", planning_mode="direct",
        actions=[
            CandidateAction(
                action_id="cover", tool="chat", operation="answer",
                purpose="Summarise governed drafts",
                metadata={"body": "The requested drafts are ready for review."},
            ),
            CandidateAction(
                action_id="safe_report", tool="fs",
                operation="save_under_outputs",
                target=str(outputs / "internal_incident_report.md"),
                purpose="Prepare only the internal incident report",
                metadata={"title": "Internal Incident Report"},
            ),
            CandidateAction(
                action_id="unsafe_notice", tool="fs",
                operation="save_under_outputs",
                target=str(outputs / "parent_notification_draft.md"),
                purpose="Prepare only the private parent notification",
                metadata={"title": "Private Parent Notification"},
            ),
        ],
    )
    normalize_school_markdown_plan(plan, envelope)
    return envelope, plan


def _decision(action_id: str, route: str, *, approved: bool = False) -> GovernanceDecision:
    approval = None
    if route == "GREEN":
        approval = ApprovalRequest(
            task_id="route_aware_task", action_id=action_id,
            summary="approve external action",
            status="approved" if approved else "pending",
        )
    return GovernanceDecision(
        task_id="route_aware_task", action_id=action_id, route=route,
        reasons=["test"], ticket_required=route in {"BLUE", "GREEN"},
        approval_required=route == "GREEN", approval_request=approval,
    )


def _success(action: CandidateAction, resources: list[str] | None = None) -> ExecutionResult:
    return ExecutionResult(
        task_id="route_aware_task", action_id=action.action_id,
        ticket_id="ticket", status="success",
        output_summary=str((action.metadata or {}).get("body") or "written"),
        affected_resources=resources or [],
    )


def test_blocked_school_artifact_is_not_a_missing_artifact_failure(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")
    executions = [
        _success(by_id["cover"]),
        _success(safe, [safe.target]),
    ]
    decisions = [
        _decision("cover", "BLUE"),
        _decision("safe_report", "BLUE"),
        _decision("unsafe_notice", "RED"),
    ]

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions, executions=executions,
        governance_decisions=decisions, final_route="RED",
    )

    assert result["pass"] is True, result
    assert result["verification_status"] == "verified_partial"
    assert result["scope"]["blocked_action_ids"] == ["unsafe_notice"]
    completeness = next(
        c for c in result["checks"]
        if c["name"] == "school.execution_completeness"
    )
    assert completeness["pass"] is True
    assert completeness["details"]["expected"] == 1


def test_pure_red_with_only_chat_companion_is_verified_safe_stop(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    by_id = {a.action_id: a for a in plan.actions}
    decisions = [
        _decision("cover", "BLUE"),
        _decision("safe_report", "RED"),
        _decision("unsafe_notice", "INFEASIBLE"),
    ]
    executions = [
        _success(by_id["cover"]),
        ExecutionResult(
            task_id=envelope.task_id, action_id="verifier_synthetic",
            ticket_id="", status="failed", error="old verifier failure",
        ),
    ]

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions, executions=executions,
        governance_decisions=decisions, final_route="RED",
    )

    assert result["pass"] is True
    assert result["verification_status"] == "verified_safe_stop"
    assert result["checks"] == []
    assert result["scope"]["verified_action_ids"] == ["cover"]


def test_blue_school_artifact_is_still_required_and_fails_when_missing(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    by_id = {a.action_id: a for a in plan.actions}
    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[_success(by_id["cover"])],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "BLUE"),
            _decision("unsafe_notice", "RED"),
        ],
        final_route="RED",
    )

    assert result["pass"] is False
    assert result["verification_status"] == "failed"
    completeness = next(
        c for c in result["checks"]
        if c["name"] == "school.execution_completeness"
    )
    assert completeness["pass"] is False
    assert "not_successful:safe_report" in completeness["details"]["errors"]
    assert not any(
        "unsafe_notice" in error
        for error in completeness["details"]["errors"]
    )


def test_green_is_ignored_until_approved_then_becomes_required(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    # Keep just the cover and one governed artifact to isolate GREEN state.
    plan.actions = [
        action for action in plan.actions
        if action.action_id in {"cover", "safe_report"}
    ]
    by_id = {a.action_id: a for a in plan.actions}
    verifier = VerifierModule(rules=_rules())

    pending = verifier.verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[_success(by_id["cover"])],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "GREEN", approved=False),
        ],
        final_route="GREEN",
    )
    assert pending["pass"] is True
    assert pending["verification_status"] == "awaiting_approval"
    assert pending["scope"]["pending_green_action_ids"] == ["safe_report"]

    approved_but_failed = verifier.verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[_success(by_id["cover"])],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "GREEN", approved=True),
        ],
        final_route="GREEN",
    )
    assert approved_but_failed["pass"] is False
    assert approved_but_failed["verification_status"] == "failed"


def test_successful_green_execution_does_not_manufacture_approval(
    tmp_path: Path,
) -> None:
    envelope, plan = _case(tmp_path)
    plan.actions = [
        action for action in plan.actions
        if action.action_id in {"cover", "safe_report"}
    ]
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[
            _success(by_id["cover"]),
            _success(safe, [safe.target]),
        ],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "GREEN", approved=False),
        ],
        final_route="GREEN",
    )

    assert result["pass"] is False
    assert result["verification_status"] == "failed"
    assert result["audit_failure"] is True
    assert result["scope"]["pending_green_action_ids"] == ["safe_report"]
    assert result["scope"]["execution_before_approval_action_ids"] == [
        "safe_report"
    ]
    authority = next(
        check for check in result["checks"]
        if check["name"] == "governance.execution_authority"
    )
    assert authority["pass"] is False
    assert "execution_before_approval:safe_report" in authority["reason"]


def test_successful_execution_of_blocked_action_is_audit_failure(
    tmp_path: Path,
) -> None:
    envelope, plan = _case(tmp_path)
    plan.actions = [
        action for action in plan.actions
        if action.action_id in {"cover", "safe_report"}
    ]
    by_id = {action.action_id: action for action in plan.actions}
    blocked = by_id["safe_report"]
    blocked.metadata["content"] = INTERNAL
    Path(blocked.target).write_text(INTERNAL, encoding="utf-8")

    for route in ("RED", "INFEASIBLE"):
        result = VerifierModule(rules=_rules()).verify(
            envelope=envelope,
            plan_actions=plan.actions,
            executions=[
                _success(by_id["cover"]),
                _success(blocked, [blocked.target]),
            ],
            governance_decisions=[
                _decision("cover", "BLUE"),
                _decision("safe_report", route),
            ],
            final_route=route,
        )

        assert result["pass"] is False
        assert result["verification_status"] == "failed"
        assert result["audit_failure"] is True
        assert result["scope"]["execution_of_blocked_action_ids"] == [
            "safe_report"
        ]
        authority = next(
            check for check in result["checks"]
            if check["name"] == "governance.execution_authority"
        )
        assert (
            "execution_of_blocked_action:safe_report" in authority["reason"]
        )


def test_approved_green_success_is_eligible_for_verification(
    tmp_path: Path,
) -> None:
    envelope, plan = _case(tmp_path)
    plan.actions = [
        action for action in plan.actions
        if action.action_id in {"cover", "safe_report"}
    ]
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[
            _success(by_id["cover"]),
            _success(safe, [safe.target]),
        ],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "GREEN", approved=True),
        ],
        final_route="GREEN",
    )

    assert result["pass"] is True, result
    assert result["verification_status"] == "verified"
    assert result["scope"]["pending_green_action_ids"] == []
    assert result["scope"]["execution_before_approval_action_ids"] == []


def test_missing_substantive_governance_decision_is_audit_failure(
    tmp_path: Path,
) -> None:
    envelope, plan = _case(tmp_path)
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[_success(by_id["cover"]), _success(safe, [safe.target])],
        # `unsafe_notice` is intentionally omitted: route-aware verification
        # must expose the missing governance record, not silently exclude it.
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "BLUE"),
        ],
        final_route="BLUE",
    )

    assert result["pass"] is False
    assert result["verification_status"] == "failed"
    assert result["audit_failure"] is True
    assert result["scope"]["missing_governance_action_ids"] == [
        "unsafe_notice"
    ]
    authority = next(
        check for check in result["checks"]
        if check["name"] == "governance.execution_authority"
    )
    assert "missing_governance_decision:unsafe_notice" in authority["reason"]


def test_legacy_scope_without_decision_list_remains_compatible(
    tmp_path: Path,
) -> None:
    envelope, plan = _case(tmp_path)
    plan.actions = [
        action for action in plan.actions
        if action.action_id in {"cover", "safe_report"}
    ]
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[
            _success(by_id["cover"]),
            _success(safe, [safe.target]),
        ],
        governance_decisions=None,
        final_route="BLUE",
    )

    assert result["pass"] is True, result
    assert result["scope"]["missing_governance_action_ids"] == []
    assert result["scope"]["execution_before_approval_action_ids"] == []
    assert "audit_failure" not in result


def test_input_governance_boundary_marks_safe_outputs_partial(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    envelope.metadata["school_response_pack"] = {
        "input_governance": {
            "decision": "RED",
            "blocked_request": "broadcast individual student details",
        }
    }
    # Isolate one safe artifact; no action itself is RED.
    plan.actions = [
        action for action in plan.actions
        if action.action_id in {"cover", "safe_report"}
    ]
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")

    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[_success(by_id["cover"]), _success(safe, [safe.target])],
        governance_decisions=[
            _decision("cover", "BLUE"), _decision("safe_report", "BLUE"),
        ],
        final_route="BLUE",
    )
    assert result["pass"] is True, result
    assert result["verification_status"] == "verified_partial"


class _JudgeLLM:
    backend = "test"

    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_prompt = ""

    def chat_json(self, *, system: str, user: str, max_tokens: int) -> dict:
        self.system_prompt = system
        self.user_prompt = user
        return {
            "score": 95, "issues": [], "suggestions": [],
            "artifact_results": [
                {"action_id": "safe_report", "pass": True, "issues": []},
            ],
        }


def test_llm_judge_scores_safe_sibling_on_mixed_red_route(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    envelope.metadata["school_response_pack"] = {
        "input_governance": {
            "decision": "RED",
            "blocked_request": "broadcast student-sensitive details",
            "safe_transformations": ["prepare a privacy-safe private draft"],
        }
    }
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    blocked = by_id["unsafe_notice"]
    safe.metadata["content"] = INTERNAL
    blocked.metadata["content"] = "BLOCKED MATERIAL MUST NOT REACH THE JUDGE"
    llm = _JudgeLLM()
    judge = VerifierModule(
        rules={"llm_judge": {"enabled": True, "judge_exempt_routes": ["RED"]}},
        chat_llm=llm,
    ).llm_judge(
        envelope=envelope,
        plan_actions=plan.actions,
        executions=[
            _success(by_id["cover"]), _success(safe, [safe.target]),
            # Even a stale/inconsistent success record cannot re-authorise a
            # RED action for judging.
            _success(blocked, [blocked.target]),
        ],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "BLUE"),
            _decision("unsafe_notice", "RED"),
        ],
        final_route="RED",
        task_category="report_generation",
    )

    assert judge["pass"] is True
    assert "skipped_reason" not in judge
    assert "Internal Incident Report" in llm.user_prompt
    assert "BLOCKED MATERIAL" not in llm.user_prompt
    assert "AUTHORITATIVE GOVERNED OBJECTIVE" in llm.user_prompt
    assert "Use TBC for facts the user did not supply" in llm.user_prompt
    assert "Clearly labelled recommendations and proposals" in llm.system_prompt
    assert "role-consistent wording" in llm.system_prompt
    assert "past, current or completed-action assertion" in llm.system_prompt


def test_llm_judge_skips_pure_governed_safe_stop(tmp_path: Path) -> None:
    envelope, plan = _case(tmp_path)
    by_id = {a.action_id: a for a in plan.actions}
    llm = _JudgeLLM()
    judge = VerifierModule(
        rules={"llm_judge": {"enabled": True, "judge_exempt_routes": ["RED"]}},
        chat_llm=llm,
    ).llm_judge(
        envelope=envelope,
        plan_actions=plan.actions,
        executions=[_success(by_id["cover"])],
        governance_decisions=[
            _decision("cover", "BLUE"),
            _decision("safe_report", "RED"),
            _decision("unsafe_notice", "RED"),
        ],
        final_route="RED",
        task_category="report_generation",
    )

    assert judge["pass"] is None
    assert judge["skipped_reason"] == "route_exempt:RED"
    assert llm.user_prompt == ""


def test_generation_failure_is_not_misreported_as_governed_partial(
    tmp_path: Path,
) -> None:
    envelope, plan = _case(tmp_path)
    by_id = {a.action_id: a for a in plan.actions}
    safe = by_id["safe_report"]
    safe.metadata["content"] = INTERNAL
    Path(safe.target).write_text(INTERNAL, encoding="utf-8")
    failed = GovernanceDecision(
        task_id=envelope.task_id, action_id="unsafe_notice",
        route="INFEASIBLE",
        reasons=["school_artifact_generation_not_verified"],
        ticket_required=False, approval_required=False,
    )
    result = VerifierModule(rules=_rules()).verify(
        envelope=envelope, plan_actions=plan.actions,
        executions=[_success(by_id["cover"]), _success(safe, [safe.target])],
        governance_decisions=[
            _decision("cover", "BLUE"), _decision("safe_report", "BLUE"),
            failed,
        ],
        final_route="INFEASIBLE",
    )
    assert result["pass"] is False
    assert result["verification_status"] == "failed"


def test_broken_external_gate_link_is_a_contract_failure() -> None:
    envelope = TaskEnvelope(
        task_id="broken_gate", session_id="s1", user_id="u1",
        raw_goal="Submit the report.", normalized_goal="Submit the report.",
        metadata={"school_response_pack": {"deliverables": [{
            "deliverable_id": "external_release_authority",
            "artifact_role": "external_release_gate",
            "kind": "external_action",
            "selected": True,
            "requirement": "explicit_user_request",
        }]}},
    )
    gate = CandidateAction(
        action_id="release_gate", tool="chat", operation="answer",
        purpose="Request approval before release",
        metadata={
            "deliverable_id": "external_release_authority",
            "artifact_role": "external_release_gate",
        },
    )
    decision = GovernanceDecision(
        task_id=envelope.task_id, action_id=gate.action_id,
        route="INFEASIBLE",
        reasons=[
            "linked_artifact_not_available",
            "required_deliverable:education_authority_request",
        ],
        ticket_required=False, approval_required=False,
    )
    coverage = VerifierModule._response_pack_coverage(
        envelope=envelope,
        plan_actions=[gate],
        executions=[],
        governance_decisions=[decision],
    )
    assert coverage["complete"] is False
    assert coverage["status_counts"] == {"FAILED": 1}
    assert coverage["items"][0]["detail"] == "technical_contract_failure"
