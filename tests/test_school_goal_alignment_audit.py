from __future__ import annotations

import pytest

from teow_agl.adapters.chat_llm import ChatLLM
from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer


class _StrictAdapter(ChatLLM):
    """Production-shaped adapter with a deterministic test response."""

    def __init__(self, payload: dict) -> None:
        super().__init__(backend="openai")
        self.payload = payload
        self.calls = 0
        self.last_kwargs: dict = {}

    def chat_json(self, **kwargs) -> dict:
        self.calls += 1
        self.last_kwargs = kwargs
        return self.payload


class _LegacyInjectedAdapter:
    backend = "deepseek"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def chat_json(self, **_kwargs) -> dict:
        self.calls += 1
        return self.payload


def _action(
    action_id: str = "parent_notice",
    *,
    purpose: str = "Draft a private bilingual notice to the parent",
    audience: str = "private_recipient",
    channel: str = "private_parent_message",
    languages: list[str] | None = None,
) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        tool="fs",
        operation="save_under_outputs",
        purpose=purpose,
        target=f"{action_id}.md",
        metadata={
            "artifact_role": "private_parent_notice",
            "audience": audience,
            "channel": channel,
            "requested_languages": (
                languages if languages is not None else ["en", "ms"]
            ),
            "source_request": (
                "Draft a private bilingual English and Malay notice to the "
                "parent about Friday's recycling activity."
            ),
        },
    )


def _body() -> str:
    return (
        "# Private Parent Notice\n\n"
        "> DRAFT - NOT SENT | Private recipient only\n\n"
        "## English\n\nFriday's recycling activity is planned.\n\n"
        "## Bahasa Melayu\n\nAktiviti kitar semula dirancang pada hari Jumaat."
    )


def _clean_payload() -> dict:
    return {
        "pass": True,
        "unsupported_claims": [],
        "goal_alignment_passed": True,
        "missing_obligations": [],
        "irrelevant_artifacts": [],
        "audience_issues": [],
        "language_issues": [],
        "score": 96,
        "reason": "The requested bilingual private notice is complete.",
    }


def test_goal_alignment_clean_schema_returns_required_result_fields() -> None:
    action = _action()
    result = ContentSynthesizer(
        chat_llm=_StrictAdapter(_clean_payload())
    )._school_goal_alignment_audit(
        [action], {action.action_id: _body()}, action.metadata["source_request"],
        audit_data=_clean_payload(),
    )

    assert result == {
        "goal_alignment_passed": True,
        "missing_obligations": [],
        "irrelevant_artifacts": [],
        "audience_issues": [],
        "language_issues": [],
        "score": 96,
        "reason": "The requested bilingual private notice is complete.",
    }


@pytest.mark.parametrize(
    ("field", "issue"),
    [
        (
            "missing_obligations",
            {"obligation": "Malay section", "reason": "Only English exists."},
        ),
        (
            "irrelevant_artifacts",
            {"action_id": "cyber_plan", "reason": "Not requested."},
        ),
        (
            "audience_issues",
            {"action_id": "parent_notice", "issue": "Addressed to all staff."},
        ),
        (
            "language_issues",
            {"action_id": "parent_notice", "issue": "Malay is incomplete."},
        ),
    ],
)
def test_each_goal_alignment_dimension_independently_blocks_pass(
    field: str,
    issue: dict,
) -> None:
    payload = _clean_payload()
    payload["goal_alignment_passed"] = False
    payload[field] = [issue]
    payload["score"] = 55
    payload["reason"] = "The bundle does not fulfil the raw user goal."
    action = _action()

    result = ContentSynthesizer(
        chat_llm=_StrictAdapter(payload)
    )._school_goal_alignment_audit(
        [action], {action.action_id: _body()}, action.metadata["source_request"],
        audit_data=payload,
    )

    assert result["goal_alignment_passed"] is False
    assert result[field]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"goal_alignment_passed": True},
        {
            "goal_alignment_passed": True,
            "missing_obligations": [],
            "irrelevant_artifacts": [],
            "audience_issues": [],
            "language_issues": [],
            "score": 100,
            "reason": "",
        },
    ],
)
def test_production_adapter_fails_closed_on_incomplete_alignment_schema(
    payload: dict,
) -> None:
    action = _action()
    result = ContentSynthesizer(
        chat_llm=_StrictAdapter(payload)
    )._school_goal_alignment_audit(
        [action], {action.action_id: _body()}, action.metadata["source_request"],
        audit_data=payload,
    )

    assert result["goal_alignment_passed"] is False
    assert result["score"] == 0


def test_low_score_cannot_contradictorily_pass_with_empty_issue_lists() -> None:
    payload = _clean_payload()
    payload["score"] = 70
    action = _action()
    result = ContentSynthesizer(
        chat_llm=_StrictAdapter(payload)
    )._school_goal_alignment_audit(
        [action], {action.action_id: _body()}, action.metadata["source_request"],
        audit_data=payload,
    )

    assert result["goal_alignment_passed"] is False
    assert result["missing_obligations"]


def test_combined_audit_uses_one_call_and_requires_both_dimensions() -> None:
    action = _action()
    clean_adapter = _StrictAdapter(_clean_payload())
    clean = ContentSynthesizer(
        chat_llm=clean_adapter
    )._school_bundle_semantic_audit(
        [action], {action.action_id: _body()}, action.metadata["source_request"],
    )
    assert clean["pass"] is True
    assert clean["grounding_passed"] is True
    assert clean["goal_alignment_passed"] is True
    assert clean_adapter.calls == 1

    unsupported = _clean_payload()
    unsupported["pass"] = False
    unsupported["unsupported_claims"] = [{
        "action_id": action.action_id,
        "claim": "The notice was sent.",
        "reason": "The request asks only for a draft.",
    }]
    rejected = ContentSynthesizer(
        chat_llm=_StrictAdapter(unsupported)
    )._school_bundle_semantic_audit(
        [action], {action.action_id: _body()}, action.metadata["source_request"],
    )
    assert rejected["pass"] is False
    assert rejected["grounding_passed"] is False
    assert rejected["goal_alignment_passed"] is True


def test_legacy_fake_adapter_keeps_old_clean_grounding_tests_compatible() -> None:
    action = _action(languages=[])
    adapter = _LegacyInjectedAdapter({"pass": True, "unsupported_claims": []})
    result = ContentSynthesizer(
        chat_llm=adapter
    )._school_bundle_semantic_audit(
        [action], {action.action_id: "# Draft\n\nDRAFT - NOT SENT"},
        action.metadata["source_request"],
    )

    assert result["pass"] is True
    assert result["goal_alignment_passed"] is True
    assert result["compatibility_mode"] == "legacy_injected_test_adapter"
    assert adapter.calls == 1


def test_legacy_fake_still_catches_missing_requested_language_section() -> None:
    action = _action()
    adapter = _LegacyInjectedAdapter({"pass": True, "unsupported_claims": []})
    result = ContentSynthesizer(
        chat_llm=adapter
    )._school_bundle_semantic_audit(
        [action], {action.action_id: "# Notice\n\n## English\n\nDraft only."},
        action.metadata["source_request"],
    )

    assert result["pass"] is False
    assert result["language_issues"]
    assert any(issue.startswith("goal_alignment:language:") for issue in result["issues"])


def test_combined_audit_knows_governed_transform_is_the_goal_contract() -> None:
    action = _action(
        action_id="restricted_support",
        purpose="Prepare a restricted need-to-know student support plan",
        audience="internal",
        channel="",
        languages=["en"],
    )
    action.metadata.update({
        "artifact_role": "student_support_plan",
        "safe_transformation": (
            "Replace the broad staff disclosure with a restricted, need-to-know "
            "support plan."
        ),
        "excluded_data_concepts": [
            "pupil_names", "diagnoses", "medication", "counselling_notes"
        ],
        "restricted_internal_audience": True,
        "audience_boundary": "authorised_support_team",
        "source_request": (
            "Prepare an all-teachers message listing pupil names, diagnoses, "
            "medication and counselling notes."
        ),
    })
    adapter = _StrictAdapter(_clean_payload())
    result = ContentSynthesizer(
        chat_llm=adapter
    )._school_bundle_semantic_audit(
        [action],
        {action.action_id: (
            "# Restricted Student Support Plan\n\nDRAFT - NOT SENT\n\n"
            "Access is limited to the authorised support team."
        )},
        action.metadata["source_request"],
    )
    assert result["pass"] is True
    prompt = str(adapter.last_kwargs.get("system") or "") + str(
        adapter.last_kwargs.get("user") or ""
    )
    assert "safe_transformation" in prompt
    assert "excluded_data_concepts" in prompt
    assert "must not be reported as a missing obligation" in prompt
