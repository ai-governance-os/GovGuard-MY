"""Regression contracts for per-artifact factual-source isolation."""
from __future__ import annotations

from unittest.mock import patch

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer,
    _school_response_pack_safe_fallback,
)
from teow_agl.modules.module_school_artifact_guard import (
    validate_school_markdown as real_validate_school_markdown,
)


def _action(
    action_id: str,
    source: str,
    *,
    role: str = "private_parent_notice",
    audience: str = "private_recipient",
    target: str = "parent_notification_draft.md",
) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        tool="fs",
        operation="save_under_outputs",
        target=target,
        purpose=f"Prepare only the {role.replace('_', ' ')}",
        metadata={
            "school_output_contract": {"format": "markdown"},
            "school_content_role": "artifact",
            "coverage_source": "school_response_pack",
            "artifact_id": action_id,
            "artifact_role": role,
            "audience": audience,
            "release_state": "draft_only",
            "source_policy": "reported_facts_only",
            "source_request": source,
            "school_case_summary": source,
            "school_known_facts": [],
            "school_unknowns": [],
            "source_fact_ids": [],
            "requested_languages": ["en"],
            "claim_policy": "reported_facts_only",
            "sibling_artifacts": [],
        },
    )


def test_bundle_current_turn_is_routing_context_not_sibling_evidence() -> None:
    snake_source = (
        "A snake was seen near the canteen. Prepare an internal safety record. "
        "The pupil condition is TBC."
    )
    bus_source = (
        "The school bus is delayed by flooding. Prepare a private parent notice. "
        "No pupil injury has been reported."
    )
    current_turn = (
        "Also draft both files. A helicopter transported everyone and the snake "
        "bit a teacher."
    )
    snake = _action(
        "snake_record",
        snake_source,
        role="internal_incident_report",
        audience="internal",
        target="snake_record.md",
    )
    bus = _action("bus_notice", bus_source)
    snake.metadata["sibling_artifacts"] = [bus.action_id]
    bus.metadata["sibling_artifacts"] = [snake.action_id]
    bodies = {
        snake.action_id: _school_response_pack_safe_fallback(snake, snake_source),
        bus.action_id: _school_response_pack_safe_fallback(bus, bus_source),
    }

    class DeepSeekBundleProvider:
        backend = "deepseek"

        def __init__(self) -> None:
            self.writer_prompt = ""
            self.audit_prompt = ""

        def chat_json(self, *, system: str, user: str, max_tokens: int) -> dict:
            if "fact-grounding auditor" in system:
                self.audit_prompt = user
                return {"pass": True, "unsupported_claims": []}
            self.writer_prompt = user
            return {"artifacts": bodies}

    provider = DeepSeekBundleProvider()
    validation_sources: dict[str, set[str]] = {}

    def _spy_validate(candidate, body, source_request):
        validation_sources.setdefault(candidate.action_id, set()).add(source_request)
        return real_validate_school_markdown(candidate, body, source_request)

    with patch(
        "teow_agl.modules.module_102b_synthesizer.validate_school_markdown",
        side_effect=_spy_validate,
    ):
        result = ContentSynthesizer(chat_llm=provider).enrich_school_plan(
            [snake, bus], user_intent=current_turn,
        )

    assert result["result"] == "synthesized_verified_action_bundle"
    assert provider.writer_prompt.startswith(
        "CURRENT TURN REQUEST (routing context only):\n" + current_turn
    )
    assert "SOURCE REQUEST (facts and constraints only):" not in provider.writer_prompt
    assert f'"source_request": "{snake_source}"' in provider.writer_prompt
    assert f'"source_request": "{bus_source}"' in provider.writer_prompt
    assert provider.audit_prompt.startswith(
        "CURRENT TURN CONTEXT (not cross-artifact evidence):\n" + current_turn
    )
    assert f'"source_request": "{snake_source}"' in provider.audit_prompt
    assert f'"source_request": "{bus_source}"' in provider.audit_prompt
    assert validation_sources == {
        snake.action_id: {snake_source},
        bus.action_id: {bus_source},
    }


def test_isolated_repair_uses_owned_source_not_sibling_current_turn() -> None:
    owned = (
        "A pupil felt dizzy during assembly. Prepare a private parent notice. "
        "Medical assessment and parent contact status are TBC."
    )
    sibling_only_turn = (
        "A separate laboratory incident involved a chemical spill and the fire "
        "service arrived. Also repair the parent notice."
    )
    action = _action("parent_notice", owned)
    accepted = _school_response_pack_safe_fallback(action, owned)

    class DeepSeekIsolatedProvider:
        backend = "deepseek"

        def __init__(self) -> None:
            self.writer_prompts: list[str] = []
            self.audit_prompts: list[str] = []

        def chat(self, *, system: str, user: str, max_tokens: int) -> str:
            self.writer_prompts.append(user)
            return accepted

        def chat_json(self, *, system: str, user: str, max_tokens: int) -> dict:
            self.audit_prompts.append(user)
            return {"pass": True, "unsupported_claims": []}

    provider = DeepSeekIsolatedProvider()
    validation_sources: list[str] = []

    def _spy_validate(candidate, body, source_request):
        validation_sources.append(source_request)
        return real_validate_school_markdown(candidate, body, source_request)

    with patch(
        "teow_agl.modules.module_102b_synthesizer.validate_school_markdown",
        side_effect=_spy_validate,
    ):
        result = ContentSynthesizer(chat_llm=provider).enrich(
            action, user_intent=sibling_only_turn,
        )

    assert result["result"] == "school_markdown_synthesized_verified"
    assert provider.writer_prompts
    assert f"SOURCE REQUEST:\n{owned}\n\n" in provider.writer_prompts[0]
    assert sibling_only_turn not in provider.writer_prompts[0]
    assert provider.audit_prompts
    assert f'"source_request": "{owned}"' in provider.audit_prompts[0]
    assert sibling_only_turn not in provider.audit_prompts[0]
    assert validation_sources and set(validation_sources) == {owned}
    assert action.metadata["content"] == accepted
