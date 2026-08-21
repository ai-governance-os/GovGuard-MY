"""Complete-bundle semantic audit coverage for large school response packs."""

from __future__ import annotations

import re

import pytest

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer,
    _repair_bracket_placeholders,
    _school_response_pack_safe_fallback,
)


SOURCE = (
    "Prepare a governed response pack for a school community event using only "
    "the supplied facts. Do not send or publish anything."
)
ROLES = (
    "internal_incident_report",
    "event_action_plan",
    "staff_internal_notice",
    "school_parent_notice",
    "evidence_preservation_log",
    "finance_procurement_memo",
    "external_stakeholder_message",
    "education_authority_report",
    "regulatory_notification_assessment",
    "post_incident_review",
)


def _artifact(index: int, role: str) -> CandidateAction:
    return CandidateAction(
        action_id=f"large_pack_{index}",
        tool="fs",
        operation="save_under_outputs",
        target=f"large_pack_{index}.md",
        purpose=f"Prepare the governed {role.replace('_', ' ')}",
        metadata={
            "school_output_contract": {"format": "markdown"},
            "school_content_role": "artifact",
            "coverage_source": "school_response_pack",
            "artifact_id": f"large_pack_{index}",
            "artifact_role": role,
            "audience": "internal",
            "release_state": "draft_only",
            "source_policy": "reported_facts_only",
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


class _CompleteBundleProvider:
    backend = "deepseek"

    def __init__(
        self,
        actions: list[CandidateAction],
        *,
        audit_passes: bool = True,
        bad_action_id: str = "",
        deterministic_bad_action_id: str = "",
    ) -> None:
        self.bodies = {
            action.action_id: _school_response_pack_safe_fallback(
                action, SOURCE
            )
            for action in actions
        }
        self.audit_passes = audit_passes
        self.bad_action_id = bad_action_id
        self.deterministic_bad_action_id = deterministic_bad_action_id
        self.generation_calls = 0
        self.generation_action_ids: list[list[str]] = []
        self.audit_calls = 0
        self.per_file_calls = 0
        self.audit_users: list[str] = []

    def chat_json(
        self, system: str, user: str, *, max_tokens: int = 1500,
    ) -> dict:
        if "independent user-goal alignment auditor" in system:
            self.audit_calls += 1
            self.audit_users.append(user)
            if self.bad_action_id:
                return {
                    "pass": False,
                    "unsupported_claims": [{
                        "action_id": self.bad_action_id,
                        "claim": "The school has already sent the notice.",
                        "reason": "The source does not state that it was sent.",
                    }],
                    "goal_alignment_passed": True,
                    "missing_obligations": [],
                    "irrelevant_artifacts": [],
                    "audience_issues": [],
                    "language_issues": [],
                    "score": 95,
                    "reason": "One file contains an unsupported claim.",
                }
            if self.audit_passes:
                return {
                    "pass": True,
                    "unsupported_claims": [],
                    "goal_alignment_passed": True,
                    "missing_obligations": [],
                    "irrelevant_artifacts": [],
                    "audience_issues": [],
                    "language_issues": [],
                    "score": 96,
                    "reason": "The complete artifact bundle fulfils the goal.",
                }
            return {
                "pass": True,
                "unsupported_claims": [],
                "goal_alignment_passed": False,
                "missing_obligations": [{
                    "obligation": "complete response pack",
                    "reason": "The complete bundle does not fulfil the goal.",
                }],
                "irrelevant_artifacts": [],
                "audience_issues": [],
                "language_issues": [],
                "score": 45,
                "reason": "The complete artifact bundle misses the goal.",
            }

        self.generation_calls += 1
        action_ids = re.findall(r'"action_id":\s*"([^"]+)"', user)
        self.generation_action_ids.append(action_ids)
        bodies = {
            action_id: self.bodies[action_id]
            for action_id in action_ids
        }
        if self.deterministic_bad_action_id in bodies:
            # A markdown code fence trips validate_school_markdown's hygiene
            # check independently of the 2026-08-18 bracket-placeholder
            # repair (module_102b_synthesizer._repair_bracket_placeholders),
            # which now deliberately fixes a lone "[Your Name]" instead of
            # failing the whole artifact.
            bodies[self.deterministic_bad_action_id] += "\n\n```"
        return {
            "artifacts": bodies
        }

    def chat(
        self, system: str, user: str, *, max_tokens: int = 1500,
    ) -> str:
        self.per_file_calls += 1
        raise AssertionError("large-pack failure must not fan out per file")


@pytest.mark.parametrize("artifact_count", [7, 8, 9, 10])
def test_large_pack_audits_complete_mapping_once(
    artifact_count: int,
) -> None:
    actions = [
        _artifact(index, role)
        for index, role in enumerate(ROLES[:artifact_count])
    ]
    provider = _CompleteBundleProvider(actions)
    synthesizer = ContentSynthesizer(chat_llm=provider)
    synthesizer.live_fast_path = True

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert result["result"] == "synthesized_verified_batched_action_bundle"
    assert result["batch_count"] == 2
    assert provider.generation_calls == 2
    assert provider.audit_calls == 1
    assert provider.per_file_calls == 0
    assert len(provider.audit_users) == 1
    assert all(
        action.action_id in provider.audit_users[0] for action in actions
    )
    assert all(
        batch["result"] == "synthesized_pending_bundle_audit"
        for batch in result["batches"]
    )
    for action in actions:
        validation = action.metadata["school_generation_validation"]
        assert validation["pass"] is True
        assert validation["mode"] == "plan_level_batched_action_id_mapping"
        assert validation["semantic_audit_passed"] is True
        assert validation["goal_alignment_passed"] is True


def test_bundle_level_gap_retains_clean_files_but_not_goal_complete() -> None:
    repaired = _repair_bracket_placeholders(
        "Dear [Parent Name],\nPrepared by [Your Name] on [Today's Date]."
    )
    assert "[" not in repaired
    assert "TBC - parent name" in repaired
    assert "TBC - authorised school representative" in repaired
    assert "TBC - date" in repaired

    actions = [
        _artifact(index, role)
        for index, role in enumerate(ROLES[:7])
    ]
    provider = _CompleteBundleProvider(actions, audit_passes=False)
    synthesizer = ContentSynthesizer(chat_llm=provider)
    synthesizer.live_fast_path = True

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert result["result"] == "partial_bundle_per_action_fallback"
    assert provider.generation_calls == 2
    assert provider.audit_calls == 1
    assert provider.per_file_calls == 0
    assert result["fallback_action_ids"] == []
    assert result["safe_fallback_action_ids"] == []
    assert result["retained_action_ids"] == [
        action.action_id for action in actions
    ]
    for action in actions:
        validation = action.metadata["school_generation_validation"]
        assert validation["mode"] == "partial_bundle_retained"
        assert validation["pass"] is True
        assert validation["goal_alignment_passed"] is False
        assert validation["bundle_goal_alignment_passed"] is False


def test_one_bad_file_is_quarantined_without_discarding_clean_siblings() -> None:
    actions = [
        _artifact(index, role)
        for index, role in enumerate(ROLES[:7])
    ]
    bad_action = actions[2]
    provider = _CompleteBundleProvider(
        actions,
        bad_action_id=bad_action.action_id,
    )
    synthesizer = ContentSynthesizer(chat_llm=provider)
    synthesizer.live_fast_path = True

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert result["result"] == "partial_bundle_per_action_fallback"
    assert provider.generation_calls == 2
    assert provider.audit_calls == 1
    assert provider.per_file_calls == 0
    assert result["safe_fallback_action_ids"] == [bad_action.action_id]
    assert set(result["retained_action_ids"]) == {
        action.action_id for action in actions if action is not bad_action
    }
    assert bad_action.metadata["school_generation_validation"]["mode"] == (
        "deterministic_response_pack_fallback"
    )
    for action in actions:
        assert action.metadata["school_generation_failed"] is False


def test_small_pack_also_quarantines_only_the_named_bad_file() -> None:
    actions = [
        _artifact(index, role)
        for index, role in enumerate(ROLES[:3])
    ]
    bad_action = actions[1]
    provider = _CompleteBundleProvider(
        actions,
        bad_action_id=bad_action.action_id,
    )
    synthesizer = ContentSynthesizer(chat_llm=provider)
    synthesizer.live_fast_path = True

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert result["result"] == "partial_bundle_per_action_fallback"
    # One bounded repair call is permitted under the unchanged checks.
    assert provider.generation_calls == 2
    assert provider.audit_calls == 2
    assert provider.generation_action_ids[1] == [bad_action.action_id]
    assert provider.per_file_calls == 0
    assert result["safe_fallback_action_ids"] == [bad_action.action_id]
    assert set(result["retained_action_ids"]) == {
        actions[0].action_id, actions[2].action_id,
    }
    assert bad_action.metadata["school_generation_validation"]["mode"] == (
        "deterministic_response_pack_fallback"
    )


def test_batched_deterministic_failure_keeps_clean_live_siblings() -> None:
    actions = [
        _artifact(index, role)
        for index, role in enumerate(ROLES[:7])
    ]
    bad_action = actions[2]
    provider = _CompleteBundleProvider(
        actions,
        deterministic_bad_action_id=bad_action.action_id,
    )
    synthesizer = ContentSynthesizer(chat_llm=provider)
    synthesizer.live_fast_path = True

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert result["result"] == "synthesized_batched_bundle_with_safe_fallback"
    # Two initial batches plus one isolated repair call for the failed file.
    assert provider.generation_calls == 3
    assert provider.audit_calls == 1
    assert provider.per_file_calls == 0
    assert result["safe_fallback_action_ids"] == [bad_action.action_id]
    assert set(result["live_action_ids"]) == {
        action.action_id for action in actions if action is not bad_action
    }
    assert bad_action.metadata["school_generation_validation"]["mode"] == (
        "deterministic_response_pack_fallback"
    )


def test_batch_prompt_does_not_expose_other_batch_action_ids_as_keys() -> None:
    actions = [
        _artifact(index, role)
        for index, role in enumerate(ROLES[:7])
    ]
    for action in actions:
        action.metadata["sibling_artifacts"] = [
            {
                "action_id": sibling.action_id,
                "artifact_id": sibling.metadata["artifact_id"],
                "role": sibling.metadata["artifact_role"],
                "target": sibling.target,
            }
            for sibling in actions if sibling is not action
        ]
    provider = _CompleteBundleProvider(actions)
    synthesizer = ContentSynthesizer(chat_llm=provider)
    synthesizer.live_fast_path = True

    result = synthesizer.enrich_school_plan(actions, user_intent=SOURCE)

    assert result["result"] == "synthesized_verified_batched_action_bundle"
    assert provider.generation_calls == 2
    assert result["safe_fallback_action_ids"] == []
    assert set(result["live_action_ids"]) == {
        action.action_id for action in actions
    }
