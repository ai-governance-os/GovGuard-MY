from __future__ import annotations

from pathlib import Path
import time
import pytest

from teow_agl.models import CandidateAction, CandidatePlan, ExecutionResult, TaskEnvelope
from teow_agl.modules.module_school_situation import (
    SchoolSituationCompiler,
    govern_school_research_actions,
    reconcile_school_response_pack,
)
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer
from teow_agl.modules.module_101d_data_use_guard import DataUseGuard
from teow_agl.modules.module_school_artifact_guard import (
    normalize_school_markdown_plan,
    validate_school_markdown,
)
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.runtime import Runtime


ROOT = Path(__file__).resolve().parents[1]
POLICY = (
    ROOT / "configs" / "domain_packs" / "public_school" /
    "situation_response_policy.json"
)


def _semantics(situation: dict) -> dict:
    return {
        "checked": True,
        "school_domain": True,
        "case_relation": "new_case",
        "school_area": "health",
        "requested_action": "handle case",
        "audience": "internal",
        "confidence": 0.92,
        "data_use_concepts": ["health_or_discipline"],
        "situation": situation,
        "source": "test_semantic_llm",
    }


def _envelope(tmp_path: Path, compiled: dict) -> TaskEnvelope:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return TaskEnvelope(
        task_id="task_school",
        session_id="session_school",
        user_id="user",
        raw_goal="A snake bit a student at school.",
        normalized_goal="A snake bit a student at school.",
        workspace_roots=[str(tmp_path), str(outputs)],
        metadata={
            "school_semantics_checked": True,
            "school_semantics": _semantics({}),
            "school_situation": compiled["situation"],
            "school_response_pack": compiled["response_pack"],
        },
    )


def test_unseen_active_hazard_builds_complete_non_authorising_pack():
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "A snake has appeared at school and reportedly bitten a student.",
        _semantics({
            "family": "safety_emergency",
            "phase": "ongoing",
            "severity": "critical",
            "signals": ["active_danger", "injury_or_illness", "minor_involved"],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["guardian", "medical_services", "fire_and_rescue"],
            "case_summary": "A student was reportedly bitten by a snake at school.",
            "known_facts": [{"fact_id": "hazard", "value": "snake", "status": "reported"}],
            "unknowns": [{"fact_id": "medical_response_status", "impact": "life_safety"}],
        }),
    )

    situation = compiled["situation"]
    pack = compiled["response_pack"]
    selected = {
        item["artifact_role"] for item in pack["deliverables"]
        if item["selected"] and item["kind"] == "artifact"
    }
    assert situation["severity"] == "critical"
    assert "active_danger" in situation["signals"]
    assert {
        "internal_incident_report", "site_safety_checklist",
        "private_parent_notice", "medical_handover_script",
        "emergency_contact_script",
    }.issubset(selected)
    assert pack["emergency_banner"]
    assert "route" not in str(pack).lower()


def test_rejected_live_bundle_gets_complete_valid_safe_markdown_pack(tmp_path: Path):
    class BrokenBundleLLM:
        def __init__(self):
            self.calls = 0

        def chat_json(self, **kwargs):
            self.calls += 1
            return {"malformed": "provider truncated the bundle"}

    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "A snake has appeared at school and reportedly bitten a student.",
        _semantics({
            "family": "safety_emergency",
            "phase": "ongoing",
            "severity": "critical",
            "signals": ["active_danger", "injury_or_illness", "minor_involved"],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["guardian", "medical_services"],
            "case_summary": "A student was reportedly bitten by a snake at school.",
            "known_facts": [
                {"fact_id": "hazard", "value": "snake", "status": "reported"}
            ],
            "unknowns": [
                {"fact_id": "medical_response_status", "impact": "life_safety"}
            ],
        }),
    )
    envelope = _envelope(tmp_path, compiled)
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="broken_live_planner",
        planning_mode="direct", actions=[],
    )
    reconcile_school_response_pack(plan, envelope)
    normalize_school_markdown_plan(plan, envelope)
    artifacts = [
        action for action in plan.actions
        if action.metadata.get("school_content_role") == "artifact"
    ]
    assert {action.metadata["artifact_role"] for action in artifacts} == {
        "internal_incident_report", "private_parent_notice",
        "medical_handover_script", "site_safety_checklist",
        "emergency_contact_script", "student_accountability_checklist",
    }

    llm = BrokenBundleLLM()
    result = ContentSynthesizer(chat_llm=llm).enrich_school_plan(
        plan.actions, user_intent=envelope.raw_goal,
    )
    assert result["result"] == "synthesized_verified_response_pack_safe_fallback"
    assert llm.calls == 1
    assert len(result["safe_fallback_action_ids"]) == len(artifacts)
    assert len({action.action_id for action in artifacts}) == len(artifacts)
    assert len({action.target for action in artifacts}) == len(artifacts)
    for action in artifacts:
        body = action.metadata.get("content") or ""
        assert body and "held for review" not in body.lower()
        assert action.metadata["synthesis_skip"] is True
        assert action.metadata["school_generation_failed"] is False
        assert not any(validate_school_markdown(
            action, body, envelope.raw_goal,
        ).values())
    official_roles = {"site_safety_checklist", "emergency_contact_script"}
    for action in artifacts:
        if action.metadata["artifact_role"] in official_roles:
            assert "Official-source check: REQUIRED" in action.metadata["content"]


def test_only_life_safety_unknown_causes_one_clarification():
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "A student was found injured at school. Help us handle it.",
        _semantics({
            "family": "health_medical",
            "phase": "unknown",
            "severity": "high",
            "signals": ["injury_or_illness", "minor_involved"],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["guardian", "medical_services"],
            "case_summary": "A student was reportedly found injured at school.",
            "unknowns": [
                {"fact_id": "danger_still_present", "impact": "life_safety"},
                {"fact_id": "student_name", "impact": "content_only"},
            ],
        }),
    )
    question = compiled["response_pack"]["critical_question"]
    assert question["question_id"] == "immediate_danger"
    assert compiled["response_pack"]["state"] == "needs_clarification"

    resumed = compiler.compile(
        "A student was found injured at school. Help us handle it.",
        _semantics({
            "family": "health_medical", "phase": "unknown", "severity": "high",
            "signals": ["injury_or_illness", "minor_involved"],
            "unknowns": [{"fact_id": "danger_still_present", "impact": "life_safety"}],
        }),
        clarification_answers={"immediate_danger": "Unknown"},
    )
    assert resumed["response_pack"]["critical_question"] is None


def test_coverage_reconciler_inserts_planner_omissions_as_markdown(tmp_path: Path):
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "A snake bit a student at school.",
        _semantics({
            "family": "safety_emergency", "phase": "ongoing", "severity": "critical",
            "signals": ["active_danger", "injury_or_illness"],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["guardian", "medical_services"],
        }),
    )
    envelope = _envelope(tmp_path, compiled)
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="omitting_planner", planning_mode="direct",
        actions=[CandidateAction(
            tool="fs", operation="save_under_outputs", target="internal_incident_report.md",
            purpose="prepare internal report", metadata={"artifact_role": "internal_incident_report"},
        )],
    )
    report = reconcile_school_response_pack(plan, envelope)
    expected = {
        item["deliverable_id"] for item in compiled["response_pack"]["deliverables"]
        if item["selected"]
    }
    got = {str(action.metadata.get("deliverable_id")) for action in plan.actions}
    assert report["coverage_complete"] is True
    assert expected.issubset(got)
    assert all(
        action.requires_governance is True
        for action in plan.actions
        if action.metadata.get("coverage_source") == "school_response_pack"
    )


def test_response_pack_supersedes_planner_xlsx_and_secondary_family_noise(tmp_path: Path):
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "A student-data file was emailed to the wrong vendor. Do not send anything.",
        _semantics({
            "family": "cyber_data",
            "secondary_families": ["communications_reputation"],
            "phase": "just_occurred", "severity": "high",
            "signals": ["data_security_incident", "personal_data_involved"],
            "affected_people_types": ["student", "staff"],
        }),
    )
    selected = {
        item["artifact_role"] for item in compiled["response_pack"]["deliverables"]
        if item.get("selected") and item.get("kind") == "artifact"
    }
    assert selected == {
        "cyber_incident_response", "evidence_preservation_log",
        "regulatory_notification_assessment",
    }
    envelope = _envelope(tmp_path, compiled)
    rogue = CandidateAction(
        tool="xlsx", operation="save_under_outputs", target="student_data.xlsx",
        purpose="create xlsx artifact", metadata={},
    )
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="live_planner", planning_mode="direct",
        actions=[rogue],
    )
    result = reconcile_school_response_pack(plan, envelope)
    assert rogue.action_id in result["superseded"]
    assert all(action.tool != "xlsx" for action in plan.actions)


def test_source_spreadsheet_mention_is_not_misread_as_requested_xlsx_output():
    goal = (
        "A spreadsheet containing student names was emailed to the wrong vendor. "
        "Prepare the school response pack."
    )
    verifier = VerifierModule(rules={
        "format_check": {
            "enabled": True,
            "extension_patterns": {"xlsx": r"\bspreadsheet\b"},
        }
    })
    assert verifier._extensions_from_intent(goal) == []
    assert Runtime._detect_target_tool(goal) == ""
    assert verifier._extensions_from_intent(
        "Create a spreadsheet containing the approved inventory."
    ) == ["xlsx"]
    assert Runtime._detect_target_tool(
        "Create an Excel workbook containing the approved inventory."
    ) == "xlsx"


def test_llm_judge_skips_intentional_school_domain_boundary():
    class MustNotBeCalled:
        def chat_json(self, *args, **kwargs):
            raise AssertionError("boundary must not be judged as task content")

    envelope = TaskEnvelope(
        task_id="task_boundary", session_id="session", user_id="user",
        raw_goal="Prepare a World Cup report.",
        normalized_goal="Prepare a World Cup report.",
        metadata={
            "school_semantics": {
                "checked": True, "school_domain": False,
                "case_relation": "unrelated",
            }
        },
    )
    judge = VerifierModule(
        rules={"llm_judge": {"enabled": True}},
        chat_llm=MustNotBeCalled(),
    ).llm_judge(
        envelope=envelope, plan_actions=[], executions=[],
        final_route="BLUE", task_category="report_generation",
    )
    assert judge["pass"] is None
    assert judge["skipped_reason"] == "school_domain_boundary"


def test_llm_judge_does_not_demand_parent_pack_again_for_delta_child():
    class MustNotBeCalled:
        def chat_json(self, *args, **kwargs):
            raise AssertionError("delta contract is verified deterministically")

    envelope = TaskEnvelope(
        task_id="task_delta", session_id="session", user_id="user",
        raw_goal="Prepare the original pack plus one added authority summary.",
        normalized_goal="Prepare the original pack plus one added authority summary.",
        metadata={
            "response_pack_mode": "delta",
            "school_semantics": {
                "checked": True, "school_domain": True,
                "case_relation": "follow_up",
            },
        },
    )
    judge = VerifierModule(
        rules={"llm_judge": {"enabled": True}},
        chat_llm=MustNotBeCalled(),
    ).llm_judge(
        envelope=envelope, plan_actions=[], executions=[],
        final_route="BLUE", task_category="report_generation",
    )
    assert judge["pass"] is None
    assert judge["skipped_reason"] == "school_response_pack_delta"


def test_multiple_explicit_recipients_become_independent_actions(tmp_path: Path):
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "Contact the parent and emergency services about the school injury.",
        _semantics({
            "family": "health_medical", "phase": "ongoing", "severity": "critical",
            "signals": ["active_danger", "injury_or_illness"],
            "explicit_external_actions": ["guardian", "malaysia_emergency_services_999"],
        }),
    )
    envelope = _envelope(tmp_path, compiled)
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="planner", planning_mode="direct",
        actions=[CandidateAction(
            tool="chat", operation="request_external_release", target="guardian",
            metadata={"artifact_role": "external_release_gate"},
        )],
    )
    reconcile_school_response_pack(plan, envelope)
    gates = [a for a in plan.actions if a.metadata.get("external_release_action")]
    assert {a.metadata["recipient_type"] for a in gates} == {
        "guardian", "malaysia_emergency_services_999",
    }
    assert len({a.action_id for a in gates}) == 2


def test_custom_external_output_is_split_into_blue_candidate_draft_and_release_gate(tmp_path: Path):
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "Prepare the school follow-up pack.",
        _semantics({"family": "general_school_admin", "severity": "low"}),
        selected_deliverable_ids=[],
        custom_deliverables=[{
            "label": "Report for the education authority",
            "audience": "external_agency",
            "recipient_type": "education_authority",
            "mode": "external_release",
        }],
    )
    custom = [
        item for item in compiled["response_pack"]["deliverables"]
        if item.get("requirement") == "user_added"
    ]
    assert {item["kind"] for item in custom} == {"artifact", "external_action"}
    envelope = _envelope(tmp_path, compiled)
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="planner", planning_mode="direct", actions=[]
    )
    reconcile_school_response_pack(plan, envelope)
    custom_file = next(
        a for a in plan.actions
        if a.tool == "fs"
        and str(a.metadata.get("deliverable_id") or "").startswith("custom_")
    )
    assert Path(custom_file.target).name.startswith("custom_school_output_")
    assert any(a.metadata.get("external_release_action") for a in plan.actions)


def test_school_web_query_is_regenerated_without_case_pii(tmp_path: Path):
    compiled = SchoolSituationCompiler(POLICY).compile(
        "Search current rules for Ali's snake injury at SJK Example.",
        _semantics({
            "family": "safety_emergency", "phase": "ongoing", "severity": "high",
            "signals": ["injury_or_illness"],
        }),
    )
    envelope = _envelope(tmp_path, compiled)
    action = CandidateAction(
        tool="web_search", operation="search", target="Ali snake injury SJK Example",
        purpose="find current official guidance", metadata={"query": "Ali SJK Example"},
    )
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="planner", planning_mode="direct", actions=[action]
    )
    result = govern_school_research_actions(plan, envelope)
    query = action.metadata["query"]
    assert result["active"] is True
    assert "Ali" not in query and "SJK Example" not in query
    assert "site:moe.gov.my" in query and "site:moh.gov.my" in query
    assert action.requires_governance is True


def test_mixed_red_pack_still_verifies_successful_artifact(tmp_path: Path):
    action = CandidateAction(
        action_id="act_file", tool="fs", operation="save_under_outputs",
        target=str(tmp_path / "draft.md"), purpose="draft",
        metadata={
            "school_output_contract": True,
            "school_content_role": "artifact",
            "artifact_role": "school_document",
        },
    )
    execution = ExecutionResult(
        task_id="task", action_id="act_file", ticket_id="ticket",
        status="success", output_summary="fs_saved:draft.md",
        affected_resources=[str(tmp_path / "draft.md")],
    )
    envelope = TaskEnvelope(
        task_id="task", session_id="session", user_id="user",
        raw_goal="prepare a school draft", normalized_goal="prepare a school draft",
    )
    report = VerifierModule(rules={"enabled": True}).verify(
        envelope=envelope,
        plan_actions=[action], executions=[execution], final_route="RED",
    )
    assert not report["summary"].startswith("skipped:route_exempt")


def test_response_pack_api_pauses_once_and_uses_revision_lock(monkeypatch):
    from fastapi.testclient import TestClient
    import server.app as appmod

    semantic = _semantics({
        "family": "health_medical", "phase": "unknown", "severity": "high",
        "signals": ["injury_or_illness", "minor_involved"],
        "affected_people_types": ["student"],
        "stakeholder_candidates": ["guardian", "medical_services"],
        "case_summary": "A student was reportedly found injured at school.",
        "unknowns": [{"fact_id": "danger_still_present", "impact": "life_safety"}],
    })
    monkeypatch.setattr(appmod, "_school_semantics_for_goal", lambda *a, **k: semantic)
    client = TestClient(appmod.app)
    started = client.post("/api/tasks", json={
        "raw_goal": "A student was found injured at school. Help us handle it.",
        "interaction_mode": "review_if_needed",
    })
    assert started.status_code == 200
    task_id = started.json()["task_id"]
    payload = {}
    for _ in range(60):
        payload = client.get(f"/api/tasks/{task_id}").json()
        if payload.get("status") == "awaiting_clarification":
            break
        time.sleep(0.02)
    assert payload["status"] == "awaiting_clarification"
    assert payload["response_pack"]["critical_question"]["question_id"] == "immediate_danger"
    stale = client.post(f"/api/tasks/{task_id}/response-pack/confirm", json={
        "revision": 999,
        "answer": "Unknown",
        "selected_deliverable_ids": [],
    })
    assert stale.status_code == 409

    monkeypatch.setattr(appmod, "start_task", lambda req: {"task_id": "task_child"})
    confirmed = client.post(f"/api/tasks/{task_id}/response-pack/confirm", json={
        "revision": payload["response_pack"]["revision"],
        "question_id": "immediate_danger",
        "answer": "No",
        "selected_deliverable_ids": ["internal_incident_report"],
    })
    assert confirmed.status_code == 200
    assert confirmed.json()["task_id"] == "task_child"
    duplicate = client.post(f"/api/tasks/{task_id}/response-pack/confirm", json={
        "revision": payload["response_pack"]["revision"],
        "question_id": "immediate_danger",
        "answer": "No",
        "selected_deliverable_ids": ["internal_incident_report"],
    })
    assert duplicate.status_code == 409


def test_cross_domain_signals_add_required_coverage_without_event_keywords():
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "Handle this multi-domain school situation.",
        _semantics({
            "family": "health_medical",
            "secondary_families": [],
            "phase": "just_occurred",
            "severity": "high",
            "signals": [
                "injury_or_illness", "transport_operation",
                "food_water_exposure", "event_operation",
                "evacuation_accountability", "data_security_incident",
            ],
            "affected_people_types": ["student"],
        }),
    )
    selected = {
        item["artifact_role"] for item in compiled["response_pack"]["deliverables"]
        if item["selected"]
    }
    assert {
        "transport_response_plan", "food_safety_response", "event_action_plan",
        "external_stakeholder_message", "student_accountability_checklist",
        "cyber_incident_response", "evidence_preservation_log",
        "regulatory_notification_assessment",
    }.issubset(selected)


@pytest.mark.parametrize(
    ("goal", "situation", "expected"),
    [
        (
            "The school bus broke down; students are safe. Prepare parent information.",
            {
                "family": "transport_travel", "severity": "medium",
                "signals": ["transport_operation", "guardian_notification_relevant"],
                "affected_people_types": ["student"],
            },
            {"transport_response_plan", "private_parent_notice"},
        ),
        (
            "A guest cancelled a school event. Prepare the event response.",
            {
                "family": "events_cocurricular", "severity": "low",
                "signals": ["event_operation"],
            },
            {"event_action_plan", "external_stakeholder_message"},
        ),
        (
            "Several students vomited after lunch; no immediate danger is reported.",
            {
                "family": "food_hygiene", "severity": "medium",
                "signals": [
                    "food_water_exposure", "injury_or_illness",
                    "guardian_notification_relevant",
                ],
                "affected_people_types": ["student"],
            },
            {
                "food_safety_response", "internal_incident_report",
                "private_parent_notice",
            },
        ),
        (
            "A file containing student data was sent to the wrong recipient.",
            {
                "family": "cyber_data", "severity": "high",
                "signals": [
                    "personal_data_involved", "evidence_preservation_needed",
                    "data_security_incident",
                ],
                "affected_people_types": ["student"],
            },
            {
                "cyber_incident_response", "evidence_preservation_log",
                "regulatory_notification_assessment",
            },
        ),
    ],
)
def test_isolated_school_domains_select_exact_required_pack(goal, situation, expected):
    compiled = SchoolSituationCompiler(POLICY).compile(
        goal, _semantics(situation),
    )
    selected = {
        item["artifact_role"]
        for item in compiled["response_pack"]["deliverables"]
        if item.get("selected") and item.get("kind") == "artifact"
    }
    assert selected == expected


def test_staff_injury_and_low_student_injury_do_not_over_escalate():
    compiler = SchoolSituationCompiler(POLICY)
    staff = compiler.compile(
        "A teacher slipped in the staff room.",
        _semantics({
            "family": "health_medical", "severity": "medium",
            "signals": ["injury_or_illness"], "affected_people_types": ["staff"],
        }),
    )
    staff_roles = {
        item["artifact_role"] for item in staff["response_pack"]["deliverables"]
        if item["selected"]
    }
    assert "private_parent_notice" not in staff_roles
    assert "emergency_contact_script" not in staff_roles

    scrape = compiler.compile(
        "A student scraped a knee at school.",
        _semantics({
            "family": "health_medical", "severity": "low",
            "signals": ["injury_or_illness"], "affected_people_types": ["student"],
        }),
    )
    scrape_roles = {
        item["artifact_role"] for item in scrape["response_pack"]["deliverables"]
        if item["selected"]
    }
    assert "private_parent_notice" in scrape_roles
    assert "emergency_contact_script" not in scrape_roles
    assert scrape["situation"]["severity"] == "low"
    assert scrape_roles == {"internal_incident_report", "private_parent_notice"}
    assert scrape["response_pack"]["emergency_banner"] is None
    assert scrape["response_pack"]["critical_question"] is None


def test_high_cyber_incident_does_not_show_irrelevant_life_safety_banner():
    compiled = SchoolSituationCompiler(POLICY).compile(
        "The school's student portal suffered a data breach.",
        _semantics({
            "family": "cyber_data", "phase": "just_occurred", "severity": "high",
            "signals": [
                "personal_data_involved", "evidence_preservation_needed",
                "data_security_incident",
            ],
            "affected_people_types": ["student"],
        }),
    )
    assert compiled["response_pack"]["emergency_banner"] is None


def test_safe_public_fallback_is_private_by_construction_and_not_red(tmp_path: Path):
    class BrokenBundleLLM:
        def chat_json(self, **kwargs):
            return {}

    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "A snake bit Ali at school. Prepare a privacy-safe public holding draft.",
        _semantics({
            "family": "communications_reputation", "severity": "medium",
            "signals": ["public_interest", "injury_or_illness", "minor_involved"],
            "affected_people_types": ["student"],
            "case_summary": "Ali was bitten by a snake at school.",
        }),
        selected_deliverable_ids=["public_communication_draft"],
    )
    envelope = _envelope(tmp_path, compiled)
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="planner", planning_mode="direct", actions=[]
    )
    reconcile_school_response_pack(plan, envelope)
    normalize_school_markdown_plan(plan, envelope)
    ContentSynthesizer(chat_llm=BrokenBundleLLM()).enrich_school_plan(
        plan.actions, user_intent=envelope.raw_goal,
    )
    public = next(
        action for action in plan.actions
        if action.metadata.get("artifact_role") == "public_communication_draft"
    )
    body = public.metadata["content"]
    assert body.startswith("# Public Holding Statement")
    assert "Ali" not in body and "snake" not in body.lower()
    public.metadata["user_intent"] = envelope.raw_goal
    assert DataUseGuard().assess(public)["decision"] != "RED"


def test_semantic_invented_location_is_not_copied_into_safe_fallback(tmp_path: Path):
    class BrokenBundleLLM:
        def chat_json(self, **kwargs):
            return {}

    compiled = SchoolSituationCompiler(POLICY).compile(
        "A snake bit a student at school.",
        _semantics({
            "family": "safety_emergency", "severity": "high",
            "signals": ["injury_or_illness", "minor_involved"],
            "affected_people_types": ["student"],
            "case_summary": "A snake bit a student at the playground.",
            "known_facts": [
                {"fact_id": "location", "value": "playground", "status": "confirmed"}
            ],
        }),
    )
    envelope = _envelope(tmp_path, compiled)
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="planner", planning_mode="direct", actions=[]
    )
    reconcile_school_response_pack(plan, envelope)
    normalize_school_markdown_plan(plan, envelope)
    ContentSynthesizer(chat_llm=BrokenBundleLLM()).enrich_school_plan(
        plan.actions, user_intent=envelope.raw_goal,
    )
    text = "\n".join(
        str(action.metadata.get("content") or "") for action in plan.actions
    )
    assert "playground" not in text.lower()


def test_response_pack_target_is_forced_under_outputs(tmp_path: Path):
    outputs = tmp_path / "outputs"
    state = tmp_path / "state"
    outputs.mkdir()
    state.mkdir()
    action = CandidateAction(
        tool="fs", operation="save_under_outputs",
        target=str(state / "not_outputs.md"), purpose="prepare incident report",
        metadata={
            "artifact_role": "internal_incident_report",
            "coverage_source": "school_response_pack",
        },
    )
    envelope = TaskEnvelope(
        task_id="task_path", session_id="session", user_id="user",
        raw_goal="Prepare a school incident report.",
        normalized_goal="Prepare a school incident report.",
        workspace_roots=[str(tmp_path), str(outputs)],
        metadata={
            "school_semantics_checked": True,
            "school_semantics": _semantics({}),
        },
    )
    plan = CandidatePlan(
        task_id=envelope.task_id, planner_id="planner", planning_mode="direct",
        actions=[action],
    )
    normalize_school_markdown_plan(plan, envelope)
    assert Path(action.target).resolve().is_relative_to(outputs.resolve())
    assert Path(action.target).parent.name == envelope.task_id
    assert Path(action.target).name == "internal_incident_report.md"


def test_custom_public_recipient_cannot_be_downgraded_or_leak_filename():
    compiled = SchoolSituationCompiler(POLICY).compile(
        "Prepare a school response pack.",
        _semantics({"family": "general_school_admin", "severity": "low"}),
        selected_deliverable_ids=[],
        custom_deliverables=[{
            "label": "Ali MyKid 123456789012 public update",
            "audience": "internal",
            "recipient_type": "public_media",
            "mode": "external_release",
        }],
    )
    custom = [
        item for item in compiled["response_pack"]["deliverables"]
        if item.get("requirement") == "user_added"
    ]
    artifact = next(item for item in custom if item["kind"] == "artifact")
    gate = next(item for item in custom if item["kind"] == "external_action")
    assert artifact["audience"] == "public"
    assert gate["audience"] == "public"
    assert artifact["filename"].startswith("custom_school_output_")
    assert "ali" not in artifact["filename"].lower()
    assert "123456789012" not in artifact["filename"]


def test_unicode_custom_outputs_have_unique_targets():
    compiler = SchoolSituationCompiler(POLICY)
    compiled = compiler.compile(
        "准备学校文件。",
        _semantics({"family": "general_school_admin", "severity": "low"}),
        custom_deliverables=[
            {"label": "教育局事故报告"},
            {"label": "活动主办方通知"},
            {"label": "家长说明信"},
        ],
    )
    names = [
        item["filename"] for item in compiled["response_pack"]["deliverables"]
        if item.get("requirement") == "user_added" and item["kind"] == "artifact"
    ]
    assert len(names) == len(set(names)) == 3
