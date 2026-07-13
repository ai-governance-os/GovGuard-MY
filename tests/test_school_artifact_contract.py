from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.models import (
    ActionRiskAssessment, CandidateAction, CandidatePlan, ExecutionResult,
    GovernanceDecision,
    PreGovernanceAssessment, TaskEnvelope,
)
from teow_agl.modules.module_101d_data_use_guard import DataUseGuard
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer
from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.modules.module_school_artifact_guard import (
    normalize_school_markdown_plan,
    school_artifact_verification_checks,
    validate_school_markdown,
)
from teow_agl.modules.module_school_input_semantics import SchoolInputSemantics
from teow_agl.runtime import Runtime, TaskRunResult
from teow_agl.tools.chat_tool import ChatTool
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool


GOAL = (
    "This is a synthetic school safety case. Student Ali was reportedly "
    "struck by a car on school grounds. The emergency response has already "
    "been handled and the student is receiving appropriate care. Prepare: "
    "1. an internal incident report using only confirmed facts; and 2. a "
    "private notification draft for Ali's parent. The exact time, location, "
    "vehicle details, witnesses, injuries and responsibility have not been "
    "verified. Mark all unknown information as TBC. Do not assign blame. "
    "Do not publish or send anything."
)


INTERNAL = """# Internal Incident Report

> **Status:** DRAFT - NOT SENT  
> **Audience:** Internal school review only

## Confirmed or reported information

- Student: Ali
- Reported event: Ali was reportedly struck by a car on school grounds.
- Current care status: The student is receiving appropriate care.
- Emergency-response status: The emergency response has already been handled; no further response detail was supplied.

## Information not yet verified

- Exact date and time: TBC
- Exact location within the school grounds: TBC
- Vehicle and driver details: TBC
- Witnesses, injuries and responsibility: TBC

## Proposed next steps

- Verify the missing facts through the school's authorised incident process.
- Record only confirmed updates and do not assign responsibility without evidence.
"""


PARENT = """# Private Notification Draft for Ali's Parent

> **Status:** DRAFT - NOT SENT  
> **Audience:** Ali's parent or guardian only

Dear Parent/Guardian,

This private draft concerns a reported incident involving Ali on school grounds. Ali was reportedly struck by a car, and the information supplied states that the emergency response has been handled and that Ali is receiving appropriate care.

The exact time, exact location, vehicle details, witness information, injury details and responsibility remain TBC. The school should provide a confirmed update through an authorised staff member when those facts have been verified. No responsibility is assigned in this draft.

Sincerely,

TBC - authorised school representative
"""


def _envelope(tmp_path: Path) -> TaskEnvelope:
    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)
    return TaskEnvelope(
        session_id="s1", user_id="u1", raw_goal=GOAL,
        normalized_goal=GOAL, workspace_roots=[str(tmp_path), str(outputs)],
        metadata={
            "school_semantics_checked": True,
            "school_semantics": {
                "checked": True, "school_domain": True,
                "case_relation": "new_case", "audience": "internal",
            },
        },
    )


def _raw_plan(envelope: TaskEnvelope) -> CandidatePlan:
    outputs = Path(envelope.workspace_roots[-1])
    return CandidatePlan(
        task_id=envelope.task_id, planner_id="test", planning_mode="direct",
        actions=[
            CandidateAction(
                action_id="chat1", tool="chat", operation="answer",
                purpose="Tell the user the drafts were prepared",
                metadata={"body": "Here are the requested documents."},
            ),
            CandidateAction(
                action_id="internal1", tool="docx", operation="save_under_outputs",
                target=str(outputs / "internal_incident_report.docx"),
                purpose="Prepare only the internal incident report",
                metadata={"title": "Internal Incident Report"},
            ),
            CandidateAction(
                action_id="parent1", tool="docx", operation="save_under_outputs",
                target=str(outputs / "parent_notification_draft.docx"),
                purpose="Prepare only the private parent notification draft",
                metadata={"title": "Private Parent Notification"},
            ),
        ],
    )


class _BundleLLM:
    backend = "fake"

    def chat_json(self, system: str, user: str, *, max_tokens: int = 1500):
        if "fact-grounding auditor" in system:
            return {"pass": True, "unsupported_claims": []}
        if "Module 110" in system:
            return {
                "score": 96, "issues": [], "suggestions": [],
                "reasoning": "Each role-scoped artifact is grounded and separate.",
            }
        return {"artifacts": {"internal1": INTERNAL, "parent1": PARENT}}

    def chat(self, system: str, user: str, *, max_tokens: int = 1500):
        return ""


def _exec(action: CandidateAction, affected: list[str] | None = None) -> ExecutionResult:
    summary = action.metadata.get("body") if action.tool == "chat" else "written"
    return ExecutionResult(
        task_id="task", action_id=action.action_id, ticket_id="ticket",
        status="success", output_summary=str(summary or ""),
        affected_resources=affected or [],
    )


def test_school_plan_normalises_to_md_and_short_cover(tmp_path: Path):
    envelope = _envelope(tmp_path)
    plan = _raw_plan(envelope)

    diag = normalize_school_markdown_plan(plan, envelope)

    assert diag["active"] is True
    assert len([a for a in plan.actions if a.tool == "chat"]) == 1
    files = [a for a in plan.actions if a.tool == "fs"]
    assert len(files) == 2
    assert all(a.operation == "save_under_outputs" for a in files)
    assert {Path(a.target).suffix for a in files} == {".md"}
    assert {a.metadata["artifact_role"] for a in files} == {
        "internal_incident_report", "private_parent_notice",
    }
    cover = plan.actions[0].metadata["body"]
    assert len(cover) < 700
    assert "Internal Incident Report" not in cover
    assert "Dear Parent" not in cover
    assert "Nothing was sent or published" in cover


def test_plan_level_bundle_maps_exact_action_ids_without_crossing(tmp_path: Path):
    envelope = _envelope(tmp_path)
    plan = _raw_plan(envelope)
    normalize_school_markdown_plan(plan, envelope)

    diag = ContentSynthesizer(chat_llm=_BundleLLM()).enrich_school_plan(
        plan.actions, user_intent=GOAL)

    assert diag["result"] == "synthesized_verified_action_bundle"
    internal = next(a for a in plan.actions if a.action_id == "internal1")
    parent = next(a for a in plan.actions if a.action_id == "parent1")
    assert "Dear Parent" not in internal.metadata["content"]
    assert "Internal Incident Report" not in parent.metadata["content"]
    assert internal.metadata["synthesis_skip"] is True
    assert parent.metadata["synthesis_skip"] is True


def test_school_verifier_passes_clean_independent_markdown(tmp_path: Path):
    envelope = _envelope(tmp_path)
    plan = _raw_plan(envelope)
    normalize_school_markdown_plan(plan, envelope)
    contents = {"internal1": INTERNAL, "parent1": PARENT}
    executions: list[ExecutionResult] = []
    for action in plan.actions:
        if action.tool == "chat":
            executions.append(_exec(action))
            continue
        action.metadata["content"] = contents[action.action_id]
        action.metadata["school_generation_failed"] = False
        Path(action.target).write_text(contents[action.action_id], encoding="utf-8")
        executions.append(_exec(action, [action.target]))

    checks = school_artifact_verification_checks(
        envelope, plan.actions, executions)

    assert checks
    assert all(check["pass"] for check in checks), checks


def test_mixed_bundle_and_unsupported_facts_cannot_verify(tmp_path: Path):
    envelope = _envelope(tmp_path)
    plan = _raw_plan(envelope)
    normalize_school_markdown_plan(plan, envelope)
    combined = INTERNAL + "\n\n" + PARENT
    executions: list[ExecutionResult] = []
    for action in plan.actions:
        if action.tool == "chat":
            # Reproduce the original UI failure: full document body in chat.
            action.metadata["body"] = combined
            executions.append(_exec(action))
            continue
        action.metadata["content"] = combined
        Path(action.target).write_text(combined, encoding="utf-8")
        executions.append(_exec(action, [action.target]))

    checks = school_artifact_verification_checks(
        envelope, plan.actions, executions)
    failed = {c["name"] for c in checks if not c["pass"]}

    assert "school.chat_companion_scope" in failed
    assert "school.markdown_hygiene" in failed
    assert "school.role_isolation" in failed
    assert "school.cross_artifact_similarity" in failed

    internal = next(a for a in plan.actions if a.action_id == "internal1")
    invented = INTERNAL.replace(
        "## Proposed next steps",
        "Emergency services were contacted and arrived on scene. "
        "Ali was transported to a hospital.\n\n## Proposed next steps",
    )
    issues = validate_school_markdown(internal, invented, GOAL)
    assert "emergency_services_contacted_or_arrived" in issues["grounding"]
    assert "student_transported_or_admitted" in issues["grounding"]
    gathering = INTERNAL.replace(
        "## Proposed next steps",
        "We are working diligently to gather all necessary information.\n\n"
        "## Proposed next steps",
    )
    issues = validate_school_markdown(internal, gathering, GOAL)
    assert "information_gathering_already_underway" in issues["grounding"]
    actively_gathering = PARENT.replace(
        "The exact time", "We are actively working to gather all necessary "
        "information.\n\nThe exact time",
    )
    parent = next(a for a in plan.actions if a.action_id == "parent1")
    issues = validate_school_markdown(parent, actively_gathering, GOAL)
    assert "information_gathering_already_underway" in issues["grounding"]
    over_scoped = INTERNAL + "\n## Recommendations\n\n- Contact authorities.\n"
    issues = validate_school_markdown(internal, over_scoped, GOAL)
    assert "unrequested_recommendations_section" in issues["role"]
    bold_recommendations = INTERNAL + "\n**Recommendations:**\n\nNone at this stage.\n"
    issues = validate_school_markdown(internal, bold_recommendations, GOAL)
    assert "unrequested_recommendations_section" in issues["role"]
    medical_specificity = PARENT.replace(
        "receiving appropriate care", "receiving appropriate medical care")
    issues = validate_school_markdown(parent, medical_specificity, GOAL)
    assert "medical_care_specificity_added" in issues["grounding"]
    reviewing = PARENT.replace(
        "The exact time",
        "We are currently reviewing the situation and are taking all necessary "
        "steps.\n\nFurther information will be gathered.\n\nThe exact time",
    )
    issues = validate_school_markdown(parent, reviewing, GOAL)
    assert "review_or_verification_underway" in issues["grounding"]
    assert "necessary_steps_already_underway" in issues["grounding"]
    assert "information_gathering_committed" in issues["grounding"]
    process_claims = PARENT.replace(
        "The exact time",
        "The incident was reported to the school administration. We are "
        "currently in the process of verifying the details. We will provide "
        "you with more information.\n\nThe exact time",
    )
    issues = validate_school_markdown(parent, process_claims, GOAL)
    assert "incident_reported_to_school_claimed" in issues["grounding"]
    assert "review_or_verification_underway" in issues["grounding"]
    assert "future_update_commitment" in issues["grounding"]


def test_private_parent_send_is_green_but_public_health_post_is_red():
    guard = DataUseGuard()
    private = CandidateAction(
        tool="email", operation="send", target="Ali's parent",
        purpose="Send the private accident notice only to Ali's parent",
        metadata={
            "audience": "private_recipient",
            "output_scope": "private_recipient",
            "data_use_concepts": [
                "health_or_discipline", "student_sensitive_data",
                "external_release",
            ],
        },
    )
    public = CandidateAction(
        tool="publish", operation="publish", target="Facebook",
        purpose="Publish Ali's accident and injuries publicly",
        metadata={
            "audience": "public", "output_scope": "public_release",
            "data_use_concepts": [
                "health_or_discipline", "student_sensitive_data",
                "external_release", "public_disclosure",
            ],
        },
    )

    assert guard.assess(private)["decision"] == "GREEN"
    assert guard.assess(public)["decision"] == "RED"


class _WrongDomainLLM:
    backend = "fake"

    def chat_json(self, system: str, user: str, *, max_tokens: int = 450):
        return {
            "school_domain": True,
            "case_relation": "follow_up",
            "school_area": "school_event",
            "requested_action": "draft",
            "audience": "internal",
            "confidence": 0.93,
            "rationale": "The active workflow appears related.",
            "external_release": True,
        }


def test_active_case_cannot_capture_unrelated_world_cup_request():
    semantics = SchoolInputSemantics(_WrongDomainLLM())

    unrelated = semantics.classify(
        "Prepare a World Cup report",
        active_workflow_id="ad_hoc_school_event_reporting",
    )
    followup = semantics.classify(
        "They still cannot finish the speech. What should we change?",
        active_workflow_id="ad_hoc_school_event_reporting",
    )
    new_school_case = semantics.classify(
        "Prepare a student discipline report",
        active_workflow_id=None,
    )

    assert unrelated["school_domain"] is False
    assert unrelated["case_relation"] == "unrelated"
    assert unrelated["school_area"] == "other"
    assert unrelated["data_use_concepts"] == []
    assert "boundary_guard" in unrelated["source"]
    assert followup["school_domain"] is True
    assert followup["case_relation"] == "follow_up"
    assert new_school_case["school_domain"] is True


def test_private_send_runs_cover_and_md_before_one_green_gate(tmp_path: Path):
    envelope = _envelope(tmp_path)
    envelope.raw_goal = envelope.normalized_goal = (
        "Prepare an internal accident report and a private parent notice, "
        "then contact Ali's parent. Unknown facts must stay TBC."
    )
    envelope.metadata["school_semantics"].update({
        # Reproduce the real API's lossy composite summary: it noticed only the
        # draft and called the audience internal. The deterministic action layer
        # must still see the explicit parent-contact clause in the raw request.
        "requested_action": "draft",
        "audience": "internal",
    })
    envelope.metadata["data_use_concepts"] = [
        "health_or_discipline", "student_sensitive_data", "external_release",
        "official_record_change",
    ]
    plan = _raw_plan(envelope)

    diag = normalize_school_markdown_plan(plan, envelope)

    assert diag["release_gate_action_id"]
    assert [a.metadata.get("school_content_role") for a in plan.actions] == [
        "chat_companion", "artifact", "artifact", "external_release_gate",
    ]
    assert all(a.tool == "fs" and a.target.endswith(".md")
               for a in plan.actions[1:3])
    assert all(a.metadata["release_state"] == "draft_only"
               for a in plan.actions[1:3])
    assert plan.actions[-1].metadata["external_release_action"] is True
    assert plan.actions[-1].metadata["release_state"] == "pending_approval"
    assert plan.actions[-1].metadata["audience"] == "private_recipient"
    assert all("official_record_change" not in a.metadata["data_use_concepts"]
               for a in plan.actions[1:3])

    guard = DataUseGuard()
    decisions = [guard.assess(action) for action in plan.actions]
    assert [item["decision"] for item in decisions] == [
        "NO_OVERRIDE", "NO_OVERRIDE", "NO_OVERRIDE", "GREEN",
    ]
    assert decisions[0]["features"]["is_external"] is False
    assert decisions[-1]["features"]["is_external"] is True
    assert decisions[-1]["features"]["is_private_recipient"] is True


def test_explicit_do_not_send_never_creates_release_gate(tmp_path: Path):
    envelope = _envelope(tmp_path)
    envelope.metadata["school_semantics"].update({
        "requested_action": "send",
        "audience": "private_recipient",
    })
    envelope.metadata["data_use_concepts"] = ["external_release"]
    plan = _raw_plan(envelope)

    diag = normalize_school_markdown_plan(plan, envelope)

    assert diag["release_gate_action_id"] is None
    assert not any((a.metadata or {}).get("external_release_action")
                   for a in plan.actions)


def test_public_sensitive_markdown_draft_is_red_not_external_green():
    action = CandidateAction(
        tool="fs", operation="save_under_outputs", target="public_notice.md",
        purpose="Draft a public notice containing a student's medical details",
        metadata={
            "school_output_contract": True,
            "school_content_role": "artifact",
            "audience": "public", "output_scope": "public_draft",
            "release_state": "draft_only",
            "data_use_concepts": ["health_or_discipline", "public_disclosure"],
        },
    )
    assessed = DataUseGuard().assess(action)
    assert assessed["decision"] == "RED"
    assert assessed["features"]["is_public"] is True
    assert assessed["features"]["is_external"] is False


def test_public_semantics_tighten_generic_notice_before_file_write(tmp_path: Path):
    envelope = _envelope(tmp_path)
    envelope.raw_goal = envelope.normalized_goal = (
        "Publish a Facebook post naming Ali and describing his medical condition."
    )
    envelope.metadata["school_semantics"].update({
        "requested_action": "publish", "audience": "public",
    })
    envelope.metadata["data_use_concepts"] = [
        "health_or_discipline", "student_sensitive_data",
        "public_disclosure", "external_release",
    ]
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="test", planning_mode="direct",
        actions=[
            CandidateAction(
                action_id="generic", tool="fs", operation="save_under_outputs",
                target=str(tmp_path / "outputs" / "student_accident_notice.md"),
                purpose="Draft the requested student accident notice",
            )
        ],
    )

    normalize_school_markdown_plan(plan, envelope)
    artifact = next(a for a in plan.actions
                    if a.metadata.get("school_content_role") == "artifact")

    assert artifact.metadata["artifact_role"] == "public_communication_draft"
    assert artifact.metadata["audience"] == "public"
    assert artifact.metadata["output_scope"] == "public_draft"
    assert DataUseGuard().assess(artifact)["decision"] == "RED"
    cover = next(a for a in plan.actions
                 if a.metadata.get("school_content_role") == "chat_companion")
    assert "nothing has been created" in cover.metadata["body"].lower()
    assert "I prepared" not in cover.metadata["body"]


def test_sensitive_mention_failsafe_does_not_gate_contract_local_drafts(
    isolated_workspace: Path,
):
    outputs = isolated_workspace / "outputs"
    roots = [str(isolated_workspace / "workspace"), str(outputs)]
    tools = {name: MockTool(name) for name in (
        "report", "docx", "pptx", "xlsx", "desktop", "gui", "email",
        "publish", "code", "shell", "memory", "image_gen",
    )}
    tools["fs"] = FilesystemTool(roots)
    tools["chat"] = ChatTool()
    runtime = Runtime(
        config_dir=isolated_workspace / "configs",
        prompts_dir=isolated_workspace / "prompts",
        planner=_RuntimePlanner(outputs), tool_registry=tools,
        human_gate=HumanGate("reject_all"),
        trace_dir=isolated_workspace / "traces",
        domain_pack="public_school",
    )
    pre = PreGovernanceAssessment(
        task_id="sensitive_contract",
        task_category="report_generation",
        planning_mode="direct",
        context_features={"sensitive_data_mention": True},
    )
    risk = ActionRiskAssessment(
        task_id="sensitive_contract", action_id="cover", risk_score=0,
        risk_level="low", recommended_route="BLUE", reasons=["safe"],
    )
    cover = CandidateAction(
        action_id="cover", tool="chat", operation="answer",
        metadata={
            "school_output_contract": True,
            "school_content_role": "chat_companion",
            "release_state": "not_applicable",
        },
    )
    draft = CandidateAction(
        action_id="draft", tool="fs", operation="save_under_outputs",
        target=str(outputs / "internal_support_plan.md"),
        metadata={
            "school_output_contract": True,
            "school_content_role": "artifact",
            "release_state": "draft_only",
            "output_scope": "internal",
        },
    )
    legacy = CandidateAction(action_id="legacy", tool="chat", operation="answer")

    assert runtime.governance.decide(pre=pre, action=cover, risk=risk).route == "BLUE"
    risk.action_id = "draft"
    assert runtime.governance.decide(pre=pre, action=draft, risk=risk).route == "BLUE"
    risk.action_id = "legacy"
    assert runtime.governance.decide(pre=pre, action=legacy, risk=risk).route == "GREEN"


class _RuntimePlanner:
    planner_id = "openai_test"

    def __init__(self, outputs: Path) -> None:
        self.outputs = outputs

    def plan(self, brief, system_prompt):
        return {
            "plan_id": "school_plan", "task_id": brief["task_id"],
            "planner_id": self.planner_id,
            "planning_mode": brief["planning_mode"],
            "used_refusal_recovery": False, "notes": [],
            "actions": [
                {
                    "action_id": "chat1", "tool": "chat", "operation": "answer",
                    "target": "", "purpose": "cover the two drafted files",
                    "expected_effect": "show a short delivery note",
                    "reversibility": "high", "uncertainty": "low",
                    "risk_factors": [], "requires_governance": True,
                    "metadata": {"body": "Here are the requested documents."},
                },
                {
                    "action_id": "internal1", "tool": "docx",
                    "operation": "save_under_outputs",
                    "target": str(self.outputs / "internal_incident_report.docx"),
                    "purpose": "Prepare only the internal incident report",
                    "expected_effect": "write the internal report",
                    "reversibility": "high", "uncertainty": "medium",
                    "risk_factors": [], "requires_governance": True,
                    "metadata": {"title": "Internal Incident Report"},
                },
                {
                    "action_id": "parent1", "tool": "docx",
                    "operation": "save_under_outputs",
                    "target": str(self.outputs / "parent_notification_draft.docx"),
                    "purpose": "Prepare only the private parent notification draft",
                    "expected_effect": "write the private notification draft",
                    "reversibility": "high", "uncertainty": "medium",
                    "risk_factors": [], "requires_governance": True,
                    "metadata": {"title": "Private Parent Notification"},
                },
            ],
        }


def test_runtime_end_to_end_keeps_chat_and_md_artifacts_separate(
    isolated_workspace: Path,
):
    outputs = isolated_workspace / "outputs"
    workspace = isolated_workspace / "workspace"
    outputs.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    roots = [str(workspace), str(outputs)]
    llm = _BundleLLM()
    tools = {name: MockTool(name) for name in (
        "report", "docx", "pptx", "xlsx", "desktop", "gui", "email",
        "publish", "code", "shell", "memory", "image_gen",
    )}
    tools["fs"] = FilesystemTool(roots)
    tools["chat"] = ChatTool()
    runtime = Runtime(
        config_dir=isolated_workspace / "configs",
        prompts_dir=isolated_workspace / "prompts",
        planner=_RuntimePlanner(outputs), tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=isolated_workspace / "traces",
        subject_confidence_path=isolated_workspace / "state" / "sc.jsonl",
        content_synthesizer=ContentSynthesizer(chat_llm=llm),
        domain_pack="public_school",
    )
    runtime.profile.profile["workspace_roots"] = roots
    assert runtime.verifier is not None
    runtime.verifier.chat_llm = llm

    result = runtime.run(
        raw_goal=GOAL, task_id="task_school_contract",
        session_id="task_school_contract",
        metadata={
            "school_semantics_checked": True,
            "school_semantics": {
                "checked": True, "school_domain": True,
                "case_relation": "new_case", "school_area": "health",
                "requested_action": "draft", "audience": "internal",
            },
            "data_use_concepts": [
                "health_or_discipline", "student_sensitive_data",
            ],
        },
    )

    assert result.verification and result.verification["pass"] is True
    assert result.final_route == "BLUE"
    chat = next(e.output_summary for e in result.executions
                if e.action_id == "chat1" and e.status == "success")
    assert len(chat) < 700
    assert "Dear Parent" not in chat
    md_files = sorted(outputs.glob("*.md"))
    assert [p.name for p in md_files] == [
        "internal_incident_report.md", "parent_notification_draft.md",
    ]
    assert not list(outputs.glob("*.docx"))
    assert "Dear Parent" not in md_files[0].read_text(encoding="utf-8")
    assert "Internal Incident Report" not in md_files[1].read_text(encoding="utf-8")


@pytest.mark.parametrize("route", ["RED", "INFEASIBLE"])
def test_runtime_route_exempt_does_not_call_judge_or_inject_verify_failure(
    isolated_workspace: Path,
    route: str,
):
    outputs = isolated_workspace / "outputs"
    workspace = isolated_workspace / "workspace"
    roots = [str(workspace), str(outputs)]
    envelope = _envelope(isolated_workspace)
    envelope.workspace_roots = roots
    plan = _raw_plan(envelope)
    normalize_school_markdown_plan(plan, envelope)
    tools = {name: MockTool(name) for name in (
        "report", "docx", "pptx", "xlsx", "desktop", "gui", "email",
        "publish", "code", "shell", "memory", "image_gen",
    )}
    tools["fs"] = FilesystemTool(roots)
    tools["chat"] = ChatTool()
    runtime = Runtime(
        config_dir=isolated_workspace / "configs",
        prompts_dir=isolated_workspace / "prompts",
        planner=_RuntimePlanner(outputs), tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=isolated_workspace / "traces",
        subject_confidence_path=isolated_workspace / "state" / "sc.jsonl",
        content_synthesizer=ContentSynthesizer(chat_llm=_BundleLLM()),
        domain_pack="public_school",
    )
    runtime.profile.profile["workspace_roots"] = roots
    pre = PreGovernanceAssessment(
        task_id=envelope.task_id,
        task_category="report_generation",
        planning_mode="direct",
    )
    result = TaskRunResult(envelope=envelope, pre_assessment=pre, plan=plan)
    result.decisions.append(GovernanceDecision(
        task_id=envelope.task_id,
        action_id=plan.actions[1].action_id,
        route=route,
        reasons=["governed route"],
        ticket_required=False,
        approval_required=False,
    ))

    runtime._run_verification(envelope, plan, result)

    assert result.verification is not None
    assert result.verification["pass"] is True
    assert result.verification["summary"] == f"skipped:route_exempt:{route}"
    assert result.verification["checks"] == []
    assert "judge" not in result.verification
    assert not any(e.action_id == "verifier_synthetic" for e in result.executions)
