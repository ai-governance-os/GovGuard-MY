"""Epistemic-status governance regressions for school response packs."""
from __future__ import annotations

from types import SimpleNamespace

from teow_agl.models import CandidateAction, CandidatePlan, TaskEnvelope
from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer,
    _school_response_pack_safe_fallback,
)
from teow_agl.modules.module_school_artifact_guard import validate_school_markdown
from teow_agl.runtime import (
    Runtime,
    _hold_failed_school_bundle,
    _verified_fast_school_bundle,
)


def _action(source: str) -> CandidateAction:
    return CandidateAction(
        action_id="epistemic_plan",
        tool="fs",
        operation="save_under_outputs",
        target="school_document_draft.md",
        purpose="Prepare a governed school document",
        metadata={
            "school_output_contract": {"format": "markdown"},
            "school_content_role": "artifact",
            "coverage_source": "school_response_pack",
            "artifact_id": "school_document",
            "artifact_role": "school_document",
            "audience": "internal",
            "source_request": source,
            "requested_languages": ["en"],
            "school_generation_validation": {
                "pass": True,
                "mode": "plan_level_action_id_mapping",
            },
        },
    )


def _body(lines: str) -> str:
    return (
        "# School Operations Draft\n\n"
        "> DRAFT - NOT SENT | Internal review only\n\n"
        + lines
        + "\n\n## Source-confirmed facts\n\n"
        + "The event is on Saturday from 8:30 a.m. to 12:30 p.m. in Rooms "
        + "1 to 6. This section records only information supplied by the user. "
        + "Unknown details remain TBC."
    )


def test_unsupported_specific_arrangements_are_not_treated_as_decided_facts() -> None:
    source = (
        "A parent-teacher conference will be held Saturday from 8:30 a.m. "
        "to 12:30 p.m. in Rooms 1 to 6. Prepare the school documents."
    )
    content = _body(
        "## Coordinator briefing\n\n"
        "- Staff arrive at 7:30 a.m.\n"
        "- Use WhatsApp and walkie-talkies.\n"
        "- Put one table and two chairs in every room.\n"
        "- File the report within two working days."
    )
    issues = validate_school_markdown(_action(source), content, source)["grounding"]
    assert any(item.startswith("unsupported_operational_time") for item in issues)
    assert any(item.startswith("unsupported_communication_channel") for item in issues)
    assert any(item.startswith("unsupported_operational_quantity") for item in issues)
    assert any(item.startswith("unsupported_operational_deadline") for item in issues)


def test_source_supported_operational_details_remain_allowed() -> None:
    source = (
        "The briefing is at 7:30 a.m. Use WhatsApp. Put two chairs in each "
        "room and file the report within two working days."
    )
    content = _body(
        "## Confirmed arrangements\n\n"
        "- Briefing: 7:30 AM.\n"
        "- Use WhatsApp.\n"
        "- Put two chairs in each room.\n"
        "- File the report within two working days."
    )
    issues = validate_school_markdown(_action(source), content, source)["grounding"]
    assert not any(item.startswith("unsupported_operational_") for item in issues)
    assert not any(item.startswith("unsupported_communication_") for item in issues)


def test_explicitly_proposed_specifics_are_allowed_without_becoming_facts() -> None:
    source = "Prepare a parent-teacher conference operations plan."
    content = _body(
        "## Proposed arrangements - subject to school approval\n\n"
        "- Staff arrival: 7:30 a.m.\n"
        "- Communication option: WhatsApp and walkie-talkies.\n"
        "- Room option: one table and two chairs.\n"
        "- Proposed reporting target: within two working days."
    )
    issues = validate_school_markdown(_action(source), content, source)["grounding"]
    assert not any(item.startswith("unsupported_operational_") for item in issues)
    assert not any(item.startswith("unsupported_communication_") for item in issues)


def test_draft_label_alone_does_not_excuse_invented_decisions() -> None:
    source = "Prepare a school operations plan."
    content = _body(
        "## Operations\n\n"
        "DRAFT - NOT SENT. Staff will report at 7:30 a.m. and use WhatsApp."
    )
    issues = validate_school_markdown(_action(source), content, source)["grounding"]
    assert any(item.startswith("unsupported_operational_time") for item in issues)
    assert any(item.startswith("unsupported_communication_channel") for item in issues)


class _DistillerProbe:
    def __init__(self) -> None:
        self.calls = 0

    def maybe_propose(self, **_kwargs):
        self.calls += 1
        return None


def _runtime_probe() -> tuple[Runtime, _DistillerProbe, list[str]]:
    runtime = object.__new__(Runtime)
    distiller = _DistillerProbe()
    events: list[str] = []
    runtime.skill_distiller = distiller
    runtime.task_decomposition_cfg = {}
    runtime.curator_proposals = []
    runtime._emit = lambda _module, kind, *_args, **_kwargs: events.append(kind)
    return runtime, distiller, events


def _result(action: CandidateAction, verification: dict):
    plan = CandidatePlan(
        task_id="task_epistemic",
        planner_id="test",
        planning_mode="direct",
        actions=[action],
    )
    return SimpleNamespace(
        verification=verification,
        final_route="BLUE",
        plan=plan,
        task_category="school_admin",
    )


def _envelope() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_epistemic",
        session_id="session_epistemic",
        user_id="tester",
        raw_goal="Prepare the school document.",
        normalized_goal="Prepare the school document.",
    )


def test_school_skill_distillation_rejects_safe_format_only() -> None:
    runtime, distiller, events = _runtime_probe()
    action = _action("Prepare the school document.")
    action.metadata["school_generation_validation"] = {
        "pass": True,
        "mode": "deterministic_response_pack_fallback",
    }
    runtime._run_skill_distiller(
        _envelope(),
        _result(action, {
            "pass": True,
            "verification_grade": "safe_format_only",
            "judge": {"pass": None},
        }),
    )
    assert distiller.calls == 0
    assert "skill_distiller_skipped_semantic_unverified" in events


def test_school_skill_distillation_accepts_semantically_verified_live_work() -> None:
    runtime, distiller, events = _runtime_probe()
    action = _action("Prepare the school document.")
    runtime._run_skill_distiller(
        _envelope(),
        _result(action, {
            "pass": True,
            "verification_grade": "goal_complete",
            "judge": {"pass": True},
        }),
    )
    assert distiller.calls == 1
    assert "skill_distiller_skipped_semantic_unverified" not in events


class _SequencedLiveLLM:
    backend = "deepseek"

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat_json(self, *, system: str, user: str, **_kwargs) -> dict:
        self.calls.append({"system": system, "user": user})
        return self.responses.pop(0)


def _long_operational_body(section: str) -> str:
    safe_context = (
        "This working draft separates user-supplied facts from planning "
        "options. School leaders retain authority over every operational "
        "decision. The document is for internal review and has not been sent. "
        "Staff should check accessibility, safeguarding, room readiness, "
        "signage, attendance handling, privacy, emergency access, and record "
        "keeping before approval. Each owner should record completion status "
        "and raise unresolved dependencies for human decision. "
    )
    return (
        "# Parent-Teacher Conference Operations Draft\n\n"
        "> DRAFT - NOT SENT | Internal review only\n\n"
        "## Source-confirmed facts\n\n"
        "The conference is on Saturday from 8:30 a.m. to 12:30 p.m. in "
        "Rooms 1 to 6.\n\n"
        + section
        + "\n\n## Review controls\n\n"
        + safe_context * 3
    )


def test_live_writer_repairs_unsupported_decisions_before_acceptance() -> None:
    source = (
        "A parent-teacher conference will be held Saturday from 8:30 a.m. "
        "to 12:30 p.m. in Rooms 1 to 6. Prepare the school documents."
    )
    action = _action(source)
    bad = _long_operational_body(
        "## Coordinator briefing\n\n"
        "Staff report at 7:30 a.m., use WhatsApp, and file the report "
        "within two working days."
    )
    repaired = _long_operational_body(
        "## Proposed arrangements - subject to school approval\n\n"
        "Proposed: staff report at 7:30 a.m.\n\n"
        "Proposed communication option: WhatsApp, subject to approval.\n\n"
        "Proposed reporting target: within two working days."
    )
    llm = _SequencedLiveLLM([
        {"artifacts": {action.action_id: bad}},
        {"artifacts": {action.action_id: repaired}},
        {"pass": True, "unsupported_claims": []},
    ])
    result = ContentSynthesizer(chat_llm=llm).enrich_school_plan(
        [action], user_intent=source,
    )
    assert result["result"] == "synthesized_verified_action_bundle"
    assert result["attempt"] == 2
    assert action.metadata["content"] == repaired.strip()
    assert action.metadata["school_generation_validation"]["pass"] is True
    assert "EPISTEMIC STATUS RULE" in llm.calls[0]["system"]
    assert "unsupported_operational_time" in llm.calls[1]["user"]
    assert "Apply the same rule to operational planning" in llm.calls[2]["system"]


def test_unsupported_staff_assignment_and_facility_are_not_silent_facts() -> None:
    source = "Prepare an operations plan for a school visitor day."
    content = _body(
        "## Operations\n\n"
        "The senior assistant will lead registration.\n\n"
        "Use the school hall for the coordinator briefing."
    )
    issues = validate_school_markdown(_action(source), content, source)["grounding"]
    assert any(item.startswith("unsupported_operational_assignment") for item in issues)
    assert any(item.startswith("unsupported_operational_facility") for item in issues)


def test_nested_subsections_in_proposed_plan_keep_proposal_status() -> None:
    source = "Prepare an operations plan for a school visitor day."
    content = _body(
        "## Proposed arrangements - subject to school approval\n\n"
        "### Staffing\n\n"
        "The senior assistant will lead registration.\n\n"
        "### Venue\n\n"
        "Use the school hall at 7:45 a.m. with two coordinators."
    )
    issues = validate_school_markdown(_action(source), content, source)["grounding"]
    assert not any(item.startswith("unsupported_operational_") for item in issues)


def test_time_abbreviation_is_allowed_but_meridiem_conflict_is_rejected() -> None:
    source = "The briefing runs from 8:30 a.m. to 12:30 p.m."
    abbreviated = _body(
        "## Confirmed schedule\n\nBriefing starts at 8:30 and ends at 12:30."
    )
    abbreviated_issues = validate_school_markdown(
        _action(source), abbreviated, source,
    )["grounding"]
    assert not any(item.startswith("unsupported_operational_time") for item in abbreviated_issues)

    conflicting = _body(
        "## Confirmed schedule\n\nBriefing starts at 8:30 p.m."
    )
    conflicting_issues = validate_school_markdown(
        _action(source), conflicting, source,
    )["grounding"]
    assert any(item.startswith("unsupported_operational_time") for item in conflicting_issues)


def test_fast_bundle_requires_semantic_evidence_for_live_artifacts() -> None:
    action = _action("Prepare a school report from the supplied facts.")
    action.metadata["school_generation_validation"] = {
        "pass": True,
        "mode": "plan_level_action_id_mapping",
    }
    assert _verified_fast_school_bundle([action]) is False
    action.metadata["school_generation_validation"][
        "semantic_audit_passed"
    ] = True
    assert _verified_fast_school_bundle([action]) is False
    action.metadata["school_generation_validation"][
        "goal_alignment_passed"
    ] = True
    assert _verified_fast_school_bundle([action]) is True


def test_fast_bundle_rejects_verified_deterministic_role_fallback() -> None:
    action = _action("Prepare a school report from the supplied facts.")
    action.metadata["school_generation_validation"] = {
        "pass": True,
        "mode": "deterministic_response_pack_fallback",
    }
    assert _verified_fast_school_bundle([action]) is False


def test_fast_bundle_rejects_mixed_live_and_deterministic_artifacts() -> None:
    live = _action("Prepare a school report from the supplied facts.")
    live.metadata["school_generation_validation"] = {
        "pass": True,
        "mode": "plan_level_action_id_mapping",
        "semantic_audit_passed": True,
        "goal_alignment_passed": True,
    }
    fallback = _action("Prepare a parent draft from the supplied facts.")
    fallback.action_id = "parent-fallback"
    fallback.metadata["school_generation_validation"] = {
        "pass": True,
        "mode": "deterministic_response_pack_fallback",
    }
    assert _verified_fast_school_bundle([live, fallback]) is False


def test_plan_level_bundle_exception_is_terminal_not_retryable_per_file() -> None:
    action = _action("Prepare a school report from the supplied facts.")
    action.metadata.pop("school_generation_validation", None)

    _hold_failed_school_bundle([action], reason="provider raised")

    assert action.metadata["synthesis_skip"] is True
    assert action.metadata["school_generation_failed"] is True
    assert action.metadata["school_generation_validation"]["pass"] is False
    assert action.metadata["school_generation_validation"]["mode"] == (
        "plan_level_bundle_exception"
    )


def test_fast_path_uses_one_bounded_bundle_repair_without_per_file_fanout() -> None:
    class BadBundleLLM:
        backend = "deepseek"

        def __init__(self) -> None:
            self.bundle_calls = 0
            self.per_file_calls = 0

        def chat_json(self, system, user, *, max_tokens=1500):
            self.bundle_calls += 1
            return {"artifacts": {"epistemic_plan": "# Tiny draft"}}

        def chat(self, system, user, *, max_tokens=1500):
            self.per_file_calls += 1
            return ""

    llm = BadBundleLLM()
    synth = ContentSynthesizer(chat_llm=llm)
    synth.live_fast_path = True
    action = _action(
        "A student slipped during recess. Prepare an internal school report."
    )
    result = synth.enrich_school_plan(
        [action], user_intent=action.metadata["source_request"]
    )
    assert llm.bundle_calls == 2
    assert llm.per_file_calls == 0
    assert result["fallback_action_ids"] == []
    assert action.metadata["school_generation_validation"]["pass"] is True
    assert action.metadata["school_generation_validation"]["mode"].startswith(
        "deterministic_"
    )


def test_fast_path_goal_miss_uses_honest_local_fallback_without_fanout() -> None:
    action = _action(
        "Prepare a school operations record from the facts supplied."
    )
    action.metadata["requested_languages"] = []
    live_body = _school_response_pack_safe_fallback(
        action, action.metadata["source_request"]
    )
    llm = _SequencedLiveLLM([
        {"artifacts": {action.action_id: live_body}},
        {
            "pass": True,
            "unsupported_claims": [],
            "goal_alignment_passed": False,
            "missing_obligations": [{
                "obligation": "requested record",
                "reason": "The live draft did not fulfil the request.",
            }],
            "irrelevant_artifacts": [],
            "audience_issues": [],
            "language_issues": [],
            "score": 40,
            "reason": "The live draft missed the requested goal.",
        },
        {"artifacts": {action.action_id: live_body}},
        {
            "pass": True,
            "unsupported_claims": [],
            "goal_alignment_passed": False,
            "missing_obligations": [{
                "obligation": "requested record",
                "reason": "The repaired draft still did not fulfil the request.",
            }],
            "irrelevant_artifacts": [],
            "audience_issues": [],
            "language_issues": [],
            "score": 40,
            "reason": "The repaired live draft still missed the requested goal.",
        },
    ])
    synth = ContentSynthesizer(chat_llm=llm)
    synth.live_fast_path = True

    result = synth.enrich_school_plan(
        [action], user_intent=action.metadata["source_request"]
    )

    assert len(llm.calls) == 4
    assert result["fallback_action_ids"] == []
    assert action.metadata["synthesis_skip"] is True
    validation = action.metadata["school_generation_validation"]
    assert validation["pass"] is True
    assert validation["mode"].startswith("deterministic_")


def test_fast_path_merges_bundle_audit_without_second_llm_judge() -> None:
    class VerifierProbe:
        def __init__(self) -> None:
            self.judge_calls = 0

        def verify(self, **_kwargs):
            return {
                "enabled": True,
                "pass": True,
                "summary": "all checks passed",
                "checks": [{"name": "response_pack_coverage", "pass": True}],
            }

        def llm_judge(self, **_kwargs):
            self.judge_calls += 1
            raise AssertionError("fast path must not call a duplicate judge")

    action = _action("Prepare a school report from the supplied facts.")
    action.metadata["school_generation_validation"] = {
        "pass": True,
        "mode": "plan_level_action_id_mapping",
        "semantic_audit_passed": True,
        "goal_alignment_passed": True,
    }
    plan = CandidatePlan(
        task_id="task_fast_verify",
        planner_id="smart_mock",
        planning_mode="direct",
        actions=[action],
    )
    result = SimpleNamespace(
        executions=[],
        decisions=[],
        pre_assessment=SimpleNamespace(task_category="school_admin"),
        used_adapted_skill=False,
        adapted_target_tool=None,
        verification=None,
    )
    runtime = object.__new__(Runtime)
    runtime.verifier = VerifierProbe()
    runtime.live_fast_path = True
    runtime._emit = lambda *_args, **_kwargs: None
    runtime._run_verification(_envelope(), plan, result)
    assert runtime.verifier.judge_calls == 0
    assert result.verification["pass"] is True
    assert result.verification["verification_grade"] == "goal_complete"
    assert result.verification["judge"]["skipped_reason"] == (
        "merged_school_bundle_verification"
    )
