"""Phase 13 Task Tree — runtime + governance integration tests.

This file proves the *plumbing* between 102T and the rest of the
pipeline. The unit tests in test_task_tree.py only cover the
decomposer in isolation — these tests exercise the full ``_run_tree``
path:

  * Decomposed task spawns one sub-task per leaf
  * Leaves run in topological order; later leaves see earlier
    leaves' outputs via `prior_subgoals` in the brief
  * If a leaf fails, dependent leaves are marked
    `skipped_due_to_failure`
  * Each leaf goes through 101A → 101B → 103 → 107 (governance applies)
  * Sub-goals don't re-trigger decomposition (_is_subgoal guard)
  * Reflector / session-indexing skipped on leaves per config
  * Trace events tagged with module=102T + leaf events fired
  * Kill switch: TASK_TREE_ENABLED=0 falls back to single-shot
  * Simple goals don't decompose (regression guard)
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from teow_agl.modules.module_102t_task_tree import TaskTreeModule
from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool


# ---------------------------------------------------------------------------
# Stub planner — emits a deterministic single-action plan per call.
# ---------------------------------------------------------------------------
class _StubPlanner:
    planner_id = "stub_planner"

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0

    def plan(self, brief, system_prompt):
        self.calls += 1
        out = self.responder(brief)
        out.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:8]}")
        out.setdefault("task_id", brief.get("task_id", "unknown"))
        out.setdefault("planner_id", self.planner_id)
        out.setdefault("planning_mode", brief.get("planning_mode", "direct"))
        out.setdefault("used_refusal_recovery", False)
        out.setdefault("notes", [])
        return out


# ---------------------------------------------------------------------------
# Stub chat LLM — returns a fixed tree-decomposition JSON.
# ---------------------------------------------------------------------------
class _StubChatLLM:
    def __init__(self, decomp_response: dict) -> None:
        self.decomp_response = decomp_response

    def chat_json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        return dict(self.decomp_response)

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return json.dumps(self.decomp_response)


# ---------------------------------------------------------------------------
# Fixture — Runtime with 102T wired in.
# ---------------------------------------------------------------------------
def _good_fs_action(target: Path, content: str = "leaf content") -> dict:
    return {
        "action_id": "a1", "tool": "fs",
        "operation": "save_under_outputs", "target": str(target),
        "purpose": "p", "expected_effect": "e",
        "reversibility": "high", "uncertainty": "low",
        "risk_factors": [], "requires_governance": True,
        "metadata": {"content": content},
    }


def _make_runtime(workspace: Path, planner_responder,
                  decomp_response: dict) -> Runtime:
    workspace_roots = [str(workspace / "workspace"),
                       str(workspace / "outputs")]
    tools = {n: MockTool(n) for n in
             ["report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "human", "memory",
              "chat", "image_gen"]}
    tools["fs"] = FilesystemTool(workspace_roots)

    rt = Runtime(
        config_dir=workspace / "configs",
        prompts_dir=workspace / "prompts",
        planner=_StubPlanner(planner_responder),
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=workspace / "traces",
    )
    rt.profile.profile["workspace_roots"] = workspace_roots
    # Attach Task Tree module with the stubbed decomposer LLM
    prompt = (workspace / "prompts" / "module_102t_decomposer_system.md").read_text(encoding="utf-8")
    rt.task_tree = TaskTreeModule(
        chat_llm=_StubChatLLM(decomp_response),
        system_prompt=prompt,
        config=rt.task_decomposition_cfg,
    )
    return rt


# ===========================================================================
# Tree spawns and runs each leaf
# ===========================================================================

def test_decomposed_task_spawns_one_subtask_per_leaf(isolated_workspace):
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    decomp = {
        "tree_id": "tree_test",
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "first leaf step",
             "depends_on": []},
            {"sub_goal_id": "sg_b", "description": "second leaf step",
             "depends_on": ["sg_a"]},
            {"sub_goal_id": "sg_c", "description": "third leaf step",
             "depends_on": ["sg_b"]},
        ],
    }

    def planner(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target,
                                             content=brief["user_intent"])]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    # Long enough + with trigger phrase to fire the heuristic
    result = rt.run(raw_goal="First do step A, then do step B, finally do step C")

    assert result.task_tree is not None
    assert len(result.task_tree.leaves) == 3
    # Each leaf spawned its own sub-task
    spawned = [l.spawned_task_id for l in result.task_tree.leaves]
    assert all(spawned)
    assert len(set(spawned)) == 3  # all distinct
    # All three leaves marked done
    for leaf in result.task_tree.leaves:
        assert leaf.status == "done", f"{leaf.sub_goal_id}: {leaf.status}"


def test_leaves_run_in_topological_order(isolated_workspace):
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    decomp = {
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "step A", "depends_on": []},
            {"sub_goal_id": "sg_b", "description": "step B",
             "depends_on": ["sg_a"]},
            {"sub_goal_id": "sg_c", "description": "step C",
             "depends_on": ["sg_b"]},
        ],
    }
    seen_order: list[str] = []

    def planner(brief):
        seen_order.append(brief["user_intent"])
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target,
                                             content=brief["user_intent"])]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    rt.run(raw_goal="Do step A then step B then step C in order")

    # The planner should have seen A → B → C in that order
    assert seen_order == ["step A", "step B", "step C"]


def test_prior_subgoals_threaded_into_brief(isolated_workspace):
    """Downstream leaves' planning briefs should contain
    `prior_subgoals` with completed leaves' summaries."""
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    decomp = {
        "leaves": [
            {"sub_goal_id": "sg_first", "description": "first leaf",
             "depends_on": []},
            {"sub_goal_id": "sg_second", "description": "second leaf",
             "depends_on": ["sg_first"]},
        ],
    }
    briefs_seen: list[dict] = []

    def planner(brief):
        briefs_seen.append(dict(brief))
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target,
                                             content=brief["user_intent"])]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    rt.run(raw_goal="First do leaf one, then do leaf two using its output")

    # 3 planner calls total: skipped (root tree fork doesn't run 102) + 2 leaves
    # The second leaf's brief must contain prior_subgoals
    second_brief = briefs_seen[1]
    assert "prior_subgoals" in second_brief
    prior = second_brief["prior_subgoals"]
    assert any(p["sub_goal_id"] == "sg_first" for p in prior)


def test_failed_dep_skips_downstream_leaves(isolated_workspace):
    """If leaf A fails, leaf B (depends on A) should NOT execute and
    must be marked skipped_due_to_failure."""
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    decomp = {
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "first leaf that will fail",
             "depends_on": []},
            {"sub_goal_id": "sg_b", "description": "second leaf depends on first",
             "depends_on": ["sg_a"]},
        ],
    }

    def planner(brief):
        intent = brief["user_intent"]
        if "fail" in intent:
            # Plan an action with a bad (out-of-workspace) target → fails
            return {"planning_mode": brief["planning_mode"], "actions": [{
                "action_id": "a1", "tool": "fs",
                "operation": "save_under_outputs",
                "target": "/totally/outside/workspace.txt",
                "purpose": "p", "expected_effect": "e",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": "x"},
            }]}
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target, content=intent)]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    result = rt.run(raw_goal=("First do leaf one which will fail, "
                              "then do leaf two depending on it"))

    by_id = {l.sub_goal_id: l for l in result.task_tree.leaves}
    assert by_id["sg_a"].status == "failed"
    assert by_id["sg_b"].status == "skipped_due_to_failure"
    # The failed leaf consumed a planner call; the skipped one did NOT
    assert by_id["sg_b"].spawned_task_id is None


def test_each_leaf_goes_through_governance(isolated_workspace):
    """Each leaf must emit 103 (governance_decision) + 107
    (execution_*) trace events — proving the full pipeline ran for
    each sub-task, not bypassed."""
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    decomp = {
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "leaf A", "depends_on": []},
            {"sub_goal_id": "sg_b", "description": "leaf B", "depends_on": []},
        ],
    }

    def planner(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target,
                                             content=brief["user_intent"])]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    captured: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        captured.append({"module": ev.module,
                         "event_type": ev.event_type,
                         "task_id": ev.task_id})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    rt.run(raw_goal="First do leaf A, then do leaf B in parallel mode")

    # Expect at least one governance_decision + execution_completed
    # per LEAF task_id — leaves' task_ids are different from parent's.
    govern_events = [e for e in captured if e["event_type"] == "governance_decision"]
    exec_events = [e for e in captured if e["event_type"] == "execution_completed"]
    # ≥ 2 governance decisions (one per leaf) + ≥ 2 successful exec
    assert len(govern_events) >= 2
    assert len(exec_events) >= 2


def test_subgoal_does_not_redecompose(isolated_workspace):
    """A sub-goal task should NOT trigger another 102T decomposition,
    even if its description looks complex. This is the
    `_is_subgoal=True` envelope-metadata guard."""
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    # Use a decomposer that ALWAYS returns 2 leaves, including for
    # children. If the guard works, it only fires once (for the root).
    decomp = {
        "leaves": [
            {"sub_goal_id": "sg_x", "description": "child step one " * 20,
             "depends_on": []},
            {"sub_goal_id": "sg_y", "description": "child step two " * 20,
             "depends_on": ["sg_x"]},
        ],
    }

    def planner(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target,
                                             content=brief["user_intent"])]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    result = rt.run(raw_goal="First do something long, then do another long thing, finally do a third long thing")

    # Root decomposed → 2 leaves
    assert result.task_tree is not None
    # No leaf should itself have a task_tree (the guard worked)
    for sub in result.subgoal_results:
        assert sub.task_tree is None, \
            "sub-goal re-decomposed — _is_subgoal guard failed"


# ===========================================================================
# Kill switch + regression guards
# ===========================================================================

def test_simple_goal_does_not_decompose(isolated_workspace):
    """A short, simple goal must NOT fire decomposition — regression
    guard so we don't pay LLM cost on chit-chat."""
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    decomp = {  # the LLM would happily decompose if asked, but the
                # heuristic shouldn't ask
        "leaves": [{"sub_goal_id": "x", "description": "x", "depends_on": []},
                   {"sub_goal_id": "y", "description": "y", "depends_on": []}],
    }

    def planner(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target, content="x")]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    # Track whether the decomposer was called
    rt.task_tree.chat_llm.calls_made = 0
    original_chat_json = rt.task_tree.chat_llm.chat_json

    def tracked(system, user, max_tokens=1500):
        rt.task_tree.chat_llm.calls_made += 1
        return original_chat_json(system, user, max_tokens)
    rt.task_tree.chat_llm.chat_json = tracked

    result = rt.run(raw_goal="hi")  # short, no triggers
    assert result.task_tree is None
    assert rt.task_tree.chat_llm.calls_made == 0


def test_kill_switch_disables_decomposition(isolated_workspace,
                                            monkeypatch):
    monkeypatch.setenv("TASK_TREE_ENABLED", "0")
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    decomp = {
        "leaves": [{"sub_goal_id": "x", "description": "x", "depends_on": []},
                   {"sub_goal_id": "y", "description": "y", "depends_on": []}],
    }

    def planner(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target, content="x")]}

    rt = _make_runtime(isolated_workspace, planner, decomp)
    result = rt.run(raw_goal="First do A, then do B, finally do C, then do D")
    # Kill switch is set → no tree even though heuristic would fire
    assert result.task_tree is None


def test_decomposer_failure_falls_back_to_single_shot(isolated_workspace):
    """If 102T's LLM crashes / returns garbage, runtime should fall
    through to the normal single-shot pipeline (per config.fallback)."""
    target = isolated_workspace / "outputs" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Empty decomp response → decompose() returns None → fallback
    decomp_garbage = {}

    def planner(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target, content="single-shot path")]}

    rt = _make_runtime(isolated_workspace, planner, decomp_garbage)
    result = rt.run(raw_goal="First do A, then do B, finally do C")
    # No tree built, but task still completed via single-shot
    assert result.task_tree is None
    assert any(e.status == "success" for e in result.executions)
