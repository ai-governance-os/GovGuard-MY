"""Focused, offline verification for the MAIC Mixed-Live hardening.

Kept outside pytest as a focused competition-hardening smoke check. Run from
the project root:

    python scripts/verify_competition_enhancements.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teow_agl.models import CandidateAction, ActionRiskAssessment, PreGovernanceAssessment
from teow_agl.modules.module_101d_data_use_guard import DataUseGuard
from teow_agl.modules.module_103_governance import GovernanceModule
from teow_agl.modules.module_school_input_semantics import SchoolInputSemantics
from teow_agl.policies.governance_profile import ProfileView


class _SemanticLLM:
    backend = "openai"

    def chat_json(self, system: str, user: str, *, max_tokens: int = 450) -> dict:
        raise AssertionError("OpenAI semantic intake must use native JSON mode")

    def chat_json_openai(
        self, system: str, user: str, *, max_tokens: int = 450,
    ) -> dict:
        assert "MUST NOT output BLUE" in system
        assert "Active workflow" in user
        return {
            "school_domain": True,
            "case_relation": "follow_up",
            "school_area": "student_support",
            "requested_action": "advise",
            "audience": "internal",
            "confidence": 0.94,
            "rationale": "Continuation of the pupil speech-support case.",
            "student_sensitive_data": True,
            "public_disclosure": False,
            "persistent_sensitive_learning": False,
        }


def _action(*concepts: str, operation: str = "draft") -> CandidateAction:
    return CandidateAction(
        action_id="verify_action",
        tool="chat",
        operation=operation,
        purpose="Handle the school request",
        expected_effect="A governed result",
        metadata={"data_use_concepts": list(concepts), "task_id": "verify_task"},
    )


def verify_semantic_intake() -> None:
    sem = SchoolInputSemantics(_SemanticLLM()).classify(
        "The pupils still cannot deliver the speech. What should we change?",
        active_workflow_id="ad_hoc_school_event_reporting",
    )
    assert sem["school_domain"] is True
    assert sem["case_relation"] == "follow_up"
    assert sem["school_area"] == "student_support"
    assert sem["data_use_concepts"] == ["student_sensitive_data"]
    unrelated = SchoolInputSemantics._normalise({
        "school_domain": False,
        "case_relation": "new_case",
        "school_area": "public_communication",
        "external_release": True,
    })
    assert unrelated["case_relation"] == "unrelated"
    assert unrelated["school_area"] == "other"
    assert unrelated["data_use_concepts"] == []


def verify_deterministic_concept_policy() -> None:
    guard = DataUseGuard()
    assert guard.assess(_action(
        "student_sensitive_data", "public_disclosure", operation="publish"
    ))["decision"] == "RED"
    assert guard.assess(_action(
        "persistent_sensitive_learning"
    ))["decision"] == "RED"
    assert guard.assess(_action(
        "financial_value_change"
    ))["decision"] == "GREEN"
    assert guard.assess(_action(
        "official_record_change"
    ))["decision"] == "GREEN"
    assert guard.assess(_action(
        "unsupported_fact_invention"
    ))["decision"] == "INFEASIBLE"
    # A private/internal support discussion is not blocked merely because it
    # contains a pupil learning need.
    assert guard.assess(_action(
        "student_sensitive_data"
    ))["decision"] == "NO_OVERRIDE"


def verify_learning_cannot_remove_mandatory_gate() -> None:
    profile_data = json.loads(
        (ROOT / "configs" / "default_user_governance_profile.json").read_text(
            encoding="utf-8"
        )
    )
    taxonomy = json.loads(
        (ROOT / "configs" / "action_taxonomy.json").read_text(encoding="utf-8")
    )
    signature = "competition-floor-check"
    gov = GovernanceModule(
        profile=ProfileView(profile_data),
        hard_safety_cfg={},
        learned_policy={"route_weight_adjustments": {signature: -5.0}},
        action_taxonomy=taxonomy,
        policy_version="verification",
    )
    action = CandidateAction(
        action_id="publish_action",
        tool="publish",
        operation="publish",
        target="school_facebook",
        purpose="Publish an approved school announcement",
        expected_effect="External release",
        metadata={"task_id": "verify_task"},
    )
    pre = PreGovernanceAssessment(
        task_id="verify_task", task_category="communication",
        planning_mode="approval_first",
    )
    risk = ActionRiskAssessment(
        task_id="verify_task", action_id=action.action_id,
        risk_score=50, risk_level="high", recommended_route="GREEN",
        features={"external_facing": True},
    )
    decision = gov.decide(
        pre=pre, action=action, risk=risk, signature=signature
    )
    assert decision.route == "GREEN"
    assert any("policy_floor_enforced:BLUE->GREEN" in r for r in decision.reasons)


def verify_mixed_live_selection() -> None:
    import server.app as appmod

    names = (
        "OPENAI_API_KEY", "TEOW_AGL_LIVE_WORKFLOWS",
        "TEOW_AGL_LIVE_SCHOOL_INPUTS", "TEOW_AGL_PLANNER",
    )
    old = {name: os.environ.get(name) for name in names}
    try:
        os.environ["OPENAI_API_KEY"] = "verification-placeholder-not-a-real-key"
        os.environ["TEOW_AGL_LIVE_WORKFLOWS"] = "ad_hoc_school_event_reporting"
        os.environ["TEOW_AGL_LIVE_SCHOOL_INPUTS"] = "1"
        os.environ["TEOW_AGL_PLANNER"] = "smart_mock"
        # Scripted buttons carry an explicit workflow id. Free text that merely
        # resembles Route A/B must not steal that workflow.
        assert appmod._goal_runs_live(
            "Prepare the scripted speech-competition package.",
            scripted_workflow_id="ad_hoc_school_event_reporting",
        ) is True
        assert appmod._goal_runs_live(
            "The pupils still cannot deliver the speech. What should we change?",
            active_workflow_id="ad_hoc_school_event_reporting",
            school_semantics={
                "checked": True,
                "school_domain": True,
                "case_relation": "follow_up",
                "source": "openai",
            },
        ) is True
        assert appmod._goal_runs_live(
            "Draft an investigation report for a student conduct incident.",
            school_semantics={
                "checked": True,
                "school_domain": True,
                "case_relation": "new_case",
                "source": "openai",
            },
        ) is True
        assert appmod._goal_runs_live(
            "Prepare a World Cup report.",
            active_workflow_id="ad_hoc_school_event_reporting",
            school_semantics={
                "checked": True,
                "school_domain": False,
                "case_relation": "unrelated",
                "source": "openai",
            },
        ) is False
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def verify_out_of_domain_boundary() -> None:
    import server.app as appmod

    prompt = "Prepare a report about the FIFA World Cup."
    sem = {
        "checked": True,
        "school_domain": False,
        "case_relation": "unrelated",
        "school_area": "other",
        "data_use_concepts": [],
        "source": "verification_fixture",
    }
    rt, mode = appmod._make_runtime_for_goal(prompt, school_semantics=sem)
    result = rt.run(
        raw_goal=prompt,
        metadata={
            "school_semantics_checked": True,
            "school_semantics": sem,
            "data_use_concepts": [],
            "active_workflow_id": None,
            "school_followup": False,
            "no_index": True,
        },
    )
    assert mode == "deterministic"
    assert result.final_route == "BLUE"
    assert len(result.executions) == 1
    body = result.executions[0].output_summary
    assert "outside the active School Administration Pack" in body
    assert "not used or carried over any student" in body
    assert result.plan is not None
    assert result.plan.planner_id == "school_domain_boundary"


def verify_frontend_continuity_wire() -> None:
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "const activeWorkflowId = state.activeWorkflowId" in source
    assert "active_workflow_id: activeWorkflowId" in source
    assert "applyCaseContinuityFromPoll(task_id, state_d)" in source
    assert "if (d.context_workflow_id) activeWorkflowId = d.context_workflow_id" in source
    assert "base.liveWorkflows.includes(detectedId)" in source
    runtime_source = (ROOT / "teow_agl" / "runtime.py").read_text(encoding="utf-8")
    assert 'not envelope.metadata.get("school_semantics_checked")' in runtime_source
    assert 'leaf_meta[key] = envelope.metadata[key]' in runtime_source
    assert 'not envelope.metadata.get("school_semantics_checked")' in runtime_source


def main() -> None:
    checks = (
        ("semantic intake", verify_semantic_intake),
        ("deterministic concept policy", verify_deterministic_concept_policy),
        ("mandatory policy floor", verify_learning_cannot_remove_mandatory_gate),
        ("Mixed-Live selection", verify_mixed_live_selection),
        ("out-of-domain boundary", verify_out_of_domain_boundary),
        ("frontend continuity wire", verify_frontend_continuity_wire),
    )
    for label, check in checks:
        check()
        print(f"PASS  {label}")
    print(f"PASS  all {len(checks)} competition enhancement checks")


if __name__ == "__main__":
    main()
