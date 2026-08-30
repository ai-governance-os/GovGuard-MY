from __future__ import annotations

import pytest

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer


class _AuditLLM:
    backend = "openai"

    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, **_kwargs):
        return self.payload


def _audit(
    payload,
    *,
    source="The school was not closed.",
    artifact="The school was closed.",
    role="internal_incident_report",
    purpose="draft report",
    target="report.md",
    source_policy="",
):
    action = CandidateAction(
        action_id="a1", tool="fs", operation="save_under_outputs",
        purpose=purpose, target=target,
        metadata={
            "artifact_role": role,
            "audience": "internal",
            "source_policy": source_policy,
        },
    )
    return ContentSynthesizer(chat_llm=_AuditLLM(payload))._school_fact_grounding_audit(
        [action], {"a1": artifact}, source,
    )


@pytest.mark.parametrize("payload", [
    {},
    {"malformed": "x"},
    {"pass": False},
    {"pass": True},
    {"pass": True, "unsupported_claims": "none"},
])
def test_semantic_fact_audit_fails_closed_on_malformed_or_incomplete_json(payload):
    assert _audit(payload)["pass"] is False


def test_semantic_fact_audit_accepts_only_explicit_clean_schema():
    assert _audit({"pass": True, "unsupported_claims": []}) == {
        "pass": True, "issues": [],
    }


@pytest.mark.parametrize(("source", "claim"), [
    ("The school was not closed.", "The school was closed."),
    ("Ali's parent was not contacted.", "Ali's parent was contacted."),
])
def test_semantic_fact_audit_never_discards_a_reported_polarity_error(source, claim):
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The output reverses the source polarity.",
        }],
    }
    result = _audit(payload, source=source, artifact=claim)
    assert result["pass"] is False
    assert any(claim in issue for issue in result["issues"])


def test_semantic_fact_audit_accepts_source_grounded_negative_paraphrase():
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": "Maintenance contacted: No (as of time of report)",
            "reason": "Not supported",
        }],
    }
    result = _audit(
        payload,
        source="Maintenance has not yet been contacted.",
        artifact="Maintenance contacted: No (as of time of report)",
    )
    assert result == {"pass": True, "issues": []}


@pytest.mark.parametrize("claim", [
    "The argument took place in the staff office.",
    "The teachers have been separated.",
    "No injuries were reported.",
])
def test_semantic_fact_audit_accepts_high_overlap_source_paraphrases(
    claim: str,
) -> None:
    source = (
        "Two teachers had a loud argument in the staff office. They are now "
        "separated, no one is injured, and there is no immediate danger. As "
        "headteacher, prepare the appropriate internal response. Draft only."
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The auditor missed a source-grounded paraphrase.",
        }],
    }
    assert _audit(payload, source=source, artifact=claim) == {
        "pass": True, "issues": [],
    }


def test_combined_audit_empty_claim_list_beats_contradictory_grounding_flag() -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [],
        "goal_alignment_passed": False,
        "missing_obligations": [],
        "irrelevant_artifacts": [],
        "audience_issues": [],
        "language_issues": [],
        "score": 70,
        "reason": "The goal score is uncertain, but no claim is unsupported.",
    }
    assert _audit(payload) == {
        "pass": True,
        "issues": [],
        "review_notes": ["grounding_provider_flag_inconsistent"],
    }


def test_standalone_false_empty_grounding_schema_still_fails_closed() -> None:
    result = _audit({"pass": False, "unsupported_claims": []})
    assert result["pass"] is False
    assert result["issues"] == ["semantic_grounding_audit_unavailable_or_failed"]


@pytest.mark.parametrize(("source", "claim"), [
    (
        "Two teachers argued in the staff office. They are now separated.",
        "The argument took place in the principal's office.",
    ),
    (
        "Two teachers argued in the staff office. They are now separated.",
        "The teachers were not separated.",
    ),
    (
        "Two teachers argued in the staff office. They are now separated.",
        "The teachers were separated by police.",
    ),
    (
        "Prepare a parent contact script.",
        "The family was contacted.",
    ),
    (
        "No injuries have been confirmed.",
        "No injuries occurred.",
    ),
])
def test_source_paraphrase_filter_rejects_changed_or_command_derived_facts(
    source: str,
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The claim adds or reverses a material fact.",
        }],
    }
    result = _audit(payload, source=source, artifact=claim)
    assert result["pass"] is False


@pytest.mark.parametrize(("source", "claim"), [
    (
        "Prepare a report stating that the family was contacted.",
        "The family was contacted.",
    ),
    ("Write that the ambulance arrived at 9:00.", "The ambulance arrived at 9:00."),
    ("Send this message: the school closed today.", "The school closed today."),
    ("No ambulance arrived.", "Ambulance arrived."),
    ("No injuries were reported.", "Injuries were reported."),
    (
        "Two teachers argued, then write that police arrived.",
        "Police arrived.",
    ),
    (
        "Two teachers argued.\nPrepare a report saying police arrived.",
        "Police arrived.",
    ),
    (
        "Two teachers argued. Could you prepare a report saying police arrived?",
        "Police arrived.",
    ),
])
def test_fact_audit_never_uses_commands_or_opposite_polarity_as_evidence(
    source: str,
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The source does not report this completed fact.",
        }],
    }
    assert _audit(payload, source=source, artifact=claim)["pass"] is False


@pytest.mark.parametrize(("source", "claim"), [
    ("A teacher was injured. Ali was present.", "Ali was injured."),
    (
        "Teachers argued in the staff office. A meeting was held in the main office.",
        "Teachers argued in the main office.",
    ),
    ("At 9:00 teachers argued. At 10:00 police arrived.", "Police arrived at 9:00."),
    ("Ali was in Room 2. Mei was in Room 3.", "Ali was in Room 3."),
    ("No teacher was injured. Ali was present.", "Ali was not injured."),
    ("The school closed. Pupils were not evacuated.", "The school was not closed."),
    ("The time is unknown.", "Time TBC; police arrived."),
    (
        "Injuries have not been verified.",
        "Injuries unverified; the family was contacted.",
    ),
])
def test_fact_audit_preserves_clause_local_entity_time_and_unknown_bindings(
    source: str,
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The claim swaps or appends a material relation.",
        }],
    }
    assert _audit(payload, source=source, artifact=claim)["pass"] is False


def test_semantic_fact_audit_keeps_proposals_under_parent_heading():
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": "Identify an alternative room such as the library or hall.",
            "reason": "The source did not assign a room.",
        }],
    }
    artifact = (
        "# Relocation checklist\n\n"
        "## Proposed arrangements - subject to school approval\n\n"
        "### Before relocation\n\n"
        "- Identify an alternative room such as the library or hall.\n"
    )
    assert _audit(payload, source="Prepare a relocation checklist.", artifact=artifact) == {
        "pass": True, "issues": [],
    }


@pytest.mark.parametrize(("claim", "artifact"), [
    (
        "Staff should be separated and spoken to individually.",
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        "- Staff should be separated and spoken to individually.\n",
    ),
    (
        "Guru boleh ditemu bual secara berasingan.",
        "# Laporan dalaman\n\n"
        "## Cadangan — tertakluk kepada semakan manusia\n\n"
        "- Guru boleh ditemu bual secara berasingan.\n",
    ),
    (
        "建议由中立人员分别了解情况。",
        "# 内部报告\n\n"
        "## 建议的后续步骤 — 须经人工审核\n\n"
        "- 建议由中立人员分别了解情况。\n",
    ),
])
def test_semantic_fact_audit_accepts_future_modal_multilingual_advice(
    claim: str,
    artifact: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": claim,
            "reason": "The source did not state that this step occurred.",
        }],
    }
    assert _audit(
        payload,
        source="Prepare an internal response to a staff disagreement.",
        artifact=artifact,
    ) == {"pass": True, "issues": []}


@pytest.mark.parametrize("claim", [
    "Conduct a thorough inspection of the affected area.",
    "Ensure that the room remains closed until a safety assessment is completed.",
    "Notify staff of any approved classroom changes.",
    "Schedule a leadership review if required.",
    "Arrange alternative transport if the bus cannot be repaired.",
    "Consider mediation or conflict resolution strategies to prevent future occurrences.",
    "Document the decision after human approval.",
    "Communicate verified delays to affected families.",
])
def test_semantic_fact_audit_accepts_imperative_advice_under_qualified_heading(
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The source did not state that this step occurred.",
        }],
    }
    artifact = (
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        f"- {claim}\n"
    )
    assert _audit(
        payload,
        source="Prepare an internal report from the supplied facts.",
        artifact=artifact,
    ) == {"pass": True, "issues": []}


def test_semantic_fact_audit_does_not_treat_bare_imperative_as_authorised_advice() -> None:
    claim = "Notify every parent immediately."
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "No proposal or human-review boundary is present.",
        }],
    }
    result = _audit(
        payload,
        source="Prepare an internal report.",
        artifact=f"# Internal report\n\n{claim}\n",
    )
    assert result["pass"] is False


@pytest.mark.parametrize(
    "claim",
    [
        "Assess the situation and determine the safest transport option.",
        "Keep parents informed about verified delays and expected timelines.",
    ],
)
def test_plan_role_supplies_proposal_boundary_for_safe_imperatives(
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The source did not say this action already occurred.",
        }],
    }
    assert _audit(
        payload,
        source="The school bus broke down. Prepare a transport response plan.",
        artifact=f"# Transport response plan\n\n- {claim}\n",
        role="transport_response_plan",
        purpose="prepare a proposed transport response plan",
        target="transport_response_plan.md",
    ) == {"pass": True, "issues": []}


def test_plan_role_cannot_launder_completed_fact_inside_imperative() -> None:
    claim = "Keep parents informed that police completed the investigation."
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The embedded completed event is unsupported.",
        }],
    }
    assert _audit(
        payload,
        source="Prepare a transport response plan.",
        artifact=f"# Transport response plan\n\n- {claim}\n",
        role="transport_response_plan",
        purpose="prepare a proposed transport response plan",
        target="transport_response_plan.md",
    )["pass"] is False


def test_incomplete_official_source_control_is_not_an_event_claim() -> None:
    claim = "Official-source check: REQUIRED - not yet completed"
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The source did not state that this check occurred.",
        }],
    }
    assert _audit(
        payload,
        source="Prepare an internal safety draft.",
        artifact=f"# Safety draft\n\n> {claim}.\n",
        role="site_safety_checklist",
        purpose="prepare a safety checklist",
        target="site_safety_checklist.md",
        source_policy="official_verification_required",
    ) == {"pass": True, "issues": []}


def test_incomplete_status_cannot_hide_second_completed_fact() -> None:
    claim = (
        "Official-source check: REQUIRED - not yet completed; "
        "the family was contacted."
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The second event is unsupported.",
        }],
    }
    assert _audit(
        payload,
        source="Prepare an internal safety draft.",
        artifact=f"# Safety draft\n\n> {claim}\n",
        role="site_safety_checklist",
        purpose="prepare a safety checklist",
        target="site_safety_checklist.md",
        source_policy="official_verification_required",
    )["pass"] is False


@pytest.mark.parametrize(
    ("source", "claim", "role"),
    [
        (
            "Part of a classroom ceiling fell. Prepare an internal report.",
            "TBC",
            "internal_incident_report",
        ),
        (
            "The bus broke down. Prepare a transport response plan.",
            "Proposed arrangements - subject to school approval",
            "transport_response_plan",
        ),
        (
            "Eight pupils became unwell. Prepare a private parent notice.",
            "If you have any concerns or questions, feel free to reach out to the school administration.",
            "private_parent_notice",
        ),
        (
            "A false rumour says a pupil died at school. Prepare a public statement.",
            "We want to clarify that this information is not accurate.",
            "public_communication_draft",
        ),
    ],
)
def test_common_epistemic_or_draft_framing_false_positives_are_rescued(
    source: str,
    claim: str,
    role: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The provider misread draft framing as a case fact.",
        }],
    }
    assert _audit(
        payload,
        source=source,
        artifact=f"# Draft\n\n{claim}\n",
        role=role,
        purpose="prepare the governed draft",
    ) == {"pass": True, "issues": []}


def test_false_rumour_framing_does_not_excuse_an_added_completed_event() -> None:
    claim = (
        "We want to clarify that this information is not accurate; "
        "the family was contacted."
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The second event was not supplied.",
        }],
    }
    assert _audit(
        payload,
        source="A false rumour concerns the school. Prepare a public statement.",
        artifact=f"# Public statement\n\n{claim}\n",
        role="public_communication_draft",
    )["pass"] is False


def test_qualified_consider_advice_cannot_launder_completed_fact() -> None:
    claim = "Consider that police completed the investigation."
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The embedded completed event is not in source.",
        }],
    }
    artifact = (
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        f"- {claim}\n"
    )
    assert _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=artifact,
    )["pass"] is False


@pytest.mark.parametrize("claim", [
    "Consider that the family knows about the incident.",
    "Consider the principal approved the budget.",
    "Consider the inspection passed and the room safe.",
    "We could consider that the family knows about the incident.",
    "We should consider the principal approved the budget.",
    "Boleh pertimbangkan bahawa keluarga mengetahui kejadian itu.",
    "可以考虑家长知道此事。",
])
def test_qualified_consider_wording_cannot_launder_assertions(claim: str) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The wording embeds an unsupported assertion.",
        }],
    }
    artifact = (
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        f"- {claim}\n"
    )
    assert _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=artifact,
    )["pass"] is False


@pytest.mark.parametrize(
    ("claim", "role", "purpose"),
    [
        (
            "It is essential to preserve all evidence related to this incident.",
            "evidence_preservation_log",
            "Preserve an evidence and decision chronology log.",
        ),
        (
            "An assessment is required to determine if regulatory notifications are necessary.",
            "regulatory_notification_assessment",
            "Assess whether a regulatory notification is required.",
        ),
        (
            "This incident requires a structured response to ensure data privacy.",
            "cyber_incident_response",
            "Prepare a cyber and data privacy response plan.",
        ),
    ],
)
def test_semantic_fact_audit_accepts_narrow_contract_normative_purpose(
    claim: str,
    role: str,
    purpose: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The source did not state that this action happened.",
        }],
    }
    assert _audit(
        payload,
        source="Prepare the appropriate internal response. Draft only.",
        artifact=f"# Draft\n\n{claim}\n",
        role=role,
        purpose=purpose,
        target=f"{role}.md",
    ) == {"pass": True, "issues": []}


def test_semantic_fact_audit_does_not_let_recommendation_heading_launder_past_fact():
    claim = "The teachers were separated by the headteacher."
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": claim,
            "reason": "The source did not state that this action occurred.",
        }],
    }
    artifact = (
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        f"- {claim}\n"
    )
    result = _audit(
        payload,
        source="Prepare an internal response to a staff disagreement.",
        artifact=artifact,
    )
    assert result["pass"] is False
    assert any(claim in issue for issue in result["issues"])


@pytest.mark.parametrize("claim", [
    "Recommended: the family knows about the incident.",
    "Recommended: the pupil may have been injured.",
])
def test_semantic_fact_audit_does_not_treat_labelled_current_or_past_claim_as_advice(
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": claim,
            "reason": "The source did not state this fact.",
        }],
    }
    artifact = (
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        f"- {claim}\n"
    )
    result = _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=artifact,
    )
    assert result["pass"] is False
    assert any(claim in issue for issue in result["issues"])


def test_semantic_fact_audit_accepts_role_consistent_document_purpose_frame():
    claim = "This report is intended to support authorised human review."
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": claim,
            "reason": "The source did not state this process.",
        }],
    }
    assert _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=f"# Internal incident report\n\n{claim}\n",
    ) == {"pass": True, "issues": []}


def test_semantic_fact_audit_rejects_wrong_role_purpose_frame():
    claim = "This checklist is intended to notify every parent."
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": claim,
            "reason": "This is not the report contract.",
        }],
    }
    result = _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=f"# Internal incident report\n\n{claim}\n",
    )
    assert result["pass"] is False


@pytest.mark.parametrize("claim", [
    "This draft is intended to confirm police have completed their investigation.",
    "This report is intended to state that the family was contacted.",
    "Laporan ini bertujuan untuk mengesahkan bahawa keluarga telah dihubungi.",
    "本报告旨在确认警方已完成调查。",
])
def test_contract_purpose_frame_cannot_launder_completed_case_fact(
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The sentence asserts an event not present in source.",
        }],
    }
    result = _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=f"# Internal incident report\n\n{claim}\n",
    )
    assert result["pass"] is False


@pytest.mark.parametrize("claim", [
    "Inform staff that inspection completed.",
    "Notify staff after the principal approved the budget and the room is confirmed.",
])
def test_qualified_advice_cannot_launder_embedded_completed_fact(
    claim: str,
) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The embedded completed event is not in source.",
        }],
    }
    artifact = (
        "# Internal report\n\n"
        "## Recommended next steps — subject to human review\n\n"
        f"- {claim}\n"
    )
    assert _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=artifact,
    )["pass"] is False


def test_non_operational_preparation_framing_becomes_review_note() -> None:
    claim = "We are currently preparing an appropriate response."
    action = CandidateAction(
        action_id="a1", tool="fs", operation="save_under_outputs",
        purpose="draft report", target="report.md",
        metadata={"artifact_role": "internal_incident_report"},
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The source did not state that preparation started.",
        }],
    }
    result = ContentSynthesizer(
        chat_llm=_AuditLLM(payload)
    )._school_fact_grounding_audit(
        [action], {"a1": f"# Draft\n\n{claim}\n"},
        "Prepare an internal incident report.",
    )
    assert result["pass"] is True
    assert result["review_notes"] == [
        f"semantic_grounding_review:a1:{claim}"
    ]
    assert action.metadata["school_claim_ledger"] == [{
        "claim": claim,
        "disposition": "review_note",
        "source": "semantic_auditor",
    }]


def test_false_rumour_awareness_framing_is_review_only_when_topic_matches() -> None:
    claim = (
        "We are aware of a false rumour circulating on social media "
        "regarding the death of a pupil at our school."
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "Awareness was not explicitly reported.",
        }],
    }
    result = _audit(
        payload,
        source=(
            "A false rumour on Facebook says a pupil died at school. "
            "Prepare a public statement."
        ),
        artifact=f"# Public statement\n\n{claim}\n",
        role="public_communication_draft",
    )
    assert result["pass"] is True
    assert result["review_notes"]


def test_false_rumour_public_assurance_is_source_grounded() -> None:
    claim = (
        "We want to assure our community that this information is not true."
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The auditor treated the bounded correction as new fact.",
        }],
    }
    assert _audit(
        payload,
        source=(
            "A false rumour on Facebook says a pupil died at school. "
            "Prepare a public statement."
        ),
        artifact=f"# Public statement\n\n{claim}\n",
        role="public_communication_draft",
    ) == {"pass": True, "issues": []}


def test_false_rumour_assurance_cannot_hide_completed_action() -> None:
    claim = (
        "We want to assure our community that this information is not true; "
        "the family was contacted."
    )
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The appended action is not in the source.",
        }],
    }
    result = _audit(
        payload,
        source=(
            "A false rumour on Facebook says a pupil died at school. "
            "Prepare a public statement."
        ),
        artifact=f"# Public statement\n\n{claim}\n",
        role="public_communication_draft",
    )
    assert result["pass"] is False
    assert any(claim in issue for issue in result["issues"])


@pytest.mark.parametrize("claim", [
    "We are aware that police arrived.",
    "We are currently preparing medical treatment for the pupil.",
    "We are aware of a false rumour that police arrested a teacher.",
])
def test_soft_framing_cannot_hide_material_unsupported_claim(claim: str) -> None:
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The material event is not in the source.",
        }],
    }
    result = _audit(
        payload,
        source="Prepare an internal incident report.",
        artifact=f"# Internal report\n\n{claim}\n",
    )
    assert result["pass"] is False
    assert any(claim in issue for issue in result["issues"])
