"""Module 102W / workflow autonomy — detection, plan construction, and the
runtime integration that lets a configured workflow own its task.

All offline (smart_mock); the LLM is never the safety authority. These tests
cover the brief's §12 list for the 102W half: CN/EN detection, the plan shape
(internal report stays internal, public FB draft blocks sensitive fields),
the negative trigger (a generic action cue alone must NOT fire), and the
task-tree non-interception guarantee (§E). The 101D Data-Use-Guard routing
tests are added alongside 101D.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.adapters.smart_mock_planner import SmartMockPlanner
from teow_agl.models import TaskEnvelope
from teow_agl.modules.module_102w_workflow_resolver import WorkflowResolver
from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime
from teow_agl.tools.chat_tool import ChatTool
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool
from teow_agl.tools.report_tools import ReportTool

WORKFLOW_GOAL_CN = "成绩出来了，处理一下。"
WORKFLOW_GOAL_EN = "Sports day results are ready. Prepare everything."
GENERIC_CUE_GOAL = "帮我处理一下这份文件"  # action cue only, no results anchor


# ── helpers ────────────────────────────────────────────────────────────────

def _runtime(workspace: Path, *, gate: str = "reject_all", task_tree=None) -> Runtime:
    roots = [str(workspace / "workspace"), str(workspace / "outputs")]
    tools = {
        "fs": FilesystemTool(roots), "chat": ChatTool(), "report": ReportTool(),
        **{n: MockTool(n) for n in ("docx", "pptx", "xlsx", "desktop", "gui",
                                    "email", "publish", "code", "shell", "human")},
    }
    rt = Runtime(
        config_dir=workspace / "configs", prompts_dir=workspace / "prompts",
        planner=SmartMockPlanner(default_outputs_dir=str(workspace / "outputs")),
        tool_registry=tools, human_gate=HumanGate(gate),
        trace_dir=workspace / "traces", domain_pack="public_school",
        task_tree=task_tree,
    )
    rt.profile.profile["workspace_roots"] = roots
    return rt


def _capture(rt: Runtime) -> list[dict]:
    captured: list[dict] = []
    original = rt.trace.emit

    def cap(*a, **kw):
        ev = original(*a, **kw)
        captured.append({"module": ev.module, "event_type": ev.event_type,
                         "summary": ev.summary})
        return ev

    rt.trace.emit = cap  # type: ignore[method-assign]
    return captured


def _resolver(workspace: Path) -> WorkflowResolver:
    return WorkflowResolver(config_dir=workspace / "configs", domain="public_school")


def _envelope(goal: str, **md) -> TaskEnvelope:
    return TaskEnvelope(session_id="s", user_id="u", raw_goal=goal,
                        normalized_goal=goal, metadata=dict(md))


# ── resolver-direct unit tests (Phase 2) ────────────────────────────────────

def test_cn_minimal_goal_detected(isolated_workspace: Path):
    res = _resolver(isolated_workspace).resolve(_envelope(WORKFLOW_GOAL_CN), None)
    assert res is not None
    assert res["workflow_id"] == "post_event_reporting"
    assert res["confidence"] >= 0.7


def test_en_goal_detected(isolated_workspace: Path):
    res = _resolver(isolated_workspace).resolve(_envelope(WORKFLOW_GOAL_EN), None)
    assert res is not None and res["workflow_id"] == "post_event_reporting"


def test_subgoal_never_resolves(isolated_workspace: Path):
    """A decomposition leaf must never resolve a workflow (§6 rule 5)."""
    res = _resolver(isolated_workspace).resolve(
        _envelope(WORKFLOW_GOAL_CN, _is_subgoal=True), None)
    assert res is None


def test_negative_trigger_generic_cue(isolated_workspace: Path):
    """A generic action cue with no results/event anchor must NOT fire."""
    res = _resolver(isolated_workspace).resolve(_envelope(GENERIC_CUE_GOAL), None)
    assert res is None


def test_build_plan_uses_catalog_tools_and_metadata(isolated_workspace: Path):
    r = _resolver(isolated_workspace)
    res = r.resolve(_envelope(WORKFLOW_GOAL_CN), None)
    catalog = {"tools": {t: {} for t in ("fs", "report", "chat", "docx")}}
    plan = r.build_plan(res, _envelope(WORKFLOW_GOAL_CN), None, catalog)
    assert plan.planner_id == "102W_workflow_resolver"
    assert len(plan.actions) == 5
    valid = set(catalog["tools"])
    for a in plan.actions:
        assert a.tool in valid, f"{a.tool} not in closed catalog"
        assert a.metadata["workflow_id"] == "post_event_reporting"
        assert a.metadata.get("workflow_step_id")

    # test 3: internal report step stays internal
    rep = next(a for a in plan.actions
               if a.metadata["workflow_step_id"] == "draft_internal_report")
    assert rep.metadata["output_scope"] == "internal"
    assert (rep.tool, rep.operation) == ("report", "draft_report")

    # test 4: public FB draft blocks IC/phone/guardian income, draft-only
    fb = next(a for a in plan.actions
              if a.metadata["workflow_step_id"] == "draft_public_fb_post")
    assert fb.metadata["output_scope"] == "public_draft"
    assert fb.metadata["approval_boundary"] == "draft_only"
    for field in ("ic_number", "phone_number", "guardian_income"):
        assert field in fb.metadata["blocked_data"]


# ── runtime integration tests (Phase 3) ─────────────────────────────────────

def test_runtime_detects_workflow_and_skips_planner(isolated_workspace: Path):
    rt = _runtime(isolated_workspace)
    cap = _capture(rt)
    result = rt.run(raw_goal=WORKFLOW_GOAL_CN)
    mods = [(e["module"], e["event_type"]) for e in cap]
    assert ("102W", "workflow_detected") in mods
    # 102W built the plan — the remote planner was skipped, never called.
    assert ("102", "planner_skipped") in mods
    assert ("102", "planner_called") not in mods
    # every decided action belongs to the workflow (metadata present, test 1)
    assert result.plan is not None
    assert len(result.plan.actions) == 5
    assert all(a.metadata.get("workflow_id") == "post_event_reporting"
               for a in result.plan.actions)


def test_normal_goal_does_not_trigger_workflow(isolated_workspace: Path):
    """Negative trigger through the full runtime — falls through to the
    normal pipeline with no 102W detection (test 9)."""
    rt = _runtime(isolated_workspace)
    cap = _capture(rt)
    rt.run(raw_goal=GENERIC_CUE_GOAL)
    mods = [(e["module"], e["event_type"]) for e in cap]
    assert ("102W", "workflow_detected") not in mods


class _AlwaysTree:
    """Stub task tree that WANTS to decompose everything — it must never get
    the chance to steal a workflow task (the §E `workflow_plan is None`
    guard short-circuits the fork before `decompose` is reached)."""

    def needs_decomposition(self, goal: str) -> bool:
        return True

    def decompose(self, envelope):  # pragma: no cover - must never run
        raise AssertionError("task tree stole a workflow task — §E guard failed")


def test_task_tree_cannot_steal_workflow(isolated_workspace: Path, monkeypatch):
    monkeypatch.setenv("TASK_TREE_ALWAYS", "1")
    rt = _runtime(isolated_workspace, task_tree=_AlwaysTree())
    cap = _capture(rt)
    result = rt.run(raw_goal=WORKFLOW_GOAL_CN)  # must not raise
    mods = [(e["module"], e["event_type"]) for e in cap]
    assert ("102W", "workflow_detected") in mods
    assert result.plan is not None
    assert result.plan.planner_id == "102W_workflow_resolver"
