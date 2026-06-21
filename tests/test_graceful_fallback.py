"""Phase A4 — user-level fallback.

When Module 102 cannot produce a plan (Groq 413 / 429 / network error),
Module 102R builds a recovery plan. The recovery message the user sees
MUST be:
  * in the user's own language (Chinese question -> Chinese reply)
  * free of internal jargon ("planner error", "build plan failed", …)
  * actionable (suggest a retry)

Before this fix the `unknown` recovery template carried a hardcoded
English string "I hit a planner error before I could build a useful
plan." — shown verbatim even to Chinese users.
"""
from __future__ import annotations

import json

from teow_agl.models import PlannerRefusal
from teow_agl.modules.module_102r_refusal_recovery import (
    RefusalRecoveryModule,
    _graceful_fallback_body,
    _has_cjk,
)


# --- _graceful_fallback_body ----------------------------------------------

def test_fallback_body_chinese_for_chinese_intent():
    body = _graceful_fallback_body("你是谁？")
    assert _has_cjk(body)
    assert "抱歉" in body


def test_fallback_body_english_for_english_intent():
    body = _graceful_fallback_body("who are you?")
    assert not _has_cjk(body)
    assert "Sorry" in body


def test_fallback_body_never_leaks_internals():
    """The message must not expose pipeline internals."""
    for intent in ["你是谁？", "what is this", "写一份报告"]:
        body = _graceful_fallback_body(intent).lower()
        for leak in ["planner", "413", "429", "payload", "refusal",
                     "model_error", "build plan", "exception", "traceback"]:
            assert leak not in body, f"leaked {leak!r} for intent {intent!r}"


# --- 102R recovery integration --------------------------------------------

_TEMPLATES = json.load(open(
    "configs/refusal_recovery_templates.json", encoding="utf-8"))


def _refusal(task_id: str = "t1") -> PlannerRefusal:
    return PlannerRefusal(
        refusal_id="r1", task_id=task_id, planner_id="groq_qwen_qwen3_32b",
        refusal_type="model_error",
        message="Client error '413 Payload Too Large'",
        raw_output_hash="", recovery_allowed=True,
    )


def test_recovery_unknown_chinese_question_gets_chinese_body():
    mod = RefusalRecoveryModule(_TEMPLATES)
    plan = mod.recover(
        refusal=_refusal(),
        planning_brief={"task_category": "unknown", "user_intent": "你是谁？"},
    )
    assert plan.used_refusal_recovery is True
    action = plan.actions[0]
    assert action.tool == "chat"
    assert action.operation == "answer"
    body = action.metadata.get("body", "")
    assert _has_cjk(body), f"expected Chinese body, got {body!r}"


def test_recovery_unknown_english_question_gets_english_body():
    mod = RefusalRecoveryModule(_TEMPLATES)
    plan = mod.recover(
        refusal=_refusal(),
        planning_brief={"task_category": "unknown",
                        "user_intent": "explain quantum tunnelling"},
    )
    body = plan.actions[0].metadata.get("body", "")
    assert body and not _has_cjk(body)


def test_recovery_body_no_longer_hardcoded_english_error():
    """Regression guard: the old 'I hit a planner error' string is gone."""
    mod = RefusalRecoveryModule(_TEMPLATES)
    plan = mod.recover(
        refusal=_refusal(),
        planning_brief={"task_category": "unknown", "user_intent": "你好吗"},
    )
    body = plan.actions[0].metadata.get("body", "")
    assert "hit a planner error" not in body
    assert "planner" not in body.lower()


def test_recovery_chat_action_has_nonempty_body():
    """The chat tool fails on an empty body — the recovery must fill it."""
    mod = RefusalRecoveryModule(_TEMPLATES)
    plan = mod.recover(
        refusal=_refusal(),
        planning_brief={"task_category": "unknown", "user_intent": "test"},
    )
    assert plan.actions[0].metadata.get("body", "").strip() != ""
