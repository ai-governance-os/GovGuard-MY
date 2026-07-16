"""Regression coverage for the task-local school-provider outage circuit."""

from __future__ import annotations

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer
from teow_agl.runtime import _verified_deterministic_school_fallback


SOURCE = "A snake was reported at school and reportedly bit a student."


def _artifact(index: int, role: str) -> CandidateAction:
    source_policy = (
        "official_verification_required"
        if role in {
            "site_safety_checklist",
            "emergency_contact_script",
            "fire_rescue_contact_script",
        }
        else "reported_facts_only"
    )
    return CandidateAction(
        action_id=f"artifact_{index}",
        tool="fs",
        operation="write",
        target=f"artifact_{index}.md",
        purpose=f"Prepare the governed {role.replace('_', ' ')}",
        metadata={
            "school_output_contract": {"format": "markdown"},
            "school_content_role": "artifact",
            "coverage_source": "school_response_pack",
            "artifact_id": f"artifact_{index}",
            "artifact_role": role,
            "audience": "internal",
            "release_state": "draft_only",
            "source_policy": source_policy,
            "source_request": SOURCE,
            "school_case_summary": SOURCE,
            "school_known_facts": [],
            "school_unknowns": [],
            "source_fact_ids": [],
            "requested_languages": [],
            "claim_policy": "reported_facts_only",
            "sibling_artifacts": [],
        },
    )


def test_outage_calls_live_provider_once_across_more_than_six_artifacts() -> None:
    class OutageProvider:
        backend = "openai"

        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, **_kwargs) -> dict:
            self.calls += 1
            return {}

    roles = [
        "internal_incident_report",
        "private_parent_notice",
        "medical_handover_script",
        "site_safety_checklist",
        "emergency_contact_script",
        "student_accountability_checklist",
        "fire_rescue_contact_script",
    ]
    actions = [_artifact(index, role) for index, role in enumerate(roles)]
    provider = OutageProvider()
    synthesizer = ContentSynthesizer(chat_llm=provider)

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert len(actions) == 7
    assert result["batch_count"] == 2
    assert provider.calls == 1
    assert synthesizer._school_provider_unavailable is True
    assert result["batches"][0]["issues"] == [
        "provider_unavailable_or_timeout"
    ]
    assert result["batches"][1]["issues"] == ["provider_circuit_open"]
    assert all(action.metadata.get("content") for action in actions)
    assert _verified_deterministic_school_fallback(actions) is True

    # One live-authored file makes the bundle semantic-judge eligible again;
    # the outage exemption is deliberately all-or-nothing.
    actions[0].metadata["school_generation_validation"] = {
        "pass": True, "mode": "plan_level_action_id_mapping",
    }
    assert _verified_deterministic_school_fallback(actions) is False
