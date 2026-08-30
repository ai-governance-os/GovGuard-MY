"""Structural regressions for school case continuity and privacy context."""

from __future__ import annotations

import pytest

from teow_agl.modules.module_school_case_context import (
    build_case_context,
    merge_followup_situation,
    resolve_case_relation,
)
from teow_agl.modules.module_school_privacy import (
    source_has_individual_sensitive_detail,
    source_identifiers,
)


def _semantics() -> dict:
    # Deliberately let the model propose follow-up. The deterministic guard
    # must still require bounded case evidence before inheriting anything.
    return {
        "checked": True,
        "school_domain": True,
        "case_relation": "follow_up",
        "source": "test_semantic_llm",
        "situation": {},
    }


def _freezer_context() -> dict:
    return build_case_context(
        case_context_id="case_freezer",
        source_task_id="task_freezer",
        raw_goal=(
            "The canteen freezer failed overnight. Frozen food was soft and "
            "no temperature reading was recorded. Prepare an incident report."
        ),
        school_situation={
            "active": True,
            "family": "food_hygiene",
            "case_summary": "Canteen freezer failure and isolated food.",
            "known_facts": [{
                "fact_id": "food_condition",
                "value": "Frozen food was reported soft",
                "status": "reported",
            }],
        },
        response_pack={
            "deliverables": [{
                "deliverable_id": "incident",
                "artifact_role": "internal_incident_report",
                "kind": "artifact",
            }],
        },
    )


@pytest.mark.parametrize("goal", [
    (
        "The food was later measured at 15 degrees Celsius and has been "
        "isolated. Add a disposal-and-stock reconciliation record and a "
        "short staff instruction. Do not repeat the original incident report."
    ),
    (
        "After that, only add a disposal record to the original incident "
        "report. Do not repeat the report."
    ),
    (
        "Then add only a short staff instruction. Do not repeat the original "
        "incident report."
    ),
    "Only add a disposal record; do not repeat the original incident report.",
])
def test_bounded_additive_language_continues_the_existing_case(goal: str) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _freezer_context())
    assert resolved["case_relation"] == "follow_up"
    assert resolved["case_relation_evidence"]["candidate_case_context_id"] == (
        "case_freezer"
    )


def test_validated_later_update_preserves_parent_case_facts() -> None:
    goal = (
        "The food was later measured at 15 degrees Celsius and was isolated. "
        "Only add a disposal record; do not repeat the original incident report."
    )
    resolved = resolve_case_relation(goal, _semantics(), _freezer_context())
    assert resolved["case_relation"] == "follow_up"

    merged = merge_followup_situation(
        {
            "family": "food_hygiene",
            "phase": "follow_up",
            "severity": "medium",
            "known_facts": [{
                "fact_id": "food_temperature",
                "value": "The food was measured at 15 degrees Celsius",
                "status": "reported",
            }],
            "unknowns": [],
            "requested_outputs": [],
        },
        _freezer_context(),
        current_text=goal,
    )
    facts = {item["fact_id"]: item for item in merged["known_facts"]}
    assert "food_condition" in facts
    assert "food_temperature" in facts
    assert merged["context_merge"] == "confirmed_follow_up"


@pytest.mark.parametrize("goal", [
    (
        "Later this week, a snake was found in another school. Prepare a new "
        "incident report."
    ),
    (
        "Then a ceiling panel fell in the art room. Prepare an incident "
        "report for that event."
    ),
    (
        "A different pupil was injured in a separate incident today. Prepare "
        "a report."
    ),
])
def test_temporal_word_alone_never_inherits_an_independent_event(goal: str) -> None:
    resolved = resolve_case_relation(goal, _semantics(), _freezer_context())
    assert resolved["case_relation"] != "follow_up"


@pytest.mark.parametrize("source", [
    "The canteen freezer failed overnight.",
    "Frozen food is soft because the freezer failed.",
    "The fire alarm failed during assembly.",
    "The school bus failed its brake inspection.",
])
def test_equipment_or_service_failure_is_not_student_sensitive(source: str) -> None:
    assert source_has_individual_sensitive_detail(source) is False
    assert source_identifiers(source) == set()


def test_sentence_initial_pronoun_is_not_a_student_identifier() -> None:
    source = (
        "Eight pupils vomited after lunch. They are stable and there is no "
        "unmet medical emergency now."
    )

    assert "They" not in source_identifiers(source)


@pytest.mark.parametrize(("source", "identifier"), [
    ("Ali failed BM.", "Ali"),
    ("A Year 5 student Ali failed BM with 20 marks.", "Ali"),
    ("Student Ali failed the examination.", "Ali"),
    ("Ali has a disciplinary record.", "Ali"),
])
def test_named_student_sensitive_information_remains_protected(
    source: str,
    identifier: str,
) -> None:
    assert source_has_individual_sensitive_detail(source) is True
    assert identifier in source_identifiers(source)
