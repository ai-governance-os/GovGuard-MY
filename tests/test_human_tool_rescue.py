"""Defensive runtime rewrite: if planner picks `human.request_clarification`
(or any other banned tool), runtime auto-rewrites to `chat.answer` so the
user never sees a 'no_tool_handler / 1 failed/denied' dead end.

Background:
  Both Groq llama-3.3 and Gemini Flash have a strong training-data
  bias to pick `human.request_clarification` whenever a user question
  is even slightly ambiguous (greeting, identity, vague request).
  We removed `human` from the catalog AND the executor's tool
  registry — but the LLM still emits it because the catalog is just
  a prompt hint, not an enforcement layer.
  Without this rescue, the executor's `no_tool_handler` path fires
  and the user sees a useless 'failed' chip. With the rescue, runtime
  swaps tool='human' → tool='chat' + operation='answer' BEFORE the
  executor runs, and the synthesizer writes the body from user_intent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.models import CandidateAction, CandidatePlan
from teow_agl.runtime import Runtime


# ===========================================================================
# Unit tests on the _normalize_actions rewrite
# ===========================================================================

def _plan(actions: list[CandidateAction]) -> CandidatePlan:
    return CandidatePlan(
        plan_id="plan_t", task_id="task_t",
        planner_id="stub", planning_mode="explain_only",
        used_refusal_recovery=False, actions=actions, notes=[],
    )


def _action(tool: str, op: str, **meta) -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool=tool, operation=op, target="",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata=dict(meta),
    )


def _minimal_runtime(isolated_workspace, planner=None, **kwargs) -> Runtime:
    """Build a no-tool-actually-needed Runtime just to access
    _normalize_actions in isolation."""
    from teow_agl.modules.module_105_human_gate import HumanGate
    from teow_agl.tools.mock_tools import MockTool
    from teow_agl.adapters.mock_planner import MockPlanner

    tools = {n: MockTool(n) for n in
             ["fs", "report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "memory", "chat",
              "image_gen"]}
    rt = Runtime(
        config_dir=isolated_workspace / "configs",
        prompts_dir=isolated_workspace / "prompts",
        planner=planner or MockPlanner(),
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=isolated_workspace / "traces",
        **kwargs,
    )
    return rt


class _CountingPlanner:
    planner_id = "counting_refusal_planner"

    def __init__(self, refusal_type: str = "model_error") -> None:
        self.refusal_type = refusal_type
        self.calls = 0

    def plan(self, planning_brief, system_prompt):
        self.calls += 1
        return {
            "refusal_id": "refusal_test",
            "task_id": planning_brief.get("task_id", "unknown"),
            "planner_id": self.planner_id,
            "refusal_type": self.refusal_type,
            "message": "forced_by_test",
            "raw_output_hash": "",
            "recovery_allowed": True,
        }


def test_human_request_clarification_rewritten_to_chat_answer(isolated_workspace):
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([
        _action("human", "request_clarification",
                body="Could you tell me more about what you want?")
    ])
    rt._normalize_actions(plan)
    a = plan.actions[0]
    # Tool + operation must be rewritten
    assert a.tool == "chat"
    assert a.operation == "answer"
    # Audit metadata kept so trace shows what happened
    assert a.metadata.get("planner_originally_picked") == "human.request_clarification"
    assert "__llm_drift_rescue" in a.metadata
    # Body cleared so synthesizer rewrites from user_intent (the
    # original body was a half-formed "I need clarification" stub).
    assert a.metadata.get("body") == ""
    assert a.metadata.get("__planner_body_dropped") == "Could you tell me more about what you want?"


def test_deprecated_human_key_also_rescued(isolated_workspace):
    """If somehow the planner picks the literal `_human_DEPRECATED`
    catalog key, same rewrite fires."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("_human_DEPRECATED", "request_clarification")])
    rt._normalize_actions(plan)
    assert plan.actions[0].tool == "chat"
    assert plan.actions[0].operation == "answer"


def test_human_with_other_operations_also_rewritten(isolated_workspace):
    """The rewrite is unconditional on operation. Even
    `human.request_approval` (a never-implemented op) gets routed to
    chat so the user doesn't see a dead end."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("human", "request_approval")])
    rt._normalize_actions(plan)
    assert plan.actions[0].tool == "chat"
    assert plan.actions[0].operation == "answer"


def test_human_case_insensitive(isolated_workspace):
    """LLMs sometimes emit 'Human' or 'HUMAN'."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("Human", "Request_Clarification"),
                   _action("HUMAN", "request_clarification")])
    rt._normalize_actions(plan)
    for a in plan.actions:
        assert a.tool == "chat"
        assert a.operation == "answer"


def test_normal_tools_not_affected(isolated_workspace):
    """Sanity: any non-banned tool should be untouched."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([
        _action("chat", "answer", body="hi"),
        _action("docx", "save_under_outputs", body="x" * 100),
        _action("fs", "save_under_outputs", body="x" * 100),
        _action("image_gen", "generate_image", prompt="koi"),
    ])
    rt._normalize_actions(plan)
    assert plan.actions[0].tool == "chat"
    assert plan.actions[0].operation == "answer"
    assert plan.actions[0].metadata.get("body") == "hi"  # untouched
    assert plan.actions[1].tool == "docx"
    assert plan.actions[2].tool == "fs"
    assert plan.actions[3].tool == "image_gen"


def test_target_cleared_on_rewrite(isolated_workspace):
    """When `human` is rewritten to `chat`, any target the planner
    set is cleared (chat doesn't write a file)."""
    rt = _minimal_runtime(isolated_workspace)
    a = _action("human", "request_clarification")
    a.target = "outputs/should_not_exist.txt"
    plan = _plan([a])
    rt._normalize_actions(plan)
    assert plan.actions[0].target == ""


def test_audit_trail_metadata_preserved(isolated_workspace):
    """The rewrite leaves an audit breadcrumb so post-hoc trace
    inspection can tell that the planner originally picked `human`
    but was rescued."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("human", "request_clarification",
                          body="please clarify")])
    rt._normalize_actions(plan)
    a = plan.actions[0]
    assert a.metadata["planner_originally_picked"] == "human.request_clarification"
    assert "rescue" in a.metadata["__llm_drift_rescue"].lower()
    assert a.metadata["__planner_body_dropped"] == "please clarify"


# ===========================================================================
# Generalised hallucinated-tool rescue (not just `human`)
# ===========================================================================

def test_hallucinated_tool_rescued_to_chat(isolated_workspace):
    """If planner emits a tool name not in the catalog, rewrite to
    chat.answer rather than letting the executor fail with
    no_tool_handler."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("clarify_request", "ask", body="What did you mean?")])
    rt._normalize_actions(plan)
    a = plan.actions[0]
    assert a.tool == "chat"
    assert a.operation == "answer"
    assert "__llm_drift_rescue_hallucinated_tool" in a.metadata


def test_made_up_search_tool_rescued(isolated_workspace):
    """LLM-style names like 'browse' or 'lookup' that aren't real
    tools also get rescued."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("browse", "open", body="check the news")])
    rt._normalize_actions(plan)
    assert plan.actions[0].tool == "chat"
    assert plan.actions[0].operation == "answer"


def test_chat_wrong_operation_normalized(isolated_workspace):
    """If planner emits chat.request_clarification (wrong op), force
    op to 'answer' so chat tool accepts it."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([_action("chat", "request_clarification",
                          body="What did you want?")])
    rt._normalize_actions(plan)
    a = plan.actions[0]
    assert a.tool == "chat"
    assert a.operation == "answer"
    assert "__llm_drift_rescue_chat_op" in a.metadata


def test_chat_valid_ops_preserved(isolated_workspace):
    """The valid chat ops (answer / reply / respond / explain) should
    NOT be rewritten."""
    rt = _minimal_runtime(isolated_workspace)
    plan = _plan([
        _action("chat", "answer", body="hi"),
        _action("chat", "reply", body="hi"),
        _action("chat", "respond", body="hi"),
        _action("chat", "explain", body="hi"),
    ])
    rt._normalize_actions(plan)
    for a in plan.actions:
        assert a.tool == "chat"
        # operation should be untouched
        assert "__llm_drift_rescue_chat_op" not in a.metadata


def test_identity_question_skips_planner_and_answers_chat(isolated_workspace):
    """Identity/chitchat should not depend on the remote planner. This is
    the user-visible fix for `你是谁？` turning into `1 failed/denied` when
    Groq returns 413/429."""
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner=planner)

    result = rt.run(raw_goal="你是谁？")

    assert planner.calls == 0
    assert result.plan is not None
    assert result.plan.planner_id == "identity_direct"
    assert result.plan.actions[0].tool == "chat"
    assert result.plan.actions[0].operation == "answer"
    assert result.plan.actions[0].metadata["body"]
    assert not any(e.status in ("failed", "denied") for e in result.executions)


def test_unknown_refusal_recovery_uses_chat_not_human(isolated_workspace):
    """If a non-identity unknown task hits planner refusal, 102R must not
    reintroduce the deprecated human tool that the server no longer
    registers."""
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner=planner)

    result = rt.run(raw_goal="Please handle this ambiguous thing")

    assert planner.calls == 1
    assert result.plan is not None
    assert result.plan.used_refusal_recovery is True
    assert [a.tool for a in result.plan.actions] == ["chat"]
    assert result.plan.actions[0].operation == "answer"
    assert not any(e.error == "no_tool_handler:human" for e in result.executions)


def test_capability_question_skips_planner_and_web(isolated_workspace):
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner=planner)

    result = rt.run(raw_goal="你可以帮助我做什么？")

    assert planner.calls == 0
    assert result.plan is not None
    assert result.plan.planner_id == "identity_direct"
    assert result.plan.actions[0].tool == "chat"
    assert not any(e["module"] == "WEB" for e in rt.trace.read_all())


def test_simple_chinese_ppt_uses_direct_office_plan(isolated_workspace):
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner=planner)

    result = rt.run(raw_goal="做一张幻灯")

    assert planner.calls == 0
    assert result.pre_assessment.task_category == "office_doc_generation"
    assert result.plan is not None
    assert result.plan.planner_id == "office_direct"
    assert any(a.tool == "pptx" and a.operation == "save_under_outputs"
               for a in result.plan.actions)
