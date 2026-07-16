from __future__ import annotations

import re
from pathlib import Path

import pytest

from teow_agl.modules.module_school_situation import SchoolSituationCompiler


POLICY = (
    Path(__file__).parents[1] / "configs" / "domain_packs" / "public_school"
    / "situation_response_policy.json"
)


def _semantic_payload(*, external=None) -> dict:
    return {
        "checked": True, "source": "school_semantic_llm+boundary_guard",
        "school_domain": True, "requested_action": "draft",
        "audience": "internal", "data_use_concepts": [],
        "situation": {
            # Deliberately adversarial high-impact model output.
            "family": "safety_emergency", "phase": "ongoing",
            "severity": "critical",
            "signals": [
                "active_danger", "injury_or_illness", "minor_involved",
                "external_help_may_be_required", "data_security_incident",
                "financial_value_involved", "possible_regulatory_trigger",
                "evacuation_accountability",
            ],
            "affected_people_types": ["student"],
            "stakeholder_candidates": [
                "medical_services", "guardian",
                "malaysia_emergency_services_999", "education_authority",
            ],
            "known_facts": [], "unknowns": [],
            "requested_outputs": [{
                "artifact_role": "school_document", "audience": "internal",
            }],
            "explicit_external_actions": list(external or []),
        },
    }


def _compile(text: str, *, external=None) -> dict:
    return SchoolSituationCompiler(POLICY).compile(
        text, _semantic_payload(external=external),
    )


@pytest.mark.parametrize("text", [
    "Draft an agenda for the routine staff meeting next Monday.",
    "Draft a recycling-day notice for parents.",
    "Prepare next week's class timetable.",
])
def test_model_cannot_invent_emergency_cyber_or_finance_pack(text):
    result = _compile(text)
    situation = result["situation"]
    assert situation["family"] == "general_school_admin"
    assert situation["severity"] not in {"high", "critical"}
    assert not set(situation["signals"]).intersection({
        "active_danger", "injury_or_illness", "data_security_incident",
        "financial_value_involved", "evacuation_accountability",
    })
    roles = {
        item.get("artifact_role")
        for item in result["response_pack"]["deliverables"]
    }
    assert not roles.intersection({
        "emergency_contact_script", "medical_handover_script",
        "cyber_incident_response", "finance_procurement_memo",
    })


def test_real_snake_injury_survives_semantic_ceiling_via_source_floor():
    result = _compile(
        "A snake bit a student near the canteen and may still be on campus."
    )
    signals = set(result["situation"]["signals"])
    assert {"injury_or_illness", "minor_involved", "active_danger"}.issubset(signals)
    assert result["situation"]["severity"] == "critical"


def test_malay_snake_bite_and_loose_hazard_survive_source_ceiling():
    result = _compile(
        "Seekor ular mematuk seorang murid berhampiran kantin dan mungkin "
        "masih berkeliaran di sekolah. Sediakan pakej tindak balas, jangan "
        "hantar apa-apa."
    )
    signals = set(result["situation"]["signals"])
    assert {"injury_or_illness", "minor_involved", "active_danger"}.issubset(
        signals
    )
    assert result["situation"]["family"] == "safety_emergency"
    assert result["situation"]["severity"] == "critical"
    artifact_items = [
        item for item in result["response_pack"]["deliverables"]
        if item.get("selected") and item.get("kind") == "artifact"
    ]
    assert artifact_items
    assert all(item.get("requested_languages") == ["ms"] for item in artifact_items)
    assert not any(
        item["kind"] == "external_action"
        for item in result["response_pack"]["deliverables"]
    )


def test_single_malay_fallback_keeps_one_h1_and_malay_section():
    from teow_agl.models import CandidateAction
    from teow_agl.modules.module_102b_synthesizer import (
        _school_response_pack_safe_fallback,
    )
    action = CandidateAction(
        action_id="ms-emergency", tool="fs", operation="save_under_outputs",
        target="emergency_contact_script.md", purpose="Sediakan skrip",
        metadata={
            "artifact_role": "emergency_contact_script",
            "audience": "external_agency", "requested_languages": ["ms"],
            "school_known_facts": [], "school_unknowns": [],
        },
    )
    body = _school_response_pack_safe_fallback(
        action, "Seekor ular masih berkeliaran di sekolah.",
    )
    assert len(re.findall(r"(?m)^#\s+", body)) == 1
    assert "## Bahasa Melayu" in body
    assert "DRAF - BELUM DIHANTAR" in body


def test_malay_fallback_localises_official_source_note_and_cover():
    from teow_agl.models import CandidateAction
    from teow_agl.modules.module_102b_synthesizer import (
        _school_response_pack_safe_fallback,
    )
    from teow_agl.modules.module_school_artifact_guard import (
        school_cover_message,
    )
    source = (
        "Seekor ular masih berkeliaran di sekolah. Sediakan skrip BOMBA dan "
        "jangan hantar kepada sesiapa."
    )
    action = CandidateAction(
        action_id="ms-official", tool="fs", operation="save_under_outputs",
        target="fire_rescue_contact_script.md", purpose="Sediakan skrip",
        metadata={
            "artifact_role": "fire_rescue_contact_script",
            "audience": "external_agency", "requested_languages": ["ms"],
            "source_policy": "official_verification_required",
            "school_known_facts": [], "school_unknowns": [],
        },
    )
    body = _school_response_pack_safe_fallback(action, source)
    assert "Semakan sumber rasmi" in body
    assert "Official-source check" not in body
    cover = school_cover_message(source, ["fire_rescue_contact_script.md"])
    assert "Tugasan tadbir urus" in cover
    assert "Tiada kandungan telah dihantar" in cover


def test_specific_injury_parent_draft_cannot_expand_to_school_community():
    semantics = _semantic_payload()
    semantics["situation"]["requested_outputs"] = [{
        "artifact_role": "school_parent_notice",
        "audience": "school_community",
        "recipient_type": "guardian",
        "languages": ["ms"],
    }]
    result = SchoolSituationCompiler(POLICY).compile(
        "Seekor ular mematuk seorang murid. Sediakan draf ibu bapa; jangan "
        "hantar kepada sesiapa.",
        semantics,
    )
    selected = [
        item["artifact_role"]
        for item in result["response_pack"]["deliverables"]
        if item.get("selected") and item.get("kind") == "artifact"
    ]
    assert "private_parent_notice" in selected
    assert "school_parent_notice" not in selected
    assert result["response_pack"]["input_governance"]["decision"] != "RED"


@pytest.mark.parametrize("text", [
    "A motorbike knocked over a Year 3 pupil at the gate.",
    "A student was hit by a motorcycle outside the school gate.",
    "A school van ran over a Year 4 student near the entrance.",
])
def test_vehicle_impact_synonyms_preserve_real_student_injury(text):
    result = _compile(text)
    signals = set(result["situation"]["signals"])
    assert {"injury_or_illness", "minor_involved"}.issubset(signals)
    assert result["situation"]["family"] in {
        "safety_emergency", "health_medical",
    }
    assert result["situation"]["severity"] in {"high", "critical"}


def test_unknown_immediate_danger_is_not_promoted_to_confirmed_active_danger():
    semantics = _semantic_payload()
    semantics["situation"]["unknowns"] = [
        {"fact_id": "danger_still_present", "impact": "life_safety"},
    ]
    result = SchoolSituationCompiler(POLICY).compile(
        "A motorbike knocked over a Year 3 pupil at the gate. We do not know "
        "whether there is still immediate danger.",
        semantics,
    )
    assert "active_danger" not in set(result["situation"]["signals"])
    assert result["response_pack"]["critical_question"]["question_id"] == (
        "immediate_danger"
    )


def test_one_captured_snake_does_not_hide_second_loose_snake():
    result = _compile(
        "The first snake was captured, but a second snake may still be loose "
        "near the canteen and one pupil was bitten."
    )
    assert "active_danger" in set(result["situation"]["signals"])
    assert result["situation"]["severity"] == "critical"


@pytest.mark.parametrize("student_label", [
    "Year 4 student", "Form 1 pupil", "10-year-old child",
])
def test_school_level_student_labels_preserve_real_injury(student_label):
    result = _compile(
        f"A snake bit a {student_label} and may still be loose on campus."
    )
    signals = set(result["situation"]["signals"])
    assert {"injury_or_illness", "minor_involved", "active_danger"}.issubset(
        signals
    )
    roles = {
        item.get("artifact_role")
        for item in result["response_pack"]["deliverables"]
        if item.get("selected")
    }
    assert {
        "internal_incident_report", "private_parent_notice",
        "medical_handover_script",
    }.issubset(roles)


def test_real_data_breach_survives_semantic_ceiling_via_source_floor():
    result = _compile(
        "A data breach occurred and student data was exposed. Prepare the response."
    )
    assert "data_security_incident" in set(result["situation"]["signals"])
    assert result["situation"]["family"] == "cyber_data"


@pytest.mark.parametrize("text", [
    "Create the canteen allergy menu for next week.",
    "Add food-allergy items to the staff meeting agenda.",
    "Draft an anti-bullying policy agenda for staff review.",
    "Prepare a cyber-awareness agenda for the staff meeting.",
    "Sediakan agenda latihan kecemasan ular untuk minggu depan.",
    "为下周的蛇患应急演习准备一份会议议程。",
])
def test_policy_menu_and_agenda_topics_cannot_become_live_incidents(text):
    result = _compile(text)
    signals = set(result["situation"]["signals"])
    assert not signals.intersection({
        "active_danger", "injury_or_illness", "food_water_exposure",
        "safeguarding_concern", "data_security_incident",
        "guardian_notification_relevant",
    })
    roles = {
        item.get("artifact_role")
        for item in result["response_pack"]["deliverables"]
        if item.get("selected")
    }
    assert not roles.intersection({
        "private_parent_notice", "safeguarding_action_plan",
        "cyber_incident_response", "emergency_contact_script",
    })


@pytest.mark.parametrize("external", [
    ["guardian"], ["public_media"], ["education_authority"],
])
def test_semantic_model_cannot_inject_external_action_into_draft_only_text(external):
    result = _compile(
        "Draft a private notice for Ali's parent. Do not send anything.",
        external=external,
    )
    assert result["situation"]["explicit_external_actions"] == []
    assert not any(
        item["kind"] == "external_action"
        for item in result["response_pack"]["deliverables"]
    )


@pytest.mark.parametrize(("text", "recipient"), [
    ("Draft the notice, then contact Ali's parent.", "guardian"),
    ("Submit the report to the District Education Office.", "education_authority"),
    ("Publish this notice on Facebook.", "public_media"),
])
def test_explicit_source_release_still_creates_exact_human_gate(text, recipient):
    result = _compile(text, external=[])
    assert result["situation"]["explicit_external_actions"] == [recipient]
    gates = [
        item for item in result["response_pack"]["deliverables"]
        if item["kind"] == "external_action"
    ]
    assert len(gates) == 1
    assert gates[0]["recipient_type"] == recipient
