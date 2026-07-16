from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.modules.module_school_situation import SchoolSituationCompiler


POLICY = (
    Path(__file__).parents[1] / "configs" / "domain_packs" / "public_school"
    / "situation_response_policy.json"
)


def _semantics() -> dict:
    return {
        "checked": True,
        "source": "live_api",
        "school_domain": True,
        "requested_action": "send",
        "audience": "unknown",
        "data_use_concepts": [],
        "situation": {
            "family": "records_regulatory",
            "phase": "follow_up",
            "severity": "low",
            "signals": [],
            "affected_people_types": ["unknown"],
            "stakeholder_candidates": [],
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": [{
                "artifact_role": "school_document",
                "audience": "internal",
            }],
            "explicit_external_actions": [],
        },
    }


def _compile(answer: str | None = None):
    return SchoolSituationCompiler(POLICY).compile(
        "The report is ready. Send this report now.",
        _semantics(),
        clarification_answers=(
            {"external_recipient": answer} if answer is not None else None
        ),
    )


def test_materially_unknown_release_recipient_causes_one_question():
    result = _compile()
    question = result["response_pack"]["critical_question"]
    assert result["response_pack"]["state"] == "needs_clarification"
    assert question["question_id"] == "external_recipient"
    assert result["situation"]["explicit_external_actions"] == [
        "external_stakeholder"
    ]


@pytest.mark.parametrize(("answer", "expected"), [
    ("Parent or guardian", {"guardian"}),
    ("District Education Office", {"education_authority"}),
    ("Other external recipient", {"external_stakeholder"}),
])
def test_recipient_answer_closes_to_governed_recipient(answer, expected):
    result = _compile(answer)
    assert result["response_pack"]["critical_question"] is None
    assert set(result["situation"]["explicit_external_actions"]) == expected
    gates = {
        item["recipient_type"]
        for item in result["response_pack"]["deliverables"]
        if item["kind"] == "external_action"
    }
    assert gates == expected


def test_draft_only_answer_removes_release_action_without_removing_draft():
    result = _compile("Draft only - do not send")
    assert result["response_pack"]["critical_question"] is None
    assert result["situation"]["explicit_external_actions"] == []
    assert any(
        item["kind"] == "artifact"
        for item in result["response_pack"]["deliverables"]
    )
    assert not any(
        item["kind"] == "external_action"
        for item in result["response_pack"]["deliverables"]
    )
