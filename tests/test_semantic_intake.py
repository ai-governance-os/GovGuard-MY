"""Module 101C — Semantic Intake (L2 intent classifier) tests.

Offline only: the LLM is always a fake. Covers the module's decision
table, the purity/abstain contract, and runtime integration (override
re-assessment, clarify direct plan, abstain = unchanged behavior).
"""
from __future__ import annotations

import json

import pytest

from teow_agl.modules.module_101c_semantic_intake import (
    SemanticIntakeModule,
    closed_categories_from_classifier,
)
from teow_agl.runtime import Runtime


# ===========================================================================
# Helpers
# ===========================================================================

class FakeLLM:
    """chat_json returns a canned payload; counts calls."""

    def __init__(self, payload: dict | None) -> None:
        self.payload = payload
        self.calls = 0

    def chat_json(self, system: str, user: str, *, max_tokens: int = 350) -> dict:
        self.calls += 1
        self.last_system = system
        self.last_user = user
        return dict(self.payload) if self.payload else {}


BASE_CONFIG = {
    "enabled": True,
    "max_input_chars": 1000,
    "max_tokens": 350,
    "confidence_thresholds": {"accept": 0.7, "clarify": 0.4},
    "category_descriptions": {"office_doc_generation": "make office docs"},
    "clarify_templates": {
        "cjk": "请说得具体一点。",
        "default": "Could you be more specific?",
    },
    "allow_clarify_on_none": True,
}

CLOSED = ["office_doc_generation", "file_delete", "research_report"]


def _module(payload, *, config=None, closed=None) -> tuple[SemanticIntakeModule, FakeLLM]:
    llm = FakeLLM(payload)
    mod = SemanticIntakeModule(
        config=config if config is not None else BASE_CONFIG,
        closed_categories=closed if closed is not None else CLOSED,
        chat_llm=llm,
    )
    return mod, llm


def _minimal_runtime(workspace, planner=None, **kwargs) -> Runtime:
    from teow_agl.modules.module_105_human_gate import HumanGate
    from teow_agl.tools.mock_tools import MockTool
    from teow_agl.adapters.mock_planner import MockPlanner

    tools = {n: MockTool(n) for n in
             ["fs", "report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "memory", "chat",
              "image_gen"]}
    return Runtime(
        config_dir=workspace / "configs",
        prompts_dir=workspace / "prompts",
        planner=planner or MockPlanner(),
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=workspace / "traces",
        **kwargs,
    )


class _CountingPlanner:
    """Refusal planner that counts calls (planner should be skipped on
    the clarify path, called once on abstain)."""
    planner_id = "counting_refusal_planner"

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, planning_brief, system_prompt):
        self.calls += 1
        return {
            "refusal_id": "refusal_test",
            "task_id": planning_brief.get("task_id", "unknown"),
            "planner_id": self.planner_id,
            "refusal_type": "model_error",
            "message": "forced_by_test",
            "raw_output_hash": "",
            "recovery_allowed": True,
        }


# ===========================================================================
# Decision table (module unit tests)
# ===========================================================================

def test_confident_label_overrides():
    mod, llm = _module({"category": "office_doc_generation",
                        "confidence": 0.92, "rationale": "wants a deck"})
    out = mod.classify("帮我把这个东西弄成能给老板看的那种")
    assert out["decision"] == "override"
    assert out["category"] == "office_doc_generation"
    assert llm.calls == 1


def test_mid_confidence_clarifies_with_llm_question():
    mod, _ = _module({"category": "file_delete", "confidence": 0.55,
                      "clarify_question": "你是想删除哪个文件？"})
    out = mod.classify("把那个旧的处理掉")
    assert out["decision"] == "clarify"
    assert out["clarify_question"] == "你是想删除哪个文件？"


def test_mid_confidence_without_question_uses_template_cjk():
    mod, _ = _module({"category": "file_delete", "confidence": 0.55})
    out = mod.classify("把那个旧的处理掉")
    assert out["decision"] == "clarify"
    assert out["clarify_question"] == "请说得具体一点。"


def test_mid_confidence_without_question_uses_template_default():
    mod, _ = _module({"category": "file_delete", "confidence": 0.55})
    out = mod.classify("deal with the old one")
    assert out["decision"] == "clarify"
    assert out["clarify_question"] == "Could you be more specific?"


def test_low_confidence_abstains():
    mod, _ = _module({"category": "file_delete", "confidence": 0.2})
    out = mod.classify("hmm")
    assert out["decision"] == "abstain"
    assert out["reason"] == "below_clarify_threshold"


def test_none_category_abstains_without_question():
    mod, _ = _module({"category": "none", "confidence": 0.9})
    out = mod.classify("what is the meaning of life?")
    assert out["decision"] == "abstain"
    assert out["reason"] == "no_category_fits"


def test_none_category_with_question_clarifies():
    mod, _ = _module({"category": "none", "confidence": 0.6,
                      "clarify_question": "Do you want me to write that up as a file?"})
    out = mod.classify("something about the thing we discussed")
    assert out["decision"] == "clarify"


def test_invented_label_abstains():
    mod, _ = _module({"category": "world_domination", "confidence": 0.99})
    out = mod.classify("anything")
    assert out["decision"] == "abstain"
    assert out["reason"] == "label_outside_closed_set"


def test_hard_block_label_abstains_because_not_in_closed_set():
    # closed_categories_from_classifier never includes hard-block
    # categories, so even a "confident" LLM cannot block via L2.
    mod, _ = _module({"category": "emergency", "confidence": 0.99})
    out = mod.classify("anything")
    assert out["decision"] == "abstain"


def test_llm_unavailable_abstains():
    mod, llm = _module(None)  # chat_json returns {}
    out = mod.classify("帮我弄一下那个")
    assert out["decision"] == "abstain"
    assert out["reason"] == "llm_unavailable_or_unparseable"
    assert llm.calls == 1


def test_garbage_confidence_treated_as_zero():
    mod, _ = _module({"category": "file_delete", "confidence": "very sure"})
    out = mod.classify("把那个旧的处理掉")
    assert out["decision"] == "abstain"


def test_env_kill_switch_disables(monkeypatch):
    monkeypatch.setenv("SEMANTIC_INTAKE_ENABLED", "0")
    mod, llm = _module({"category": "file_delete", "confidence": 0.99})
    out = mod.classify("把那个旧的处理掉")
    assert out["decision"] == "abstain"
    assert out["reason"] == "disabled"
    assert llm.calls == 0


def test_config_disabled_abstains_without_llm_call():
    cfg = dict(BASE_CONFIG, enabled=False)
    mod, llm = _module({"category": "file_delete", "confidence": 0.99}, config=cfg)
    out = mod.classify("把那个旧的处理掉")
    assert out["decision"] == "abstain"
    assert llm.calls == 0


def test_closed_set_excludes_hard_block_and_unknown():
    classifier = {
        "default_planning_mode_by_category": {
            "office_doc_generation": "direct",
            "credential_or_secret": "blocked",
            "emergency": "blocked",
            "unknown": "explain_only",
        },
        "hard_block_categories": ["credential_or_secret", "emergency"],
    }
    closed = closed_categories_from_classifier(classifier)
    assert closed == ["office_doc_generation"]


def test_prompt_contains_closed_categories_and_descriptions():
    mod, llm = _module({"category": "none", "confidence": 0.0})
    mod.classify("whatever")
    assert "office_doc_generation: make office docs" in llm.last_system
    assert "file_delete" in llm.last_system


# ===========================================================================
# Runtime integration
# ===========================================================================

def _inject(rt: Runtime, payload: dict | None) -> FakeLLM:
    llm = FakeLLM(payload)
    rt.semantic_intake = SemanticIntakeModule(
        config=dict(rt.semantic_intake_cfg or BASE_CONFIG, enabled=True),
        closed_categories=closed_categories_from_classifier(rt.cfg.intake_classifier),
        chat_llm=llm,
    )
    return llm


def test_runtime_override_reroutes_unknown_to_configured_mode(isolated_workspace):
    rt = _minimal_runtime(isolated_workspace)
    llm = _inject(rt, {"category": "office_doc_generation", "confidence": 0.9,
                       "rationale": "user wants a deliverable doc"})
    # No classifier keyword matches this phrasing → L1 unknown → L2 fires.
    result = rt.run(raw_goal="帮我把上次那份东西弄成正式一点的版本")
    assert llm.calls == 1
    assert result.pre_assessment.task_category == "office_doc_generation"
    # Planning mode must come from the config map, not from the LLM.
    cfg_mode = rt.cfg.intake_classifier["default_planning_mode_by_category"]["office_doc_generation"]
    assert result.pre_assessment.planning_mode == cfg_mode
    assert any("category_override:semantic_intake" in r
               for r in result.pre_assessment.reasons)
    events = [e["event_type"] for e in rt.trace.read_all()]
    assert "semantic_classification" in events
    assert "pre_governance_reassessment" in events


def test_runtime_clarify_skips_planner_and_asks(isolated_workspace):
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner=planner)
    _inject(rt, {"category": "file_delete", "confidence": 0.5,
                 "clarify_question": "你是想删除哪个文件？"})
    result = rt.run(raw_goal="把那个旧的处理掉一下")
    assert planner.calls == 0
    assert result.plan is not None
    assert result.plan.planner_id == "semantic_clarify"
    assert result.plan.actions[0].tool == "chat"
    assert result.plan.actions[0].operation == "answer"
    assert result.plan.actions[0].metadata["body"] == "你是想删除哪个文件？"
    assert not any(e.status in ("failed", "denied") for e in result.executions)


def test_runtime_abstain_preserves_prior_behavior(isolated_workspace):
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner=planner)
    llm = _inject(rt, None)  # LLM unavailable → abstain
    result = rt.run(raw_goal="把那个旧的处理掉一下")
    assert llm.calls == 1
    assert planner.calls == 1  # planner consulted exactly as before 101C
    assert result.pre_assessment.task_category == "unknown"


def test_runtime_skips_l2_for_identity_questions(isolated_workspace):
    rt = _minimal_runtime(isolated_workspace)
    llm = _inject(rt, {"category": "office_doc_generation", "confidence": 0.99})
    result = rt.run(raw_goal="你是谁？")
    assert llm.calls == 0  # deterministic identity path, no LLM spend
    assert result.plan.planner_id == "identity_direct"


def test_runtime_skips_l2_when_keyword_classifier_matched(isolated_workspace):
    rt = _minimal_runtime(isolated_workspace)
    llm = _inject(rt, {"category": "file_delete", "confidence": 0.99})
    result = rt.run(raw_goal="写一份报告")  # L1 hit: report keywords
    assert llm.calls == 0
    assert result.pre_assessment.task_category != "unknown"


def test_runtime_override_cannot_hard_block(isolated_workspace):
    """Philosophy guard: even if a misconfigured closed set lets the
    LLM emit a hard-block category, the runtime must keep the original
    assessment rather than let L2 cause a block."""
    rt = _minimal_runtime(isolated_workspace)
    llm = FakeLLM({"category": "emergency", "confidence": 0.99})
    rt.semantic_intake = SemanticIntakeModule(
        config=dict(BASE_CONFIG, enabled=True),
        closed_categories=["emergency"],  # deliberately misconfigured
        chat_llm=llm,
    )
    result = rt.run(raw_goal="随便弄点什么吧")
    assert result.pre_assessment.hard_block is False
    assert result.pre_assessment.task_category == "unknown"


def test_runtime_default_construction_from_config(isolated_workspace):
    """With configs/semantic_intake.json enabled and no explicit module,
    Runtime builds one; default ChatLLM backend is mock → abstain →
    behavior identical to pre-101C."""
    rt = _minimal_runtime(isolated_workspace)
    assert rt.semantic_intake is not None
    out = rt.semantic_intake.classify("帮我弄一下那个东西")
    assert out["decision"] == "abstain"
