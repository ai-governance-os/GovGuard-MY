"""Regression locks for deterministic Main Demo probe learning isolation."""
from __future__ import annotations

from pathlib import Path

from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool


class _CountingPlanner:
    planner_id = "counting_planner"

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0

    def plan(self, brief, system_prompt):
        self.calls += 1
        import uuid
        out = self.responder(brief)
        out.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:8]}")
        out.setdefault("task_id", brief.get("task_id", "unknown"))
        out.setdefault("planner_id", self.planner_id)
        out.setdefault("planning_mode", brief.get("planning_mode", "direct"))
        out.setdefault("used_refusal_recovery", False)
        out.setdefault("notes", [])
        return out


def _make_runtime(workspace: Path, planner) -> Runtime:
    roots = [str(workspace / "workspace"), str(workspace / "outputs")]
    tools = {
        name: MockTool(name)
        for name in [
            "fs", "report", "docx", "pptx", "xlsx", "desktop", "gui",
            "email", "publish", "code", "shell", "human", "memory",
        ]
    }
    tools["fs"] = FilesystemTool(roots)
    rt = Runtime(
        config_dir=workspace / "configs",
        prompts_dir=workspace / "prompts",
        planner=planner,
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=workspace / "traces",
        subject_confidence_path=workspace / "state" / "sc.jsonl",
        plan_cache_path=workspace / "state" / "pc.jsonl",
        plan_cache_outputs_dir=str(workspace / "outputs"),
    )
    rt.profile.profile["workspace_roots"] = roots
    rt.subject_confidence.min_observations_for_confident = 3
    rt.subject_confidence.confident_threshold = 0.6
    rt.plan_cache.min_successes_for_cache = 3
    (workspace / "outputs").mkdir(parents=True, exist_ok=True)
    return rt


def _responder(target: Path):
    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [{
                "action_id": "a1", "tool": "fs",
                "operation": "save_under_outputs", "target": str(target),
                "purpose": "write a note", "expected_effect": "file written",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": "test note"},
            }],
        }
    return responder


def test_deterministic_probe_never_reads_a_stale_plan_cache(
    isolated_workspace: Path,
):
    target = isolated_workspace / "outputs" / "note.txt"
    planner = _CountingPlanner(_responder(target))
    rt = _make_runtime(isolated_workspace, planner)
    for i in range(3):
        rt.run(raw_goal=f"Save a note {i}")
    assert rt.plan_cache.lookup(category="file_write") is not None
    ordinary = rt.run(raw_goal="Save a note 4")
    assert ordinary.plan.planner_id == "execute_direct_cache"
    assert planner.calls == 3
    probe = rt.run(
        raw_goal="Save a note 5",
        metadata={"deterministic_demo_probe": True},
    )
    assert planner.calls == 4
    assert probe.plan.planner_id != "execute_direct_cache"


def test_deterministic_probe_never_writes_learning_stores(
    isolated_workspace: Path,
):
    target = isolated_workspace / "outputs" / "probe_note.txt"
    planner = _CountingPlanner(_responder(target))
    rt = _make_runtime(isolated_workspace, planner)
    for i in range(5):
        rt.run(
            raw_goal=f"Save a probe note {i}",
            metadata={"deterministic_demo_probe": True},
        )
    assert not rt.subject_confidence.is_confident("file_write")
    assert rt.plan_cache.lookup(category="file_write") is None
    assert planner.calls == 5
