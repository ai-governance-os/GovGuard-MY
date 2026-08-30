"""Deterministic acceptance matrix for proposal-aware school response packs.

These tests deliberately bypass the semantic provider.  Each case supplies a
closed, manually controlled situation so failures identify response-pack
orchestration regressions rather than model-classification drift.

The matrix asserts only the minimum useful pack and explicitly forbidden
selected artifacts.  A recommended artifact may remain unselected; a required
artifact must be selected.  Content-generation and live-provider retention are
covered separately by the batch semantic-audit tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.modules.module_school_situation import SchoolSituationCompiler


ROOT = Path(__file__).resolve().parents[1]
POLICY = (
    ROOT / "configs" / "domain_packs" / "public_school" /
    "situation_response_policy.json"
)
CATALOG = json.loads(POLICY.read_text(encoding="utf-8"))["artifact_catalog"]


def _requested_output(role: str, *, explicit: bool = False) -> dict:
    spec = CATALOG[role]
    return {
        "artifact_role": role,
        "label": spec["label"],
        "purpose": f"Prepare the appropriate {spec['label'].lower()}",
        "audience": spec["audience"],
        "recipient_type": spec["recipient_type"],
        "languages": ["en"],
        "explicit": explicit,
    }


def _semantics(
    *,
    family: str,
    phase: str,
    severity: str,
    signals: tuple[str, ...] = (),
    people: tuple[str, ...] = (),
    stakeholders: tuple[str, ...] = (),
    requested_outputs: tuple[dict, ...] = (),
    unknowns: tuple[dict, ...] = (),
    audience: str = "internal",
    data_use_concepts: tuple[str, ...] = (),
) -> dict:
    return {
        "checked": True,
        "school_domain": True,
        "case_relation": "new_case",
        "school_area": family,
        "requested_action": "prepare",
        "audience": audience,
        "confidence": 0.99,
        "data_use_concepts": list(data_use_concepts),
        "situation": {
            "family": family,
            "phase": phase,
            "severity": severity,
            "signals": list(signals),
            "affected_people_types": list(people),
            "stakeholder_candidates": list(stakeholders),
            "known_facts": [],
            "unknowns": list(unknowns),
            "requested_outputs": list(requested_outputs),
        },
        "source": "test_semantic_llm",
    }


def _artifact_rows(compiled: dict) -> list[dict]:
    return [
        item for item in compiled["response_pack"]["deliverables"]
        if item.get("kind") == "artifact"
    ]


def _roles(compiled: dict, *, requirement: str | None = None) -> set[str]:
    rows = _artifact_rows(compiled)
    if requirement is not None:
        rows = [item for item in rows if item.get("requirement") == requirement]
    return {str(item.get("artifact_role") or "") for item in rows}


def _selected_roles(compiled: dict) -> set[str]:
    return {
        str(item.get("artifact_role") or "")
        for item in _artifact_rows(compiled)
        if item.get("selected") is True
    }


CASES = [
    {
        "id": "staff_conflict_resolved",
        "text": (
            "Two teachers had a loud argument in the staff office. They are "
            "now separated, no one is injured, and there is no immediate "
            "danger. Prepare the appropriate internal response."
        ),
        "semantics": _semantics(
            family="staffing_hr", phase="just_occurred", severity="medium",
            people=("staff",), stakeholders=("school_leadership",),
        ),
        "required": {"internal_incident_report"},
        "recommended": set(),
        "forbidden": {
            "site_safety_checklist", "emergency_contact_script",
            "student_accountability_checklist", "private_parent_notice",
            "public_communication_draft", "duty_roster",
        },
    },
    {
        "id": "minor_pupil_injury",
        "text": (
            "A Year 3 pupil scraped her knee during PE. First aid was given "
            "and she has returned to class. Prepare what the school needs; "
            "do not send anything."
        ),
        "semantics": _semantics(
            family="health_medical", phase="just_occurred", severity="low",
            signals=("injury_or_illness", "minor_involved"),
            people=("student",), stakeholders=("guardian",),
        ),
        "required": {"internal_incident_report", "private_parent_notice"},
        "recommended": {"medical_handover_script"},
        "forbidden": {
            "site_safety_checklist", "emergency_contact_script",
            "student_accountability_checklist", "public_communication_draft",
        },
    },
    {
        "id": "facility_ceiling_damage",
        "text": (
            "After heavy rain, part of the ceiling in a Year 4 classroom fell. "
            "The room has been closed and no injuries are confirmed. Prepare "
            "the school response package."
        ),
        "semantics": _semantics(
            family="facilities_environment", phase="just_occurred",
            severity="medium", signals=("service_disruption",),
            people=("student", "staff"),
            stakeholders=("school_leadership",),
        ),
        "required": {"internal_incident_report", "site_safety_checklist"},
        "recommended": {"staff_internal_notice"},
        "forbidden": {
            "emergency_contact_script", "student_accountability_checklist",
            "private_parent_notice", "public_communication_draft",
        },
    },
    {
        "id": "confidential_safeguarding_report",
        "text": (
            "A parent reports that a teacher may have hit a pupil at school. "
            "Keep the matter confidential and prepare what school leadership "
            "needs. Do not contact the accused."
        ),
        "semantics": _semantics(
            family="safeguarding_welfare", phase="just_occurred",
            severity="high",
            signals=(
                "safeguarding_concern", "minor_involved",
                "evidence_preservation_needed", "possible_regulatory_trigger",
            ),
            people=("student", "staff"),
            stakeholders=("guardian", "school_leadership"),
            data_use_concepts=("student_sensitive_data",),
        ),
        "required": {
            "internal_incident_report", "safeguarding_action_plan",
            "evidence_preservation_log",
        },
        "recommended": {
            "regulatory_notification_assessment", "post_incident_review",
        },
        "forbidden": {
            "staff_internal_notice", "public_communication_draft",
            "school_parent_notice", "private_parent_notice",
            "emergency_contact_script", "site_safety_checklist",
        },
    },
    {
        "id": "routine_marks_deadline",
        "text": (
            "Teachers must submit monthly assessment marks by 25 October. "
            "Prepare the appropriate internal communication; do not send it."
        ),
        "semantics": _semantics(
            family="general_school_admin", phase="planned", severity="low",
            people=("staff",), stakeholders=("school_staff",),
            requested_outputs=(_requested_output("staff_internal_notice"),),
        ),
        "required": {"staff_internal_notice"},
        "recommended": set(),
        "forbidden": {
            "internal_incident_report", "evidence_preservation_log",
            "site_safety_checklist", "emergency_contact_script",
            "private_parent_notice", "public_communication_draft",
            "user_titled_document",
        },
    },
    {
        "id": "privacy_safe_public_rumour_response",
        "text": (
            "A false rumour on Facebook says a pupil died at school. Prepare "
            "the appropriate school response without naming any child. Do not "
            "publish it yet."
        ),
        "semantics": _semantics(
            family="communications_reputation", phase="ongoing",
            severity="medium", signals=("public_interest", "minor_involved"),
            people=("student",), stakeholders=("public_media",),
            audience="public",
        ),
        "required": {"public_communication_draft"},
        "recommended": set(),
        "forbidden": {
            "private_parent_notice", "school_parent_notice",
            "emergency_contact_script", "site_safety_checklist",
            "student_accountability_checklist",
        },
    },
    {
        "id": "missing_pupil_excursion",
        "text": (
            "A Year 2 pupil cannot be found after an off-campus school "
            "excursion. The bus returned without her. Prepare the immediate "
            "response pack."
        ),
        "semantics": _semantics(
            family="safeguarding_welfare", phase="ongoing",
            severity="critical",
            signals=(
                "person_missing", "minor_involved", "safeguarding_concern",
                "evacuation_accountability", "external_help_may_be_required",
                "possible_regulatory_trigger",
            ),
            people=("student",),
            stakeholders=("guardian", "school_leadership"),
        ),
        "required": {
            "internal_incident_report", "student_accountability_checklist",
            "site_safety_checklist", "emergency_contact_script",
            "private_parent_notice", "safeguarding_action_plan",
        },
        "recommended": {
            "evidence_preservation_log", "regulatory_notification_assessment",
            "post_incident_review",
        },
        "forbidden": {
            "public_communication_draft", "food_safety_response",
            "finance_procurement_memo",
        },
    },
    {
        "id": "canteen_food_illness",
        "text": (
            "Eight pupils started vomiting after lunch from the school canteen. "
            "Prepare the response pack and do not contact anyone yet."
        ),
        "semantics": _semantics(
            family="food_hygiene", phase="just_occurred", severity="high",
            signals=(
                "food_water_exposure", "injury_or_illness",
                "minor_involved", "possible_regulatory_trigger",
            ),
            people=("student",),
            stakeholders=("guardian", "medical_services", "school_leadership"),
            unknowns=({"fact_id": "danger_still_present", "impact": "life_safety"},),
        ),
        "required": {
            "food_safety_response", "internal_incident_report",
            "private_parent_notice", "medical_handover_script",
        },
        "recommended": {
            "regulatory_notification_assessment", "post_incident_review",
        },
        "forbidden": {
            "public_communication_draft", "transport_response_plan",
            "finance_procurement_memo", "student_accountability_checklist",
        },
    },
    {
        "id": "safe_bus_breakdown",
        "text": (
            "The school bus broke down on the way home. All pupils are safe "
            "and supervised. Prepare the appropriate response; do not send "
            "anything."
        ),
        "semantics": _semantics(
            family="transport_travel", phase="ongoing", severity="medium",
            signals=("transport_operation", "guardian_notification_relevant"),
            people=("student",), stakeholders=("guardian", "transport_provider"),
        ),
        "required": {"transport_response_plan"},
        "recommended": {
            "private_parent_notice", "external_stakeholder_message",
        },
        "forbidden": {
            "internal_incident_report", "site_safety_checklist",
            "emergency_contact_script", "student_accountability_checklist",
            "public_communication_draft",
        },
    },
    {
        "id": "unapproved_procurement_comparison",
        "text": (
            "The school needs to choose between two quotations for replacing "
            "damaged classroom fans, but the budget approval is not yet "
            "confirmed. Prepare the appropriate decision support."
        ),
        "semantics": _semantics(
            family="finance_procurement", phase="planned", severity="low",
            signals=("financial_value_involved",),
            people=("staff",), stakeholders=("school_leadership",),
        ),
        "required": {"finance_procurement_memo"},
        "recommended": set(),
        "forbidden": {
            "internal_incident_report", "public_communication_draft",
            "site_safety_checklist", "emergency_contact_script",
            "private_parent_notice",
        },
    },
    {
        "id": "planned_water_maintenance",
        "text": (
            "A water-supply maintenance shutdown is planned for next Tuesday. "
            "One classroom block will be closed for two hours and families "
            "need to know. Prepare the appropriate communication; do not send."
        ),
        "semantics": _semantics(
            family="facilities_environment", phase="planned", severity="low",
            signals=("service_disruption",), people=("student", "staff"),
            stakeholders=("guardian", "school_staff"),
            requested_outputs=(_requested_output("school_parent_notice"),),
        ),
        "required": {"school_parent_notice"},
        "recommended": {"site_safety_checklist", "staff_internal_notice"},
        "forbidden": {
            "internal_incident_report", "evidence_preservation_log",
            "emergency_contact_script", "student_accountability_checklist",
            "public_communication_draft",
        },
    },
    {
        "id": "public_medical_privacy_attack",
        "text": (
            "A Year 5 pupil named Amir has asthma and fainted during PE. "
            "Prepare a Facebook post naming Amir and explaining his medical "
            "condition so parents can see the school handled it responsibly."
        ),
        "semantics": _semantics(
            family="health_medical", phase="just_occurred", severity="high",
            signals=(
                "injury_or_illness", "minor_involved",
                "possible_regulatory_trigger",
            ),
            people=("student",),
            stakeholders=("guardian", "public_media", "school_leadership"),
            requested_outputs=(
                _requested_output("public_communication_draft", explicit=True),
            ),
            audience="public",
            data_use_concepts=(
                "student_sensitive_data", "health_or_discipline",
            ),
        ),
        "required": {
            "public_communication_draft", "internal_incident_report",
            "private_parent_notice",
        },
        "recommended": {
            "regulatory_notification_assessment", "post_incident_review",
        },
        "forbidden": {
            "school_parent_notice", "staff_internal_notice",
            "site_safety_checklist", "emergency_contact_script",
            "student_accountability_checklist", "medical_handover_script",
        },
        "governance": "RED",
    },
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_proposal_aware_pack_matrix(case: dict) -> None:
    compiled = SchoolSituationCompiler(POLICY).compile(
        case["text"], case["semantics"],
    )
    required = _roles(compiled, requirement="required")
    recommended = _roles(compiled, requirement="recommended")
    selected = _selected_roles(compiled)

    assert case["required"] <= required
    assert case["required"] <= selected
    assert case["recommended"] <= recommended
    assert not case["forbidden"].intersection(selected)
    if case.get("governance"):
        assert (
            compiled["response_pack"]["input_governance"]["decision"]
            == case["governance"]
        )


def _unclear_staff_conflict_semantics() -> dict:
    return _semantics(
        family="staffing_hr", phase="ongoing", severity="high",
        signals=("external_help_may_be_required",), people=("staff",),
        stakeholders=("school_leadership",),
        requested_outputs=(_requested_output("internal_incident_report"),),
        unknowns=({"fact_id": "danger_still_present", "impact": "life_safety"},),
    )


def test_staff_conflict_immediate_danger_yes_no_changes_only_safety_pack() -> None:
    text = (
        "Two teachers are arguing in a staff office after school and may "
        "become physical. No pupils are present. It is unknown whether there "
        "is immediate danger. Prepare the appropriate internal response."
    )
    compiler = SchoolSituationCompiler(POLICY)

    initial = compiler.compile(text, _unclear_staff_conflict_semantics())
    question = initial["response_pack"]["critical_question"]
    assert question and question["question_id"] == "immediate_danger"

    answered_no = compiler.compile(
        text, _unclear_staff_conflict_semantics(),
        clarification_answers={"immediate_danger": "No"},
    )
    assert answered_no["response_pack"]["critical_question"] is None
    assert "internal_incident_report" in _selected_roles(answered_no)
    assert not {
        "site_safety_checklist", "emergency_contact_script",
        "student_accountability_checklist",
    }.intersection(_selected_roles(answered_no))

    answered_yes = compiler.compile(
        text, _unclear_staff_conflict_semantics(),
        clarification_answers={"immediate_danger": "Yes"},
    )
    assert answered_yes["response_pack"]["critical_question"] is None
    assert {
        "internal_incident_report", "site_safety_checklist",
        "emergency_contact_script",
    } <= _selected_roles(answered_yes)
    assert "student_accountability_checklist" not in _selected_roles(answered_yes)

    answered_unknown = compiler.compile(
        text, _unclear_staff_conflict_semantics(),
        clarification_answers={"immediate_danger": "Unknown"},
    )
    assert answered_unknown["response_pack"]["critical_question"] is None


def test_food_incident_no_answer_keeps_core_pack_without_emergency_expansion() -> None:
    case = next(item for item in CASES if item["id"] == "canteen_food_illness")
    compiler = SchoolSituationCompiler(POLICY)
    initial = compiler.compile(case["text"], case["semantics"])
    assert (
        initial["response_pack"]["critical_question"]["question_id"]
        == "immediate_danger"
    )

    answered_no = compiler.compile(
        case["text"], case["semantics"],
        clarification_answers={"immediate_danger": "No"},
    )
    selected = _selected_roles(answered_no)
    assert {
        "food_safety_response", "internal_incident_report",
        "private_parent_notice",
    } <= selected
    assert "medical_handover_script" not in selected
    assert not {
        "site_safety_checklist", "emergency_contact_script",
        "student_accountability_checklist",
    }.intersection(selected)

    answered_yes = compiler.compile(
        case["text"], case["semantics"],
        clarification_answers={"immediate_danger": "Yes"},
    )
    assert {
        "site_safety_checklist", "emergency_contact_script",
        "student_accountability_checklist",
    } <= _selected_roles(answered_yes)
