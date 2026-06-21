"""Capability card (configs/capability_card.json) — the single source
of the product's self-knowledge. These tests prove the consolidation:

  1. Direct answers (identity / greeting / boundary) come from the
     card — edit the card, the answer changes, no code touch.
  2. Adversarial purity: emptying the card's patterns + answers makes
     the direct path collapse (falls through to the planner). If any
     literal table survived in runtime.py, these would keep firing.
  3. Module-level helpers still work standalone (back-compat for the
     older demo-patch tests) by falling back to the repo card.
"""
from __future__ import annotations

import json
from pathlib import Path

from teow_agl.runtime import (
    Runtime,
    _desktop_boundary_answer,
    _identity_answer,
    _is_desktop_boundary_question,
)


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


def _edit_card(workspace: Path, mutate) -> None:
    path = workspace / "configs" / "capability_card.json"
    card = json.loads(path.read_text(encoding="utf-8"))
    mutate(card)
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")


# ===========================================================================
# Answers come from the card
# ===========================================================================

def test_boundary_answer_text_comes_from_card(isolated_workspace):
    marker = "CARD_MARKER_b7 治理 GREEN BLUE"

    def mutate(card):
        card["answers"]["capability_boundary"]["cjk"] = marker

    _edit_card(isolated_workspace, mutate)
    rt = _minimal_runtime(isolated_workspace)
    result = rt.run(raw_goal="动我的电脑可以吗")
    assert result.plan.planner_id == "desktop_boundary_direct"
    assert result.plan.actions[0].metadata["body"] == marker


def test_identity_answer_text_comes_from_card(isolated_workspace):
    marker = "CARD_MARKER_i3 我是 TEOW-AGL 测试版"

    def mutate(card):
        card["answers"]["identity"]["cjk"] = marker

    _edit_card(isolated_workspace, mutate)
    rt = _minimal_runtime(isolated_workspace)
    result = rt.run(raw_goal="你是谁？")
    assert result.plan.planner_id == "identity_direct"
    assert result.plan.actions[0].metadata["body"] == marker


def test_greeting_answer_text_comes_from_card(isolated_workspace):
    marker = "CARD_MARKER_g1 哈喽！"

    def mutate(card):
        card["answers"]["greeting"]["cjk"] = marker

    _edit_card(isolated_workspace, mutate)
    rt = _minimal_runtime(isolated_workspace)
    result = rt.run(raw_goal="你好")
    assert result.plan.planner_id == "greeting_direct"
    assert result.plan.actions[0].metadata["body"] == marker


def test_direct_answers_skip_synthesizer(isolated_workspace):
    """Card answers must reach the user verbatim — never rewritten by
    102B (the clarify-question lesson, applied to all card answers)."""
    rt = _minimal_runtime(isolated_workspace)
    result = rt.run(raw_goal="你好")
    assert result.plan.actions[0].metadata.get("synthesis_skip") is True


# ===========================================================================
# Adversarial purity — emptying the card collapses the direct path
# ===========================================================================

def test_emptied_card_collapses_boundary_direct_path(isolated_workspace):
    def mutate(card):
        card["boundary_patterns"] = []
        card["answers"]["capability_boundary"] = {}

    _edit_card(isolated_workspace, mutate)
    rt = _minimal_runtime(isolated_workspace)
    result = rt.run(raw_goal="control my computer please")
    # No prepared answer left → must fall through to the normal
    # planner path instead of serving a hardcoded string.
    assert result.plan is None or result.plan.planner_id != "desktop_boundary_direct"


def test_emptied_card_collapses_greeting_direct_path(isolated_workspace):
    def mutate(card):
        card["greeting_phrases"] = []
        card["answers"]["greeting"] = {}

    _edit_card(isolated_workspace, mutate)
    rt = _minimal_runtime(isolated_workspace)
    result = rt.run(raw_goal="你好")
    assert result.plan is None or result.plan.planner_id != "greeting_direct"


# ===========================================================================
# Back-compat: module-level helpers fall back to the repo card
# ===========================================================================

def test_module_helpers_use_repo_card_by_default():
    assert _is_desktop_boundary_question("control my computer please") is True
    assert _is_desktop_boundary_question("what is photosynthesis") is False
    body_cn = _desktop_boundary_answer("动我的电脑可以吗")
    assert "GREEN" in body_cn or "治理" in body_cn
    body_en = _desktop_boundary_answer("control my computer please")
    assert "governance" in body_en.lower() or "GREEN" in body_en
    assert "TEOW" in _identity_answer("你是谁?")
