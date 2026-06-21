"""EXECUTE_DIRECT end-to-end: after N successful runs of same shape, the
runtime skips 102 and serves from PlanCache.

This is the headline 'agent gets faster' demo — counts the number of times
the planner is called via a counter, and asserts the cache hit zeroes it.
"""
from __future__ import annotations

from pathlib import Path

from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool


class CountingPlanner:
    """Wraps a MockPlanner-style responder and counts how many times plan() is called."""
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


def _make_rt(workspace: Path, planner) -> Runtime:
    workspace_roots = [str(workspace / "workspace"), str(workspace / "outputs")]
    tools = {n: MockTool(n) for n in ["fs","report","docx","pptx","xlsx",
                                       "desktop","gui","email","publish",
                                       "code","shell","human","memory"]}
    tools["fs"] = FilesystemTool(workspace_roots)
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
    rt.profile.profile["workspace_roots"] = workspace_roots
    # Confidence threshold low so 3 successes is enough for our test
    rt.subject_confidence.min_observations_for_confident = 3
    rt.subject_confidence.confident_threshold = 0.6
    rt.plan_cache.min_successes_for_cache = 3
    return rt


def test_execute_direct_after_three_successes(isolated_workspace: Path):
    """Run the same shape 3x successfully → 4th run should skip 102."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [{
                "action_id": "a1", "tool": "fs", "operation": "save_under_outputs",
                "target": str(target),
                "purpose": "write a note",
                "expected_effect": "file written",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": "test note"},
            }],
        }

    planner = CountingPlanner(responder)
    rt = _make_rt(isolated_workspace, planner)

    # Run 3 times — planner called each time
    for i in range(3):
        result = rt.run(raw_goal=f"Save a note {i}")
        assert any(e.status == "success" for e in result.executions), f"run {i} failed"

    assert planner.calls == 3, f"expected 3 planner calls, got {planner.calls}"
    # subject_confidence should now be confident
    assert rt.subject_confidence.is_confident("file_write")

    # 4th run — should hit cache, NOT call planner
    result4 = rt.run(raw_goal="Save a note 4")
    assert planner.calls == 3, f"4th run should not call planner; calls={planner.calls}"
    assert result4.plan is not None
    assert result4.plan.planner_id == "execute_direct_cache"
    # trace should contain a CACHE event
    cache_events = [e for e in rt.trace.read_all() if e["module"] == "CACHE"]
    assert any(e["event_type"] == "cache_hit" for e in cache_events)


def test_failure_invalidates_cache_and_next_run_uses_planner(isolated_workspace: Path):
    """After cache is active, a failed cached run invalidates it; next run calls planner."""
    target = isolated_workspace / "outputs" / "note2.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    success_responder = lambda b: {
        "planning_mode": b["planning_mode"],
        "actions": [{
            "action_id": "a1", "tool": "fs", "operation": "save_under_outputs",
            "target": str(target), "purpose": "write",
            "expected_effect": "file", "reversibility": "high",
            "uncertainty": "low", "risk_factors": [],
            "requires_governance": True, "metadata": {"content": "ok"},
        }],
    }
    planner = CountingPlanner(success_responder)
    rt = _make_rt(isolated_workspace, planner)

    # Warm cache
    for i in range(3):
        rt.run(raw_goal=f"Save {i}")
    assert planner.calls == 3

    # 4th run — cache hit
    rt.run(raw_goal="Save 4")
    assert planner.calls == 3

    # Invalidate the cache directly (simulating any failure path), then
    # verify the next run goes through the planner again.
    cache_entry = rt.plan_cache.lookup(category="file_write")
    assert cache_entry is not None, "cache should be active after 3 successes"
    cache_entry["status"] = "invalidated"
    rt.plan_cache._append(cache_entry)  # type: ignore[attr-defined]
    rt.plan_cache._entries = None  # force reload

    rt.run(raw_goal="Save 5")
    assert planner.calls == 4, f"after invalidation, planner should be called; calls={planner.calls}"
