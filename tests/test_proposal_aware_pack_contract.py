"""Focused TDD contracts for proposal-aware school response packs.

These tests intentionally separate operator choice from policy necessities:
semantic suggestions should be useful defaults, while source-grounded safety
and privacy controls remain mandatory and cannot be deselected.
"""
from __future__ import annotations

from pathlib import Path

from teow_agl.modules.module_school_situation import (
    SchoolSituationCompiler,
    _explicitly_no_unmet_emergency,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = (
    ROOT / "configs" / "domain_packs" / "public_school" /
    "situation_response_policy.json"
)


def _semantics(
    situation: dict,
    *,
    case_relation: str = "new_case",
    requested_action: str = "recommend",
) -> dict:
    return {
        "checked": True,
        "school_domain": True,
        "case_relation": case_relation,
        "school_area": "other",
        "requested_action": requested_action,
        "audience": "internal",
        "confidence": 0.98,
        "data_use_concepts": [],
        "situation": situation,
        "source": "test_semantic_llm",
    }


def _staff_situation(**overrides) -> dict:
    situation = {
        "family": "staffing_hr",
        "phase": "post_incident",
        "severity": "low",
        "signals": [],
        "affected_people_types": ["staff"],
        "stakeholder_candidates": ["school_leadership"],
        "case_summary": "A staff disagreement was reported.",
        "known_facts": [],
        "unknowns": [],
    }
    situation.update(overrides)
    return situation


def _deliverable(compiled: dict, role: str) -> dict:
    return next(
        item for item in compiled["response_pack"]["deliverables"]
        if item.get("artifact_role") == role
    )


def _required_roles(compiled: dict) -> set[str]:
    return {
        str(item.get("artifact_role"))
        for item in compiled["response_pack"]["deliverables"]
        if item.get("requirement") == "required"
    }


def test_authoritative_no_is_reapplied_after_followup_context_merge() -> None:
    """A resolved danger answer must beat stale severity/signals in the parent."""
    prior_case_context = {
        "situation": {
            **_staff_situation(
                phase="ongoing",
                severity="critical",
                signals=[
                    "active_danger",
                    "external_help_may_be_required",
                    "possible_regulatory_trigger",
                ],
                stakeholder_candidates=[
                    "school_leadership",
                    "malaysia_emergency_services_999",
                ],
                case_summary="Two teachers nearly fought in the office.",
            ),
            "source_request": "Two teachers nearly fought in the office.",
            "requested_deliverables": [],
            "requested_outputs": [],
            "explicit_external_actions": [],
        },
    }
    compiler = SchoolSituationCompiler(POLICY)

    compiled = compiler.compile(
        "They are separated and safe. Prepare the appropriate follow-up.",
        _semantics(
            _staff_situation(
                phase="follow_up",
                case_summary="The staff members are separated and safe.",
            ),
            case_relation="follow_up",
        ),
        prior_case_context=prior_case_context,
        clarification_answers={"immediate_danger": "No"},
    )

    assert "active_danger" not in set(compiled["situation"]["signals"])
    assert compiled["situation"]["phase"] != "ongoing"


def test_staff_only_active_conflict_keeps_safety_controls_without_student_rollcall() -> None:
    """Active staff danger needs controls, not a pupil-accountability document."""
    compiled = SchoolSituationCompiler(POLICY).compile(
        "Two teachers are still fighting in the staff office.",
        _semantics(_staff_situation(
            phase="ongoing",
            severity="critical",
            signals=["active_danger"],
            case_summary="Two teachers are still fighting in the staff office.",
        )),
        clarification_answers={"immediate_danger": "Yes"},
        selected_deliverable_ids=[],
    )

    assert compiled["situation"]["affected_people_types"] == ["staff"]
    required = _required_roles(compiled)
    assert {
        "internal_incident_report",
        "site_safety_checklist",
        "emergency_contact_script",
    }.issubset(required)
    assert "student_accountability_checklist" not in required


def test_semantic_recommendation_is_default_selected_but_operator_optional() -> None:
    """Model-proposed coverage is a useful default, not a mandatory output."""
    situation = _staff_situation(
        requested_outputs=[{
            "artifact_role": "post_incident_review",
            "purpose": "Review the response and recommend proportionate next steps.",
        }],
    )
    compiler = SchoolSituationCompiler(POLICY)
    source = "The staff disagreement is over. What should the school do next?"

    proposed = compiler.compile(source, _semantics(situation))
    proposal = _deliverable(proposed, "post_incident_review")
    assert proposal["selection_origin"] == "system_recommendation"
    assert proposal["requirement"] == "recommended"
    assert proposal["required"] is False
    assert proposal["selected"] is True

    deselected = compiler.compile(
        source,
        _semantics(situation),
        selected_deliverable_ids=[],
    )
    proposal = _deliverable(deselected, "post_incident_review")
    assert proposal["requirement"] == "recommended"
    assert proposal["required"] is False
    assert proposal["selected"] is False


def test_irrelevant_semantic_safety_files_are_not_default_selected() -> None:
    """A resolved staff matter must not inherit a generic emergency bundle."""
    situation = _staff_situation(
        requested_outputs=[
            {
                "artifact_role": "internal_incident_report",
                "purpose": "Record the staff incident and response.",
            },
            {
                "artifact_role": "emergency_contact_script",
                "purpose": "Contact emergency services.",
            },
            {
                "artifact_role": "student_accountability_checklist",
                "purpose": "Account for pupils.",
            },
        ],
    )
    compiled = SchoolSituationCompiler(POLICY).compile(
        "The two teachers are separated and safe. What should I do next?",
        _semantics(situation),
        clarification_answers={"immediate_danger": "No"},
    )

    internal = _deliverable(compiled, "internal_incident_report")
    emergency = _deliverable(compiled, "emergency_contact_script")
    pupil_rollcall = _deliverable(
        compiled, "student_accountability_checklist"
    )
    assert internal["selected"] is True
    assert emergency["requirement"] == "recommended"
    assert emergency["selected"] is False
    assert pupil_rollcall["requirement"] == "recommended"
    assert pupil_rollcall["selected"] is False


def test_hard_privacy_response_outputs_remain_required_and_selected() -> None:
    """An operator cannot deselect the minimum response to an actual data leak."""
    compiled = SchoolSituationCompiler(POLICY).compile(
        (
            "A spreadsheet containing pupil medical details was emailed to "
            "the wrong vendor. Prepare the internal response; do not send."
        ),
        _semantics({
            "family": "cyber_data",
            "phase": "just_occurred",
            "severity": "high",
            "signals": [
                "data_security_incident",
                "personal_data_involved",
                "evidence_preservation_needed",
            ],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["school_leadership"],
            "case_summary": "Pupil medical details were sent to a wrong vendor.",
            "known_facts": [],
            "unknowns": [],
        }),
        selected_deliverable_ids=[],
    )

    for role in {
        "cyber_incident_response",
        "evidence_preservation_log",
        "regulatory_notification_assessment",
    }:
        item = _deliverable(compiled, role)
        assert item["requirement"] == "required"
        assert item["required"] is True
        assert item["selected"] is True


def test_open_ended_staff_response_does_not_promote_model_notice_to_user_request() -> None:
    """A model suggestion cannot suppress the staff-incident record floor."""
    semantics = _semantics({
        "family": "discipline_behaviour",
        "phase": "just_occurred",
        "severity": "medium",
        "signals": [],
        "affected_people_types": ["staff"],
        "stakeholder_candidates": ["school_leadership", "school_staff"],
        "case_summary": "Two teachers had a loud argument.",
        "known_facts": [],
        "unknowns": [],
        "requested_outputs": [{
            "artifact_role": "staff_internal_notice",
            "label": "Internal response to staff argument",
            "purpose": "Inform staff and outline next steps.",
            "audience": "internal",
            "recipient_type": "school_staff",
            "explicit": True,
        }],
    })
    compiled = SchoolSituationCompiler(POLICY).compile(
        (
            "Two teachers had a loud argument in the staff office. They are "
            "now separated, no one is injured, and there is no immediate "
            "danger. Prepare the appropriate internal response."
        ),
        semantics,
    )

    incident = _deliverable(compiled, "internal_incident_report")
    notice = _deliverable(compiled, "staff_internal_notice")
    assert incident["requirement"] == "required"
    assert incident["selected"] is True
    assert notice["requirement"] == "recommended"
    assert notice["selection_origin"] == "system_recommendation"


def test_staff_conflict_normalises_inferred_duplicate_discipline_report() -> None:
    source = (
        "Two teachers had a loud argument in the staff office. They are now "
        "separated and no one is injured. Prepare the appropriate internal "
        "response. Draft only."
    )
    semantics = _semantics({
        "family": "discipline_behaviour",
        "phase": "just_occurred",
        "severity": "medium",
        "signals": [],
        "affected_people_types": ["staff"],
        "stakeholder_candidates": ["school_leadership"],
        "case_summary": source,
        "known_facts": [],
        "unknowns": [],
        "requested_outputs": [{
            "artifact_role": "discipline_investigation_report",
            "label": "Discipline investigation report",
            "explicit": False,
        }],
    })
    compiled = SchoolSituationCompiler(POLICY).compile(source, semantics)
    selected = {
        item.get("artifact_role")
        for item in compiled["response_pack"]["deliverables"]
        if item.get("selected") is True
    }
    assert selected == {"internal_incident_report"}


def test_provider_fallback_still_recognises_staff_only_conflict() -> None:
    source = (
        "Two teachers had a loud argument in the staff office. They are now "
        "separated, no one is injured, and there is no immediate danger. "
        "Prepare the appropriate internal response. Draft only."
    )
    semantics = {
        "checked": False,
        "school_domain": True,
        "case_relation": "new_case",
        "school_area": "other",
        "requested_action": "",
        "audience": "unknown",
        "confidence": 0.0,
        "data_use_concepts": [],
        "situation": {},
        "source": "fallback",
    }
    compiled = SchoolSituationCompiler(POLICY).compile(source, semantics)
    assert compiled["situation"]["family"] == "staffing_hr"
    selected = {
        item.get("artifact_role")
        for item in compiled["response_pack"]["deliverables"]
        if item.get("selected") is True
    }
    assert selected == {"internal_incident_report"}


def test_semantic_broad_audience_cannot_create_false_red_for_stable_food_case() -> None:
    semantics = _semantics({
        "family": "health_medical",
        "phase": "just_occurred",
        "severity": "medium",
        "signals": ["injury_or_illness"],
        "affected_people_types": ["student"],
        "stakeholder_candidates": ["medical_services", "school_leadership"],
        "case_summary": "Eight pupils vomited after lunch.",
        "known_facts": [],
        "unknowns": [],
        "requested_outputs": [],
    })
    semantics["audience"] = "school_community"
    compiled = SchoolSituationCompiler(POLICY).compile(
        (
            "Eight pupils vomited after lunch from the school canteen. They "
            "are stable and there is no unmet medical emergency now. Prepare "
            "the appropriate school response. Draft only; contact no one."
        ),
        semantics,
    )

    assert compiled["response_pack"]["critical_question"] is None
    assert compiled["response_pack"]["input_governance"]["decision"] == (
        "NO_OVERRIDE"
    )
    selected = {
        item.get("artifact_role")
        for item in compiled["response_pack"]["deliverables"]
        if item.get("kind") == "artifact" and item.get("selected") is True
    }
    assert {
        "food_safety_response", "internal_incident_report",
        "private_parent_notice",
    } <= selected
    assert "school_parent_notice" not in selected
    assert "medical_handover_script" not in selected


def test_safe_bus_breakdown_defaults_family_draft_but_not_provider_message() -> None:
    semantics = _semantics({
        "family": "transport_travel",
        "phase": "just_occurred",
        "severity": "medium",
        "signals": ["service_disruption"],
        "affected_people_types": [],
        "stakeholder_candidates": ["guardian", "transport_provider"],
        "case_summary": "The school bus broke down; pupils are safe.",
        "known_facts": [],
        "unknowns": [],
        "requested_outputs": [],
    })
    semantics["audience"] = "school_community"
    compiled = SchoolSituationCompiler(POLICY).compile(
        (
            "The school bus broke down on the way home. All pupils are safe "
            "and supervised. Prepare the appropriate response; send nothing."
        ),
        semantics,
    )

    assert _deliverable(compiled, "transport_response_plan")["selected"] is True
    parent = _deliverable(compiled, "private_parent_notice")
    provider = _deliverable(compiled, "external_stakeholder_message")
    assert parent["requirement"] == "recommended"
    assert parent["selected"] is True
    assert provider["requirement"] == "recommended"
    assert provider["selected"] is False


def test_no_emergency_resolution_requires_an_affirmative_statement() -> None:
    assert _explicitly_no_unmet_emergency(
        "There is no immediate danger now."
    ) is True
    assert _explicitly_no_unmet_emergency(
        "There is no unmet medical emergency now."
    ) is True
    for uncertain in (
        "We cannot confirm that there is no immediate danger.",
        "No immediate danger has been ruled out.",
        "Is there no immediate danger?",
    ):
        assert _explicitly_no_unmet_emergency(uncertain) is False


def test_uncertain_absence_of_students_does_not_remove_accountability_pack() -> None:
    source = (
        "There is an active gas leak. We cannot confirm that no students "
        "were present. Prepare the school response pack."
    )
    semantics = _semantics({
        "family": "facilities_environment",
        "phase": "ongoing",
        "severity": "critical",
        "signals": ["active_danger", "evacuation_accountability"],
        "affected_people_types": ["unknown"],
        "stakeholder_candidates": ["school_leadership"],
        "case_summary": source,
        "known_facts": [],
        "unknowns": [],
        "requested_outputs": [],
    })
    selected = {
        item.get("artifact_role")
        for item in SchoolSituationCompiler(POLICY).compile(
            source, semantics,
        )["response_pack"]["deliverables"]
        if item.get("selected") is True
    }
    assert "student_accountability_checklist" in selected
