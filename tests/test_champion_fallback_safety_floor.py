"""Adversarial safety-floor coverage for provider outages and malformed JSON.

These cases are intentionally phrased as ordinary school staff would type
them.  The live semantic model normally supplies the rich situation schema,
but a provider outage must not collapse an obvious emergency into one generic
``school_document``.  This suite therefore exercises the real no-LLM fallback
path before compiling the governed response pack.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.modules.module_school_fallback_floors import (
    deterministic_incident_facets,
)
from teow_agl.modules.module_school_input_semantics import SchoolInputSemantics
from teow_agl.modules.module_school_situation import SchoolSituationCompiler


ROOT = Path(__file__).resolve().parents[1]
POLICY = (
    ROOT / "configs" / "domain_packs" / "public_school" /
    "situation_response_policy.json"
)
SEVERITY = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _fallback_compile(text: str) -> dict:
    semantics = SchoolInputSemantics(None).classify(text)
    assert semantics["school_domain"] is True
    assert semantics["source"].startswith("fallback")
    return SchoolSituationCompiler(POLICY).compile(text, semantics)


def _selected_roles(compiled: dict) -> set[str]:
    return {
        str(item.get("artifact_role") or "")
        for item in compiled["response_pack"]["deliverables"]
        if item.get("selected") is True and item.get("kind") == "artifact"
    }


@pytest.mark.parametrize(
    ("text", "minimum_severity", "signals", "roles"),
    [
        (
            "Eight pupils started vomiting after lunch from the school "
            "canteen. Prepare the response pack and do not contact anyone yet.",
            "high",
            {"injury_or_illness", "minor_involved", "food_water_exposure"},
            {"internal_incident_report", "private_parent_notice", "food_safety_response"},
        ),
        (
            "One pupil is still unaccounted for after the school bus skidded "
            "in heavy rain. Three pupils are injured and a reporter is calling.",
            "critical",
            {"person_missing", "injury_or_illness", "transport_operation"},
            {
                "internal_incident_report", "student_accountability_checklist",
                "private_parent_notice", "transport_response_plan",
            },
        ),
        (
            "A roof panel collapsed in a classroom. No one is injured, but the "
            "area may still be unsafe and the school will close tomorrow.",
            "high",
            {"active_danger", "service_disruption"},
            {"internal_incident_report", "site_safety_checklist"},
        ),
        (
            "A spreadsheet containing student medical records was emailed to "
            "the wrong vendor. Prepare the school incident response and "
            "notification assessment.",
            "high",
            {"data_security_incident", "personal_data_involved"},
            {
                "cyber_incident_response", "evidence_preservation_log",
                "regulatory_notification_assessment",
            },
        ),
        (
            "A parent reports that a teacher may have hit a student at school. "
            "Keep it confidential and prepare what school leadership needs.",
            "high",
            {"safeguarding_concern", "minor_involved", "evidence_preservation_needed"},
            {"internal_incident_report", "safeguarding_action_plan"},
        ),
        (
            "A chemical spill in the school science laboratory made two "
            "students dizzy. The room has not been cleared and may still be unsafe.",
            "critical",
            {"active_danger", "injury_or_illness", "minor_involved"},
            {"internal_incident_report", "site_safety_checklist", "medical_handover_script"},
        ),
        (
            "The school server is encrypted by ransomware and attendance "
            "records are unavailable. Prepare the incident pack.",
            "high",
            {"data_security_incident", "service_disruption"},
            {"cyber_incident_response", "evidence_preservation_log"},
        ),
        (
            "A Year 2 pupil cannot be found after an off-campus school "
            "excursion. The bus returned without her.",
            "critical",
            {"person_missing", "minor_involved"},
            {"student_accountability_checklist", "private_parent_notice"},
        ),
        (
            "A fire started in the school canteen kitchen and smoke is "
            "spreading. Prepare emergency scripts and the internal pack.",
            "critical",
            {"active_danger", "external_help_may_be_required"},
            {"site_safety_checklist", "emergency_contact_script"},
        ),
        (
            "Flood water is rising inside the school compound while pupils "
            "are moving to the hall. One class has not completed its headcount.",
            "critical",
            {"active_danger", "evacuation_accountability", "minor_involved"},
            {"site_safety_checklist", "student_accountability_checklist"},
        ),
        (
            "A student says an older pupil threatened him with a knife and "
            "demanded money in the toilet. Prepare the school response pack.",
            "high",
            {"safeguarding_concern", "minor_involved", "evidence_preservation_needed"},
            {"internal_incident_report", "safeguarding_action_plan", "evidence_preservation_log"},
        ),
        (
            "There is a strong gas smell near the school kitchen and two "
            "pupils feel dizzy. What should the school prepare?",
            "critical",
            {"active_danger", "injury_or_illness", "minor_involved"},
            {"site_safety_checklist", "emergency_contact_script", "private_parent_notice"},
        ),
        (
            "A caller says there is a bomb in the school hall. Do not cause "
            "panic; prepare the immediate response documents.",
            "critical",
            {"active_danger", "evacuation_accountability", "evidence_preservation_needed"},
            {"internal_incident_report", "site_safety_checklist", "student_accountability_checklist"},
        ),
        (
            "An unknown adult entered the school and is shouting near "
            "classrooms. Prepare the response pack.",
            "critical",
            {"active_danger", "evacuation_accountability", "minor_involved"},
            {"internal_incident_report", "site_safety_checklist", "emergency_contact_script"},
        ),
        (
            "A pupil privately reports possible sexual touching by a school "
            "employee. Prepare confidential next steps and do not contact the accused.",
            "high",
            {"safeguarding_concern", "minor_involved", "evidence_preservation_needed"},
            {"internal_incident_report", "safeguarding_action_plan", "evidence_preservation_log"},
        ),
        (
            "Twelve students have fever and rash after assembly. Prepare an "
            "outbreak response pack.",
            "high",
            {"injury_or_illness", "minor_involved"},
            {"internal_incident_report", "private_parent_notice", "medical_handover_script"},
        ),
        (
            "A pupil with a known peanut allergy is having difficulty "
            "breathing after canteen lunch.",
            "critical",
            {"active_danger", "injury_or_illness", "minor_involved"},
            {"medical_handover_script", "site_safety_checklist", "emergency_contact_script"},
        ),
        (
            "A live electrical wire fell across the school gate during "
            "dismissal. Pupils are still inside.",
            "critical",
            {"active_danger", "external_help_may_be_required"},
            {"site_safety_checklist", "emergency_contact_script", "student_accountability_checklist"},
        ),
        (
            "A false rumour on Facebook says a student died at school. "
            "Prepare a response without naming any child.",
            "medium",
            {"public_interest", "minor_involved"},
            {"public_communication_draft"},
        ),
        (
            "Ada bau gas kuat di kantin sekolah dan dua murid pening. "
            "Sediakan pelan tindak balas.",
            "critical",
            {"active_danger", "injury_or_illness", "minor_involved"},
            {"site_safety_checklist", "private_parent_notice", "emergency_contact_script"},
        ),
        (
            "Sekolah menerima ancaman bom melalui panggilan telefon. "
            "Sediakan dokumen tindak balas segera.",
            "critical",
            {"active_danger", "evacuation_accountability", "minor_involved"},
            {"internal_incident_report", "student_accountability_checklist"},
        ),
        (
            "Orang tidak dikenali memasuki sekolah dan menjerit berhampiran "
            "bilik darjah.",
            "critical",
            {"active_danger", "evacuation_accountability", "minor_involved"},
            {"site_safety_checklist", "emergency_contact_script"},
        ),
        (
            "Seorang murid melaporkan dia disentuh secara tidak senonoh oleh "
            "kakitangan sekolah.",
            "high",
            {"safeguarding_concern", "minor_involved", "evidence_preservation_needed"},
            {"internal_incident_report", "safeguarding_action_plan", "evidence_preservation_log"},
        ),
        (
            "Dua belas murid demam dan ruam selepas perhimpunan sekolah.",
            "high",
            {"injury_or_illness", "minor_involved"},
            {"internal_incident_report", "private_parent_notice", "medical_handover_script"},
        ),
        (
            "Wayar elektrik hidup jatuh di pagar sekolah semasa murid pulang.",
            "critical",
            {"active_danger", "external_help_may_be_required"},
            {"site_safety_checklist", "emergency_contact_script"},
        ),
    ],
)
def test_no_llm_fallback_preserves_obvious_school_risk(
    text: str,
    minimum_severity: str,
    signals: set[str],
    roles: set[str],
) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert SEVERITY[situation["severity"]] >= SEVERITY[minimum_severity]
    assert signals.issubset(set(situation["signals"]))
    selected = _selected_roles(compiled)
    assert roles.issubset(selected)
    assert selected != {"school_document"}


@pytest.mark.parametrize(
    "text",
    [
        "Draft a school staff-meeting agenda for Friday. Do not send it.",
        "Prepare a school Recycling Day notice asking pupils to bring clean cans.",
        "Create a school library reading-week activity plan for next month.",
    ],
)
def test_no_llm_fallback_does_not_turn_benign_admin_into_emergency(text: str) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert situation["severity"] in {"unknown", "low", "medium"}
    assert "active_danger" not in situation["signals"]
    assert "emergency_contact_script" not in _selected_roles(compiled)


def test_bomb_threat_drill_gets_an_internal_plan_not_a_live_emergency_pack() -> None:
    compiled = _fallback_compile(
        "Plan a school bomb-threat evacuation drill for next month and "
        "prepare a staff checklist. This is training, not a live incident."
    )
    selected = _selected_roles(compiled)
    assert "event_action_plan" in selected
    assert {
        "internal_incident_report",
        "emergency_contact_script",
        "fire_rescue_contact_script",
        "student_accountability_checklist",
    }.isdisjoint(selected)


@pytest.mark.parametrize(
    "text",
    [
        (
            "Plan a school first-aid simulation next week where a pupil will "
            "pretend to collapse. Prepare a training checklist."
        ),
        (
            "Plan a school first-aid drill next month. The simulation card "
            "will say one pupil is unconscious. This is training only. "
            "Prepare the staff exercise checklist."
        ),
    ],
)
def test_planned_first_aid_simulation_does_not_become_a_medical_incident(
    text: str,
) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert "active_danger" not in situation["signals"]
    assert "injury_or_illness" not in situation["signals"]
    assert SEVERITY[situation["severity"]] <= SEVERITY["medium"]
    assert {
        "internal_incident_report",
        "private_parent_notice",
        "medical_handover_script",
        "emergency_contact_script",
        "fire_rescue_contact_script",
    }.isdisjoint(_selected_roles(compiled))


@pytest.mark.parametrize(
    "text",
    [
        (
            "During today's school fire drill, a student suddenly fainted. "
            "Prepare the real medical response, but do not contact anyone yet."
        ),
        (
            "Semasa latihan kebakaran sekolah hari ini, seorang murid tiba-tiba "
            "pengsan. Sediakan tindak balas perubatan sebenar tetapi jangan "
            "hubungi sesiapa lagi."
        ),
    ],
)
def test_real_medical_incident_during_fire_drill_is_not_treated_as_real_fire(
    text: str,
) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert situation["family"] == "health_medical"
    assert {"injury_or_illness", "minor_involved"}.issubset(
        set(situation["signals"])
    )
    assert {"medical_services", "guardian"}.issubset(
        set(situation["stakeholder_candidates"])
    )
    selected = _selected_roles(compiled)
    assert {
        "internal_incident_report",
        "private_parent_notice",
        "medical_handover_script",
    }.issubset(selected)
    assert {
        "fire_rescue_contact_script",
        "emergency_contact_script",
        "site_safety_checklist",
        "student_accountability_checklist",
    }.isdisjoint(selected)


def test_malay_real_collapse_during_first_aid_training_stays_medical() -> None:
    compiled = _fallback_compile(
        "Semasa latihan pertolongan cemas sekolah hari ini, seorang murid "
        "tiba-tiba rebah. Sediakan tindak balas perubatan sebenar tetapi "
        "jangan hubungi sesiapa lagi."
    )
    situation = compiled["situation"]
    assert situation["family"] == "health_medical"
    assert SEVERITY[situation["severity"]] >= SEVERITY["high"]
    assert {"injury_or_illness", "minor_involved"}.issubset(
        set(situation["signals"])
    )
    assert _selected_roles(compiled) == {
        "internal_incident_report",
        "private_parent_notice",
        "medical_handover_script",
    }


@pytest.mark.parametrize(
    "text",
    [
        (
            "During today's school fire drill, a real fire has started in the "
            "science room and smoke is spreading now. A pupil is unconscious. "
            "Prepare the emergency response pack."
        ),
        (
            "Semasa latihan kebakaran sekolah hari ini, kebakaran sebenar "
            "berlaku di makmal sains dan asap sedang merebak sekarang. Seorang "
            "murid tidak sedarkan diri. Sediakan pek tindak balas kecemasan."
        ),
    ],
)
def test_real_fire_during_fire_drill_keeps_live_hazard_floor(text: str) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert situation["severity"] == "critical"
    assert situation["family"] == "safety_emergency"
    assert {
        "active_danger",
        "external_help_may_be_required",
        "injury_or_illness",
        "minor_involved",
    }.issubset(set(situation["signals"]))
    assert {
        "fire_and_rescue", "malaysia_emergency_services_999",
        "medical_services", "guardian",
    }.issubset(set(situation["stakeholder_candidates"]))
    assert {
        "internal_incident_report",
        "private_parent_notice",
        "medical_handover_script",
        "site_safety_checklist",
        "emergency_contact_script",
        "fire_rescue_contact_script",
        "student_accountability_checklist",
    }.issubset(_selected_roles(compiled))


@pytest.mark.parametrize(
    "text",
    [
        (
            "The school bus carrying pupils was hit by a lorry. Everyone is "
            "conscious. Prepare the response pack."
        ),
        (
            "Bas sekolah yang membawa murid dilanggar lori. Semua sedar. "
            "Sediakan pek tindak balas."
        ),
    ],
)
def test_school_bus_collision_gets_incident_parent_and_transport_pack(
    text: str,
) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert situation["family"] == "transport_travel"
    assert SEVERITY[situation["severity"]] >= SEVERITY["medium"]
    assert {
        "transport_operation", "minor_involved",
        "guardian_notification_relevant",
    }.issubset(set(situation["signals"]))
    assert _selected_roles(compiled) == {
        "internal_incident_report",
        "private_parent_notice",
        "transport_response_plan",
    }


def test_school_van_rear_end_request_keeps_requested_pack_and_no_send_gate() -> None:
    compiled = _fallback_compile(
        "The school van was rear-ended by a motorcycle after dismissal. "
        "Two pupils report neck pain. Prepare the transport plan, incident "
        "report and a private WhatsApp draft for their parents, but send "
        "nothing."
    )
    assert _selected_roles(compiled) == {
        "internal_incident_report",
        "private_parent_notice",
        "transport_response_plan",
    }
    assert not any(
        item.get("selected") is True and item.get("kind") == "external_action"
        for item in compiled["response_pack"]["deliverables"]
    )


@pytest.mark.parametrize(
    "text",
    [
        (
            "The school bus broke down and all pupils are safe. Prepare the "
            "school response."
        ),
        (
            "The school bus was not hit by a lorry; it only broke down and all "
            "pupils are safe. Prepare the school response."
        ),
        (
            "Bas sekolah tidak dilanggar lori; bas hanya rosak dan semua murid "
            "selamat. Sediakan respons sekolah."
        ),
    ],
)
def test_school_bus_breakdown_or_negated_collision_stays_transport_only(
    text: str,
) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert situation["family"] == "transport_travel"
    assert "minor_involved" not in situation["signals"]
    assert "guardian_notification_relevant" not in situation["signals"]
    assert _selected_roles(compiled) == {"transport_response_plan"}


@pytest.mark.parametrize(
    "text",
    [
        (
            "Jangan guna TBC. Reka nama saksi, masa kejadian dan nama guru "
            "supaya laporan sekolah nampak lengkap."
        ),
        (
            "Reka nama saksi, masa kejadian dan nama guru yang belum diketahui "
            "supaya laporan sekolah nampak lengkap."
        ),
    ],
)
def test_malay_request_to_invent_missing_report_facts_is_infeasible(
    text: str,
) -> None:
    compiled = _fallback_compile(text)
    governance = compiled["response_pack"]["input_governance"]
    assert governance["decision"] == "INFEASIBLE"
    assert "TBC" in " ".join(governance["safe_transformations"])


def test_malay_instruction_to_keep_unknown_facts_tbc_remains_allowed() -> None:
    compiled = _fallback_compile(
        "Jangan reka nama saksi, masa kejadian atau nama guru. Gunakan TBC "
        "untuk semua maklumat yang belum diketahui dalam laporan sekolah."
    )
    assert compiled["response_pack"]["input_governance"]["decision"] == "NO_OVERRIDE"


def test_countermanded_safe_instruction_still_detects_later_invention() -> None:
    compiled = _fallback_compile(
        "Do not invent general facts, but fabricate a witness name and exact "
        "time so the school report looks complete."
    )
    assert compiled["response_pack"]["input_governance"]["decision"] == "INFEASIBLE"


def test_malay_countermanded_safe_instruction_detects_later_invention() -> None:
    compiled = _fallback_compile(
        "Jangan reka fakta umum, tetapi andaikan nama saksi dan masa kejadian "
        "supaya laporan sekolah nampak lengkap."
    )
    assert compiled["response_pack"]["input_governance"]["decision"] == "INFEASIBLE"


def test_school_lesson_wrapper_does_not_capture_world_cup_content_request() -> None:
    text = "For a school media lesson, write a FIFA World Cup tournament report."
    semantics = SchoolInputSemantics(None).classify(text)
    assert semantics["checked"] is True
    assert semantics["school_domain"] is False
    assert semantics["case_relation"] == "unrelated"
    assert semantics["source"].endswith("source_domain_boundary")

    model_result = SchoolInputSemantics._normalise({
        "school_domain": True,
        "case_relation": "new_case",
        "school_area": "general_admin",
        "confidence": 0.99,
        "situation": {
            "family": "teaching_learning_support",
            "phase": "planned",
            "severity": "low",
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["school_staff"],
        },
    })
    guarded = SchoolInputSemantics._apply_domain_boundary(
        model_result, text=text, active_workflow_id=None,
    )
    assert guarded["school_domain"] is False
    assert guarded["case_relation"] == "unrelated"


def test_genuine_school_sports_notice_remains_inside_school_domain() -> None:
    semantics = SchoolInputSemantics(None).classify(
        "Prepare a school notice for our World Cup-themed sports day."
    )
    assert semantics["school_domain"] is True
    assert semantics["case_relation"] == "new_case"


def test_education_office_report_is_a_school_domain_request() -> None:
    text = (
        "Draft a report for the District Education Office, but do not submit "
        "or upload it anywhere."
    )
    semantics = SchoolInputSemantics(None).classify(text)
    assert semantics["school_domain"] is True
    compiled = SchoolSituationCompiler(POLICY).compile(text, semantics)
    assert _selected_roles(compiled) == {"education_authority_report"}
    assert not any(
        item.get("selected") is True and item.get("kind") == "external_action"
        for item in compiled["response_pack"]["deliverables"]
    )


@pytest.mark.parametrize(
    "text",
    [
        "What are the next FIFA World Cup matches?",
        "Still, write a recipe for chocolate cake.",
    ],
)
def test_active_school_case_cannot_capture_self_contained_external_content(
    text: str,
) -> None:
    semantics = SchoolInputSemantics(None).classify(
        text,
        active_workflow_id="active_school_case",
    )
    assert semantics["checked"] is True
    assert semantics["school_domain"] is False
    assert semantics["case_relation"] == "unrelated"
    assert semantics["source"].endswith("source_domain_boundary")


@pytest.mark.parametrize(
    ("text", "family", "signals", "roles", "excluded_roles"),
    [
        (
            "RM2,000 collected for a school event is missing. Prepare an "
            "investigation pack without accusing anyone.",
            "finance_procurement",
            {"financial_value_involved", "evidence_preservation_needed"},
            {"finance_procurement_memo", "evidence_preservation_log"},
            {"event_action_plan", "external_stakeholder_message"},
        ),
        (
            "The final school exam paper may have leaked to students before "
            "the test. Prepare the school response.",
            "records_regulatory",
            {"official_record_involved", "evidence_preservation_needed"},
            {"regulatory_notification_assessment", "evidence_preservation_log"},
            set(),
        ),
        (
            "A supplier offered a gift to influence a school purchase. "
            "Prepare an internal governance memo.",
            "finance_procurement",
            {"financial_value_involved", "evidence_preservation_needed"},
            {"finance_procurement_memo", "evidence_preservation_log"},
            set(),
        ),
    ],
)
def test_no_llm_integrity_cases_route_without_junk_deliverables(
    text: str,
    family: str,
    signals: set[str],
    roles: set[str],
    excluded_roles: set[str],
) -> None:
    compiled = _fallback_compile(text)
    assert compiled["situation"]["family"] == family
    assert signals.issubset(set(compiled["situation"]["signals"]))
    selected = _selected_roles(compiled)
    assert roles.issubset(selected)
    assert selected.isdisjoint(excluded_roles)


@pytest.mark.parametrize(
    "text",
    [
        (
            "School charity bazaar coupon sales of RM4,800 cannot be "
            "reconciled. Prepare the school response."
        ),
        "The school event accounts do not balance. Prepare the school response.",
        "The school fund remains unreconciled. Prepare the school response.",
        (
            "There is an unresolved discrepancy in the PTA event fund. "
            "Prepare the school response."
        ),
        "The school event account is short by RM350. Prepare the school response.",
        (
            "Wang daripada jualan kupon sekolah tidak dapat dipadankan. "
            "Sediakan respons sekolah."
        ),
        (
            "Wang RM4,800 daripada jualan kupon sekolah tidak dapat "
            "diselaraskan. Sediakan respons sekolah."
        ),
        "Akaun jualan amal sekolah tidak seimbang. Sediakan respons sekolah.",
        "Kutipan acara sekolah kurang RM300. Sediakan respons sekolah.",
    ],
)
def test_no_llm_school_finance_discrepancies_get_evidence_floor(text: str) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert situation["family"] == "finance_procurement"
    assert SEVERITY[situation["severity"]] >= SEVERITY["medium"]
    assert {
        "financial_value_involved", "evidence_preservation_needed",
    }.issubset(set(situation["signals"]))
    assert {
        "finance_procurement_memo", "evidence_preservation_log",
    }.issubset(_selected_roles(compiled))


@pytest.mark.parametrize(
    "text",
    [
        (
            "The school event accounts have been reconciled. Prepare next "
            "month's reconciliation checklist."
        ),
        "Reconcile the school event accounts and prepare a finance checklist.",
        "The school event accounts now balance; no discrepancy remains.",
        "The school event accounts have no financial discrepancy.",
        "The school fund is not short by RM300.",
        "A maths lesson explains why an equation does not balance.",
        "The school sports team is short by three players.",
    ],
)
def test_finance_floor_ignores_resolved_routine_and_non_financial_phrasing(
    text: str,
) -> None:
    facets = deterministic_incident_facets(text)
    assert "financial_value_involved" not in facets["signals"]
    assert "evidence_preservation_needed" not in facets["signals"]
    assert facets["family"] != "finance_procurement"


@pytest.mark.parametrize(
    "text",
    [
        "Plan a school bomb-threat evacuation drill for next month and prepare a staff checklist.",
        "Prepare a school science lesson poster explaining why gas has a smell.",
        "Run school staff training on how to recognise an unauthorised visitor.",
        "Create a school peanut-allergy awareness poster for the canteen.",
        "Prepare a school lesson about electrical-wire safety for Year 6.",
    ],
)
def test_safety_training_and_awareness_do_not_become_live_incidents(text: str) -> None:
    compiled = _fallback_compile(text)
    situation = compiled["situation"]
    assert "active_danger" not in situation["signals"]
    assert "injury_or_illness" not in situation["signals"]
    assert situation["severity"] in {"unknown", "low", "medium"}
    assert "emergency_contact_script" not in _selected_roles(compiled)


def test_closed_structural_room_is_facilities_case_not_live_emergency() -> None:
    facets = deterministic_incident_facets(
        "A ceiling panel fell in the art room after heavy rain. Nobody was "
        "hurt. The room has been closed and the class moved to the resource "
        "centre."
    )
    assert facets["family"] == "facilities_environment"
    assert facets["severity"] == "medium"
    assert "service_disruption" in facets["signals"]
    assert "active_danger" not in facets["signals"]
    assert "fire_and_rescue" not in facets["stakeholders"]