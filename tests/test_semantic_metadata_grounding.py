from __future__ import annotations

from pathlib import Path

from teow_agl.modules.module_school_situation import SchoolSituationCompiler


POLICY = (
    Path(__file__).parents[1] / "configs" / "domain_packs" / "public_school"
    / "situation_response_policy.json"
)


def _compile(known_facts):
    source = "Water leak near the toilet. Prepare an internal report."
    semantics = {
        "checked": True, "source": "live_api", "school_domain": True,
        "requested_action": "draft", "audience": "internal",
        "data_use_concepts": [],
        "situation": {
            "family": "facilities_environment", "phase": "ongoing",
            "severity": "medium", "signals": ["facilities_disruption"],
            "affected_people_types": ["unknown"],
            "stakeholder_candidates": ["school_leadership"],
            "case_summary": "Three students were injured in the library.",
            "known_facts": known_facts, "unknowns": [],
            "requested_outputs": [{
                "artifact_role": "internal_incident_report",
                "audience": "internal",
            }],
            "explicit_external_actions": [],
        },
    }
    return source, SchoolSituationCompiler(POLICY).compile(source, semantics)


def test_semantic_hallucination_cannot_enter_judge_visible_case_metadata():
    source, result = _compile([{
        "fact_id": "injury", "value": "Three students were injured in the library",
        "status": "confirmed",
    }])
    situation = result["situation"]
    pack = result["response_pack"]
    assert situation["case_summary"] == source
    assert pack["case_summary"] == source
    assert situation["known_facts"] == []
    assert "library" not in str(pack).casefold()
    assert "injured" not in str(pack).casefold()


def test_source_grounded_semantic_fact_is_labelled_honestly_not_as_raw_user_fact():
    _source, result = _compile([{
        "fact_id": "leak", "value": "Water leak near the toilet",
        "status": "confirmed",
    }])
    facts = result["situation"]["known_facts"]
    assert len(facts) == 1
    assert facts[0]["status"] == "reported"
    assert facts[0]["source_type"] == (
        "semantic_extraction_grounded_in_user_request"
    )


def _fact_list(source: str, value: str):
    return SchoolSituationCompiler._safe_fact_list(
        [{"fact_id": "claim", "value": value, "status": "confirmed"}],
        source_text=source,
    )


def test_semantic_fact_grounding_preserves_negation_polarity():
    unsafe_pairs = [
        ("A student was not injured.", "A student was injured."),
        ("A student was injured.", "A student was not injured."),
        ("The school was not closed.", "The school was closed."),
        ("The school was closed.", "The school was not closed."),
        ("Ali's parent was not contacted.", "Ali's parent was contacted."),
        ("There was no data breach.", "There was a data breach."),
    ]
    for source, inverted in unsafe_pairs:
        assert _fact_list(source, inverted) == []


def test_semantic_fact_grounding_accepts_same_clause_same_polarity():
    assert _fact_list(
        "A student was not injured during the incident.",
        "A student was not injured",
    )
    assert _fact_list(
        "The school was closed after the water leak.",
        "The school was closed",
    )
