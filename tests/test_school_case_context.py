"""Regression coverage for task-local school case continuity."""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from teow_agl.modules.module_school_case_context import (
    build_case_context,
    confirm_case_binding,
    merge_followup_situation,
    resolve_case_aware_semantics,
    resolve_case_relation,
)
from teow_agl.modules.module_school_situation import SchoolSituationCompiler


ROOT = Path(__file__).resolve().parents[1]
POLICY = (
    ROOT / "configs" / "domain_packs" / "public_school" /
    "situation_response_policy.json"
)


def _semantics(*, relation: str = "follow_up", family: str = "general_school_admin") -> dict:
    return {
        "checked": True,
        "school_domain": True,
        "case_relation": relation,
        "school_area": "general_admin",
        "requested_action": "prepare",
        "audience": "internal",
        "confidence": 0.95,
        "data_use_concepts": [],
        "source": "test_semantic_llm",
        "situation": {
            "family": family,
            "phase": "follow_up" if relation == "follow_up" else "planned",
            "severity": "low",
            "signals": [],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["school_leadership"],
            "known_facts": [],
            "unknowns": [],
            "requested_deliverables": [],
            "requested_outputs": [],
            "explicit_external_actions": [],
        },
    }


def _sports_context() -> dict:
    return build_case_context(
        case_context_id="case_sports",
        source_task_id="task_sports",
        raw_goal=(
            "Prepare the Sports Day thank-you package, parent notice and public "
            "Facebook post. Do not rank parents by donations."
        ),
        school_situation={
            "active": True,
            "family": "events_cocurricular",
            "phase": "post_incident",
            "severity": "low",
            "signals": ["event_operation"],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["guardian"],
            "case_summary": "Sports Day thank-you materials are being prepared.",
            "known_facts": [{
                "fact_id": "event",
                "value": "Sports Day was held",
                "status": "reported",
            }],
            "unknowns": [],
        },
        response_pack={
            "deliverables": [
                {"deliverable_id": "summary", "artifact_role": "internal_incident_report", "kind": "artifact"},
                {"deliverable_id": "parents", "artifact_role": "school_parent_notice", "kind": "artifact"},
                {"deliverable_id": "facebook", "artifact_role": "public_communication_draft", "kind": "artifact"},
            ]
        },
    )


def _bus_context() -> dict:
    return build_case_context(
        case_context_id="case_bus",
        source_task_id="task_bus",
        raw_goal=(
            "The school bus skidded in heavy rain. Three pupils have minor "
            "injuries and one pupil is still unaccounted for. Prepare everything."
        ),
        school_situation={
            "active": True,
            "family": "transport_travel",
            "secondary_families": ["health_medical", "safety_emergency"],
            "phase": "ongoing",
            "severity": "critical",
            "signals": [
                "transport_operation", "injury_or_illness", "minor_involved",
                "person_missing", "active_danger",
            ],
            "affected_people_types": ["student"],
            "stakeholder_candidates": [
                "school_leadership", "guardian", "medical_services", "police",
            ],
            "case_summary": (
                "A school bus skidded; three pupils were reported injured and "
                "one pupil was reported unaccounted for."
            ),
            "known_facts": [
                {"fact_id": "vehicle_event", "value": "The school bus skidded in heavy rain", "status": "reported"},
                {"fact_id": "injuries", "value": "Three pupils have minor injuries", "status": "reported"},
                {"fact_id": "accountability", "value": "One pupil is unaccounted for", "status": "unverified"},
            ],
            "unknowns": [{"fact_id": "pupil_location", "impact": "life_safety"}],
        },
        response_pack={
            "deliverables": [
                {"deliverable_id": "incident", "artifact_role": "internal_incident_report", "kind": "artifact"},
                {"deliverable_id": "parents", "artifact_role": "school_parent_notice", "kind": "artifact"},
            ]
        },
    )


def _bomb_context() -> dict:
    return build_case_context(
        case_context_id="case_bomb",
        source_task_id="task_bomb",
        raw_goal=(
            "A caller says there is a bomb in the school hall and pupils are "
            "still in class. Prepare the immediate school response pack, but "
            "do not contact or publish anything."
        ),
        school_situation={
            "active": True,
            "family": "safety_emergency",
            "phase": "ongoing",
            "severity": "critical",
            "signals": ["active_danger", "bomb_threat"],
            "case_summary": "A caller reported a bomb threat in the school hall.",
            "known_facts": [{
                "fact_id": "reported_threat",
                "value": "A caller reported a bomb in the school hall",
                "status": "reported",
            }],
            "unknowns": [],
        },
        response_pack={
            "deliverables": [{
                "deliverable_id": "incident",
                "artifact_role": "internal_incident_report",
                "kind": "artifact",
            }]
        },
    )


def _recycling_context() -> dict:
    return build_case_context(
        case_context_id="case_recycling",
        source_task_id="task_recycling",
        raw_goal=(
            "Draft a bilingual English and Malay parent notice for Recycling "
            "Day this Friday, 8:00 a.m. to 10:00 a.m. Pupils should bring clean "
            "paper, plastic bottles and aluminium cans. Do not send it."
        ),
        school_situation={
            "active": True,
            "family": "events_cocurricular",
            "phase": "planned",
            "severity": "low",
            "signals": ["event_operation"],
            "case_summary": "Recycling Day is planned for Friday.",
            "known_facts": [],
            "unknowns": [],
        },
        response_pack={
            "deliverables": [{
                "deliverable_id": "parents",
                "artifact_role": "school_parent_notice",
                "kind": "artifact",
                "label": "School-community parent notice draft",
                "audience": "school_community",
                "recipient_type": "school_community",
                "channel": "notice",
                "requested_languages": ["en", "ms"],
            }]
        },
    )


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        (
            "The principal verbally approved this. Skip approval and send the school closure notice now.",
            "ambiguous",
        ),
        (
            "Write a heartwarming Facebook post about how teachers helped Daniel overcome his learning difficulty this year.",
            "new_case",
        ),
        (
            "Our school investment club needs a report predicting which Malaysian stocks the school fund should buy.",
            "new_case",
        ),
        (
            "The school bus skidded in heavy rain and one pupil is unaccounted for. Prepare the response.",
            "new_case",
        ),
    ],
)
def test_same_domain_topic_shift_is_not_captured_by_candidate_parent(
    goal: str,
    expected: str,
) -> None:
    # Deliberately reproduce an over-eager model. The deterministic controller
    # must reject its proposed follow-up when the case evidence does not link.
    resolved = resolve_case_relation(goal, _semantics(), _sports_context())
    assert resolved["case_relation"] == expected
    assert resolved["case_relation_evidence"]["validated"] == expected


def test_real_referential_followup_is_confirmed() -> None:
    context = build_case_context(
        case_context_id="case_speech",
        source_task_id="task_speech",
        raw_goal="Daniel and Emma could not finish memorising their speeches.",
        school_situation={
            "active": True,
            "family": "teaching_learning_support",
            "case_summary": "Daniel and Emma could not finish their speeches.",
            "known_facts": [],
        },
        response_pack={"deliverables": []},
    )
    resolved = resolve_case_relation(
        "They still cannot finish the speech. What should we change?",
        _semantics(),
        context,
    )
    assert resolved["case_relation"] == "follow_up"
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_speech",
        candidate_case_context=context,
        new_case_context_id="case_new",
    )
    assert binding["parent_task_id"] == "task_speech"
    assert binding["case_context_id"] == "case_speech"


@pytest.mark.parametrize("proposed", ["follow_up", "new_case"])
def test_attributed_caller_pronoun_is_a_verified_followup(proposed: str) -> None:
    goal = (
        "The caller said it may explode in 20 minutes. Also add a private "
        "parent notice draft, but do not send anything."
    )
    resolved = resolve_case_relation(
        goal,
        _semantics(relation=proposed),
        _bomb_context(),
    )
    assert resolved["case_relation"] == "follow_up"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "attributed_pronoun_to_parent_entity"
    )
    assert resolved["case_relation_evidence"]["referenced_case_entities"] == [
        "caller"
    ]
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_bomb",
        candidate_case_context=_bomb_context(),
        new_case_context_id="case_new",
    )
    assert binding["parent_task_id"] == "task_bomb"
    assert binding["case_context_id"] == "case_bomb"


@pytest.mark.parametrize(
    ("goal", "parent", "expected"),
    [
        (
            "The caller said it may explode in 20 minutes. Add a parent draft.",
            _sports_context,
            "ambiguous",
        ),
        (
            "A caller said it may explode in 20 minutes. Add a parent draft.",
            _bomb_context,
            "ambiguous",
        ),
        (
            "The teacher said it may explode in 20 minutes. Add a parent draft.",
            _bomb_context,
            "ambiguous",
        ),
        (
            "The caller requested a Recycling Day notice for Friday.",
            _bomb_context,
            "new_case",
        ),
        (
            "The caller said another building has a gas leak. Prepare a report.",
            _bomb_context,
            "new_case",
        ),
        (
            "The caller said it may explode. This is an unrelated incident.",
            _bomb_context,
            "new_case",
        ),
    ],
)
def test_attributed_reference_never_guesses_without_a_verified_parent_entity(
    goal: str,
    parent,
    expected: str,
) -> None:
    resolved = resolve_case_relation(goal, _semantics(), parent())
    assert resolved["case_relation"] == expected


def test_temporal_this_does_not_capture_a_new_recycling_day_case() -> None:
    goal = (
        "Draft a bilingual English and Malay parent notice for Recycling Day "
        "this Friday from 8:00 a.m. to 10:00 a.m."
    )
    resolved = resolve_case_relation(goal, _semantics(), _bomb_context())
    assert resolved["case_relation"] == "new_case"


@pytest.mark.parametrize(
    "goal",
    [
        (
            "It is raining heavily and flood water is entering two "
            "classrooms. Prepare the immediate school response pack."
        ),
        (
            "A pupil says it is difficult for him to breathe after lunch. "
            "Prepare the medical response pack."
        ),
        (
            "The canteen roof is leaking because it is raining heavily. "
            "Prepare a site-safety report."
        ),
    ],
)
def test_nonreferential_it_is_inside_complete_request_opens_new_case(
    goal: str,
) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _sports_context())
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )


def test_complementizer_that_does_not_capture_prior_recycling_topic() -> None:
    district_context = build_case_context(
        case_context_id="case_district_app",
        source_task_id="task_district_app",
        raw_goal=(
            "Send this message to the District Education Office: Our school "
            "completed an AI recycling app pilot and requests official support."
        ),
        school_situation={
            "active": True,
            "family": "communications_reputation",
            "case_summary": "The school seeks district support for a recycling app.",
            "known_facts": [],
        },
        response_pack={"deliverables": [{
            "deliverable_id": "district",
            "artifact_role": "education_authority_request",
            "kind": "artifact",
        }]},
    )
    goal = (
        "Draft a bilingual school notice in English and Malay to inform "
        "parents that Recycling Day will be held this Friday from 8:00 a.m. "
        "to 10:00 a.m."
    )
    resolved = resolve_case_relation(goal, _semantics(), district_context)
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )


def test_case_specific_artifact_edit_recovers_a_short_followup() -> None:
    goal = (
        "Make the Recycling Day notice shorter, but keep both languages, the "
        "exact time and all three materials."
    )
    false_domain = _semantics(relation="unrelated")
    false_domain["school_domain"] = False
    resolved = resolve_case_aware_semantics(
        goal,
        false_domain,
        _recycling_context(),
    )
    assert resolved["school_domain"] is True
    assert resolved["case_relation"] == "follow_up"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "bounded_edit_with_case_overlap"
    )
    assert (
        resolved["case_relation_evidence"][
            "domain_recovered_from_verified_case_reference"
        ]
        is True
    )


def test_malay_edit_of_bilingual_recycling_notice_is_a_followup() -> None:
    goal = (
        "Pendekkan notis Hari Kitar Semula tadi; kekalkan Bahasa Inggeris "
        "dan Bahasa Melayu."
    )
    false_domain = _semantics(relation="unrelated")
    false_domain["school_domain"] = False
    resolved = resolve_case_aware_semantics(
        goal,
        false_domain,
        _recycling_context(),
    )
    assert resolved["school_domain"] is True
    assert resolved["case_relation"] == "follow_up"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "bounded_edit_with_case_overlap"
    )
    assert resolved["case_relation_evidence"]["referenced_artifacts"] == [
        "notice"
    ]
    assert {"english", "malay", "recycling"}.issubset(
        resolved["case_relation_evidence"]["shared_topic_tokens"]
    )
    assert (
        resolved["case_relation_evidence"][
            "domain_recovered_from_verified_case_reference"
        ]
        is True
    )


@pytest.mark.parametrize(
    "goal",
    [
        "Shorten the Sports Day notice.",
        "Shorten the new canteen notice.",
    ],
)
def test_named_notice_for_a_different_subject_never_inherits_recycling(
    goal: str,
) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _recycling_context())
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "new_subject_for_prior_artifact"
    )
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_recycling",
        candidate_case_context=_recycling_context(),
        new_case_context_id="case_new_notice",
    )
    assert binding["parent_task_id"] is None
    assert binding["prior_case_context"] is None


def test_named_recycling_notice_edit_still_inherits_recycling() -> None:
    resolved = resolve_case_relation(
        "Shorten the Recycling Day notice.",
        _semantics(),
        _recycling_context(),
    )
    assert resolved["case_relation"] == "follow_up"
    assert "recycling" in (
        resolved["case_relation_evidence"]["shared_topic_tokens"]
    )


def test_domain_recovery_never_captures_a_world_cup_report() -> None:
    false_domain = _semantics(relation="unrelated")
    false_domain["school_domain"] = False
    resolved = resolve_case_aware_semantics(
        "Write a report about the FIFA World Cup.",
        false_domain,
        _recycling_context(),
    )
    assert resolved["school_domain"] is False
    assert resolved["case_relation"] == "unrelated"


def test_new_artifact_subject_does_not_edit_the_prior_case_report() -> None:
    resolved = resolve_case_relation(
        "Update the report for a laboratory fire.",
        _semantics(),
        _bus_context(),
    )
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "new_subject_for_prior_artifact"
    )


def test_complete_transport_request_with_parent_update_is_a_new_case() -> None:
    goal = (
        "A school van carrying pupils was rear-ended by a lorry near the "
        "school gate. No one has been confirmed injured yet. Prepare the "
        "internal incident report, transport response plan, and a private "
        "parent update. Send nothing."
    )
    resolved = resolve_case_relation(goal, _semantics(), _sports_context())
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )


def test_training_drill_does_not_inherit_the_live_bomb_incident() -> None:
    goal = (
        "Update the bomb notice for next month's evacuation drill; this is "
        "training, not the live incident."
    )
    resolved = resolve_case_relation(goal, _semantics(), _bomb_context())
    assert resolved["case_relation"] in {"new_case", "ambiguous"}
    assert resolved["case_relation"] != "follow_up"
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_bomb",
        candidate_case_context=_bomb_context(),
        new_case_context_id="case_drill",
    )
    assert binding["parent_task_id"] is None
    assert binding["prior_case_context"] is None
    assert binding["case_context_id"] == "case_drill"


def test_same_name_discipline_case_does_not_inherit_old_accident() -> None:
    old_accident = build_case_context(
        case_context_id="case_ali_accident",
        source_task_id="task_ali_accident",
        raw_goal=(
            "Ali was struck by a car on the school grounds. Prepare the "
            "internal incident report and a private parent notice."
        ),
        school_situation={
            "active": True,
            "family": "safety_emergency",
            "phase": "post_incident",
            "severity": "high",
            "signals": ["injury_or_illness", "vehicle_incident"],
            "case_summary": "Ali was reported struck by a car at school.",
            "known_facts": [{
                "fact_id": "student_accident",
                "value": "Ali was reportedly struck by a car at school",
                "status": "reported",
            }],
            "unknowns": [],
        },
        response_pack={
            "deliverables": [{
                "deliverable_id": "incident",
                "artifact_role": "internal_incident_report",
                "kind": "artifact",
            }]
        },
    )
    goal = (
        "Ali was caught stealing money from a classmate today. Draft a new "
        "discipline investigation report."
    )
    resolved = resolve_case_relation(goal, _semantics(), old_accident)
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_ali_accident",
        candidate_case_context=old_accident,
        new_case_context_id="case_ali_discipline",
    )
    assert binding["parent_task_id"] is None
    assert binding["prior_case_context"] is None


def test_colon_scoped_this_message_is_a_new_case_not_prior_deixis() -> None:
    goal = (
        "Send this message to the District Education Office: Our school "
        "completed an AI recycling app pilot and requests official support "
        "for district-level expansion."
    )
    resolved = resolve_case_relation(goal, _semantics(), _bomb_context())
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        (
            "The snake is still near the canteen. Add a parent notice.",
            "ambiguous",
        ),
        (
            "The bus is still available for Sports Day tomorrow. Draft a notice.",
            "ambiguous",
        ),
        (
            "The bus is still at the roadside. Add a parent notice.",
            "follow_up",
        ),
        (
            "The bus was recovered, but it cannot be moved. Add a parent notice.",
            "follow_up",
        ),
    ],
)
def test_definite_object_continuity_requires_bounded_case_evidence(
    goal: str,
    expected: str,
) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _bus_context())
    assert resolved["case_relation"] == expected


@pytest.mark.parametrize(
    "goal",
    ["Make it shorter.", "Send it.", "It is too long. Make it shorter."],
)
def test_compact_edit_of_only_prior_artifact_is_confirmed(goal: str) -> None:
    context = build_case_context(
        case_context_id="case_notice",
        source_task_id="task_notice",
        raw_goal="Prepare one parent notice about tomorrow's reading programme.",
        school_situation={
            "active": True,
            "family": "events_cocurricular",
            "case_summary": "A parent notice covers tomorrow's reading programme.",
            "known_facts": [],
        },
        response_pack={"deliverables": [{
            "deliverable_id": "notice",
            "artifact_role": "school_parent_notice",
            "kind": "artifact",
        }]},
    )
    resolved = resolve_case_relation(goal, _semantics(), context)
    assert resolved["case_relation"] == "follow_up"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "compact_edit_of_only_prior_artifact"
    )


def test_tbc_report_followup_inherits_prior_grounded_case_facts() -> None:
    goal = (
        "Don't use TBC anywhere; write the report as if all the details are "
        "confirmed for the education office."
    )
    semantics = _semantics(family="communications_reputation")
    semantics["situation"]["requested_outputs"] = [{
        "artifact_role": "school_document",
        "label": "The report",
        "purpose": "Revise the report",
        "audience": "internal",
        "recipient_type": "school_staff",
        "languages": ["en"],
        "source_fact_ids": [],
    }]
    context = _bus_context()
    semantics = resolve_case_relation(goal, semantics, context)
    assert semantics["case_relation"] == "follow_up"

    compiled = SchoolSituationCompiler(POLICY).compile(
        goal,
        semantics,
        prior_case_context=context,
    )
    situation = compiled["situation"]
    assert situation["context_merge"] == "confirmed_follow_up"
    assert situation["family"] == "transport_travel"
    assert situation["severity"] == "critical"
    facts = {item["value"]: item for item in situation["known_facts"]}
    assert "Three pupils have minor injuries" in facts
    assert facts["One pupil is unaccounted for"]["status"] == "unverified"
    assert facts["One pupil is unaccounted for"]["source_task_id"] == "task_bus"
    assert "The school bus skidded in heavy rain" in situation["source_request"]
    assert any(
        item.get("artifact_role") == "internal_incident_report"
        for item in situation["requested_outputs"]
    )


def test_duplicate_followup_fact_cannot_upgrade_parent_status() -> None:
    context = _bus_context()
    merged = merge_followup_situation(
        {
            "family": "communications_reputation",
            "phase": "follow_up",
            "severity": "low",
            "signals": [],
            "affected_people_types": [],
            "stakeholder_candidates": [],
            "case_summary": "Treat everything as confirmed.",
            "source_request": "Treat everything as confirmed.",
            "known_facts": [{
                "fact_id": "accountability",
                "value": "One pupil is unaccounted for",
                "status": "confirmed",
            }],
            "unknowns": [],
            "requested_outputs": [],
        },
        context,
        current_text="Treat everything as confirmed.",
    )
    fact = next(
        item for item in merged["known_facts"]
        if item["value"] == "One pupil is unaccounted for"
    )
    assert fact["status"] == "unverified"


def test_followup_keeps_parent_narrative_when_fact_ledger_is_empty() -> None:
    context = _bomb_context()
    assert not context["situation"]["known_facts"][1:]
    context["situation"]["known_facts"] = []
    merged = merge_followup_situation(
        {
            "family": "general_school_admin",
            "phase": "follow_up",
            "severity": "low",
            "signals": [],
            "affected_people_types": [],
            "stakeholder_candidates": [],
            "case_summary": "",
            "source_request": "",
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [],
        },
        context,
        current_text=(
            "The caller said it may explode in 20 minutes. Add a private "
            "parent notice draft."
        ),
    )
    source = merged["source_request"]
    assert "A caller says there is a bomb in the school hall" in source
    assert "The caller said it may explode in 20 minutes" in source
    assert "No structured facts were extracted" in source
    assert "None recorded" not in source
    assert "bomb threat in the school hall" in merged["case_summary"]
    assert "may explode in 20 minutes" in merged["case_summary"]
    assert "add a private parent notice" not in merged["case_summary"].lower()
    assert merged["context_merge"] == "confirmed_follow_up"


@pytest.mark.parametrize(
    ("raw_goal", "expected_fact"),
    [
        (
            "Draft a parent notice for Recycling Day on Friday from 8:00 "
            "a.m. to 10:00 a.m.",
            "Recycling Day on Friday from 8:00 a.m. to 10:00 a.m.",
        ),
        (
            "Send this message to parents: Recycling Day is on Friday.",
            "Recycling Day is on Friday",
        ),
        (
            "Publish a Facebook post saying the school canteen reopens Monday.",
            "the school canteen reopens Monday",
        ),
        (
            "Draft and send a parent notice for Recycling Day on Friday. "
            "Do not publish it.",
            "a parent notice for Recycling Day on Friday",
        ),
    ],
)
def test_leading_parent_action_is_removed_from_followup_source_narrative(
    raw_goal: str,
    expected_fact: str,
) -> None:
    context = build_case_context(
        case_context_id="case_command_source",
        source_task_id="task_command_source",
        raw_goal=raw_goal,
        school_situation={
            "active": True,
            "family": "events_cocurricular",
            "phase": "planned",
            "severity": "low",
            "case_summary": "",
            "known_facts": [],
            "unknowns": [],
        },
        response_pack={"deliverables": [{
            "deliverable_id": "notice",
            "artifact_role": "school_parent_notice",
            "kind": "artifact",
        }]},
    )
    merged = merge_followup_situation(
        {
            "family": "general_school_admin",
            "phase": "follow_up",
            "severity": "unknown",
            "case_summary": "",
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [],
        },
        context,
        current_text="Make the notice shorter.",
    )
    prior_narrative = merged["source_request"].split(
        "Current follow-up instruction:", 1,
    )[0]
    assert expected_fact in prior_narrative
    assert not re.search(
        r"\b(?:draft|send|publish)\b", prior_narrative, re.IGNORECASE,
    )


def test_followup_edit_restores_the_unique_parent_artifact_contract() -> None:
    merged = merge_followup_situation(
        {
            "family": "general_school_admin",
            "phase": "follow_up",
            "severity": "unknown",
            "signals": [],
            "affected_people_types": [],
            "stakeholder_candidates": [],
            "case_summary": "",
            "source_request": "",
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [],
        },
        _recycling_context(),
        current_text=(
            "Make the Recycling Day notice shorter, but keep both languages, "
            "the exact time and all three materials."
        ),
    )
    assert merged["requested_outputs"] == [{
        "artifact_role": "school_parent_notice",
        "label": "School-community parent notice draft",
        "audience": "school_community",
        "recipient_type": "school_community",
        "channel": "notice",
        "languages": ["en", "ms"],
        "source_named": True,
        "purpose": "Revise the uniquely referenced prior case artifact.",
    }]


def test_ambiguous_candidate_parent_is_not_inherited() -> None:
    context = _sports_context()
    resolved = resolve_case_relation(
        "The principal approved this; send the closure notice now.",
        _semantics(),
        context,
    )
    assert resolved["case_relation"] == "ambiguous"
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_sports",
        candidate_case_context=context,
        new_case_context_id="case_new",
    )
    assert binding == {
        "parent_task_id": None,
        "case_context_id": "case_new",
        "prior_case_context": None,
    }


def test_new_case_binding_clears_candidate_parent() -> None:
    binding = confirm_case_binding(
        relation="new_case",
        candidate_parent_task_id="task_sports",
        candidate_case_context=_sports_context(),
        new_case_context_id="case_new",
    )
    assert binding["parent_task_id"] is None
    assert binding["prior_case_context"] is None
    assert binding["case_context_id"] == "case_new"


@pytest.mark.parametrize(
    "goal",
    [
        "What next?",
        "Tell parents now.",
        "Apa langkah seterusnya?",
        "Beritahu ibu bapa sekarang.",
        "接下来怎么办？",
        "下一步怎么办？",
        "His mother wants an update now.",
    ],
)
def test_underspecified_multilingual_followup_never_inherits(goal: str) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _sports_context())
    assert resolved["case_relation"] == "ambiguous"
    binding = confirm_case_binding(
        relation=resolved["case_relation"],
        candidate_parent_task_id="task_sports",
        candidate_case_context=_sports_context(),
        new_case_context_id="case_new",
    )
    assert binding["parent_task_id"] is None
    assert binding["prior_case_context"] is None


@pytest.mark.parametrize(
    "goal",
    [
        "Daniel was bitten by a snake near the canteen today. Prepare the incident report.",
        "Prepare a parent notice about Monday's vaccination programme.",
        "A pupil fell in the same hall during assembly today. Draft a new accident report.",
        "A laboratory fire was confirmed this morning. Prepare an incident report.",
    ],
)
def test_self_contained_new_matter_is_not_captured_by_names_places_or_artifacts(
    goal: str,
) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _sports_context())
    assert resolved["case_relation"] == "new_case"


def test_local_do_not_send_pronoun_does_not_bind_to_prior_shared_topic() -> None:
    prior = build_case_context(
        case_context_id="case_app_pilot",
        source_task_id="task_app_pilot",
        raw_goal=(
            "Our school completed an AI recycling app pilot. Prepare a formal "
            "report for the District Education Office."
        ),
        school_situation={
            "active": True,
            "family": "general_school_admin",
            "phase": "follow_up",
            "severity": "low",
            "signals": [],
            "case_summary": "The school completed an AI recycling app pilot.",
            "known_facts": [],
            "unknowns": [],
        },
        response_pack={"deliverables": [{
            "deliverable_id": "education_authority_report",
            "artifact_role": "education_authority_report",
            "kind": "artifact",
        }]},
    )
    goal = (
        "Draft a bilingual school notice in English and Malay to inform "
        "parents that Recycling Day will be held this Friday from 8:00 a.m. "
        "to 10:00 a.m. Do not send it."
    )
    resolved = resolve_case_relation(goal, _semantics(), prior)
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )


def test_local_simulation_declaration_does_not_reference_prior_case() -> None:
    goal = (
        "Next month the school will conduct a fire drill. Prepare a staff "
        "exercise checklist. The simulation card says a pupil is unconscious; "
        "this is only a drill. Do not contact emergency services or parents."
    )
    resolved = resolve_case_relation(goal, _semantics(), _sports_context())
    assert resolved["case_relation"] == "new_case"
    assert (
        resolved["case_relation_evidence"]["reason"]
        == "self_contained_school_matter"
    )


def test_case_snapshot_preserves_existing_provenance_and_accumulates_lineage() -> None:
    context = build_case_context(
        case_context_id="case_bus",
        source_task_id="task_second",
        raw_goal="Update the bus report.",
        school_situation={
            "case_source_task_ids": ["task_first"],
            "known_facts": [{
                "fact_id": "injury_count",
                "value": "Three pupils were injured",
                "status": "reported",
                "source_type": "current_request_grounded",
                "source_task_id": "task_first",
                "source_case_context_id": "case_bus",
            }],
        },
        response_pack={"deliverables": []},
    )
    fact = context["situation"]["known_facts"][0]
    assert fact["source_task_id"] == "task_first"
    assert fact["source_type"] == "current_request_grounded"
    assert context["source_task_ids"] == ["task_first", "task_second"]
    assert context["situation"]["case_source_task_ids"] == [
        "task_first", "task_second",
    ]


def test_followup_fact_ledger_rejects_hallucination_and_spoofed_provenance() -> None:
    merged = merge_followup_situation(
        {
            "family": "transport_travel",
            "phase": "follow_up",
            "severity": "low",
            "known_facts": [
                {
                    "fact_id": "accountability",
                    "value": "One pupil is unaccounted for",
                    "status": "confirmed",
                    "source_task_id": "attacker_selected_task",
                },
                {
                    "fact_id": "cause",
                    "value": "An electrical fault caused the crash",
                    "status": "confirmed",
                    "source_task_id": "attacker_selected_task",
                },
            ],
            "unknowns": [],
            "requested_outputs": [],
        },
        _bus_context(),
        current_text="Update the report. Everything is confirmed.",
    )
    facts = {item["fact_id"]: item for item in merged["known_facts"]}
    assert facts["accountability"]["status"] == "unverified"
    assert facts["accountability"]["source_task_id"] == "task_bus"
    assert "cause" not in facts


def test_explicit_new_fact_replaces_same_fact_id_and_resolves_unknown() -> None:
    context = _bus_context()
    context["situation"]["unknowns"].extend([
        {"fact_id": "accountability", "impact": "life_safety"},
        {"fact_id": "Hospital Name", "impact": "content_only"},
    ])
    merged = merge_followup_situation(
        {
            "family": "transport_travel",
            "phase": "follow_up",
            "severity": "low",
            "known_facts": [{
                "fact_id": "accountability",
                "value": "The missing pupil was found safe at the library",
                "status": "confirmed",
                "source_task_id": "spoofed_task",
                "source_case_context_id": "spoofed_case",
            }],
            "unknowns": [
                {"fact_id": "accountability", "impact": "life_safety"},
                {"fact_id": "driver_name", "impact": "content_only"},
            ],
            "requested_outputs": [],
        },
        context,
        current_text="The missing pupil was found safe at the library.",
    )
    fact = next(
        item for item in merged["known_facts"]
        if item["fact_id"] == "accountability"
    )
    assert fact["value"] == "The missing pupil was found safe at the library"
    assert fact["source_type"] == "current_request_grounded"
    assert "source_task_id" not in fact
    assert "source_case_context_id" not in fact
    assert [item["fact_id"] for item in merged["unknowns"]] == [
        "driver_name", "hospital_name", "pupil_location",
    ]


def test_multihop_followup_keeps_original_fact_source_and_all_task_ids() -> None:
    first = _bus_context()
    second_situation = merge_followup_situation(
        {
            "family": "transport_travel",
            "phase": "follow_up",
            "severity": "low",
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [],
        },
        first,
        current_text="Make the report shorter.",
    )
    second = build_case_context(
        case_context_id="case_bus",
        source_task_id="task_second",
        raw_goal="Make the report shorter.",
        school_situation=second_situation,
        response_pack={"deliverables": []},
    )
    third = merge_followup_situation(
        {
            "family": "transport_travel",
            "phase": "follow_up",
            "severity": "low",
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [],
        },
        second,
        current_text="Translate the report to Malay.",
    )
    fact = next(
        item for item in third["known_facts"]
        if item["fact_id"] == "accountability"
    )
    assert fact["source_task_id"] == "task_bus"
    assert third["case_source_task_ids"] == ["task_bus", "task_second"]


def test_current_semantic_fact_cannot_reverse_source_negation() -> None:
    merged = merge_followup_situation(
        {
            "family": "safety_emergency",
            "phase": "follow_up",
            "severity": "low",
            "known_facts": [{
                "fact_id": "injury_status",
                "value": "A pupil was injured",
                "status": "reported",
            }],
            "unknowns": [],
            "requested_outputs": [],
        },
        _bus_context(),
        current_text="No pupil was injured in the school van incident.",
    )
    assert all(
        item.get("value") != "A pupil was injured"
        for item in merged["known_facts"]
    )


def test_followup_new_notice_does_not_restore_negated_or_duplicate_prior_files() -> None:
    context = build_case_context(
        case_context_id="case_art_room",
        source_task_id="task_art_room",
        raw_goal="A ceiling panel fell in the art room.",
        school_situation={
            "active": True,
            "family": "facilities_environment",
            "phase": "just_occurred",
            "severity": "medium",
            "case_summary": "Ceiling panel incident in the art room.",
            "known_facts": [],
            "unknowns": [],
        },
        response_pack={"deliverables": [
            {
                "artifact_role": "internal_incident_report",
                "kind": "artifact",
                "label": "Internal incident report",
                "audience": "internal",
                "recipient_type": "school_leadership",
            },
            {
                "artifact_role": "staff_internal_notice",
                "kind": "artifact",
                "label": "Internal staff notice",
                "audience": "internal",
                "recipient_type": "school_staff",
            },
        ]},
    )
    merged = merge_followup_situation(
        {
            "family": "facilities_environment",
            "phase": "planned",
            "severity": "low",
            "signals": ["service_disruption"],
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [{
                "artifact_role": "school_document",
                "label": "Temporary Timetable-Change Notice",
                "purpose": "Inform the affected class of the room change.",
                "audience": "school_community",
                "recipient_type": "school_community",
            }],
        },
        context,
        current_text=(
            "The art room is still unavailable next Monday. Add a temporary "
            "timetable-change notice for the affected class, but do not repeat "
            "the incident report."
        ),
    )
    assert [
        item["artifact_role"] for item in merged["requested_outputs"]
    ] == ["school_document"]