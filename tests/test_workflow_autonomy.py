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
from teow_agl.models import CandidateAction, CandidatePlan, TaskEnvelope
from teow_agl.modules.module_101d_data_use_guard import DataUseGuard
from teow_agl.modules.module_102w_workflow_resolver import WorkflowResolver
from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime, TaskRunResult
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
    assert len(plan.actions) == 6
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
    assert len(result.plan.actions) == 6
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


# ── 101D Data Use Guard tests (Phase 5) ─────────────────────────────────────

def _decision_for(result, step_id: str):
    action = next(a for a in result.plan.actions
                  if a.metadata.get("workflow_step_id") == step_id)
    return next(d for d in result.decisions if d.action_id == action.action_id)


def test_external_release_step_is_green(isolated_workspace: Path):
    """The external public-release step elevates to GREEN (needs human
    approval) via 101D, and with reject_all it never executes (test 5).
    Internal BLUE save steps legitimately write drafts under outputs/ — only
    the GREEN external step must produce no side effect."""
    rt = _runtime(isolated_workspace, gate="reject_all")
    result = rt.run(raw_goal=WORKFLOW_GOAL_CN)
    ext = next(a for a in result.plan.actions
               if a.metadata.get("workflow_step_id") == "prepare_public_release")
    dec = _decision_for(result, "prepare_public_release")
    assert dec.route == "GREEN"
    assert dec.approval_required
    # internal steps stay BLUE (101D inert / NO_OVERRIDE for internal scope)
    assert _decision_for(result, "draft_internal_report").route == "BLUE"
    # the rejected GREEN external step itself executes no side effect
    assert not [e for e in result.executions
                if e.action_id == ext.action_id and getattr(e, "affected_resources", None)]


def _one_action_result(rt: Runtime, goal: str, action: CandidateAction):
    env = TaskEnvelope(session_id="s", user_id="u", raw_goal=goal,
                       normalized_goal=goal)
    pre = rt.pre_gov.assess(env, rt.profile)
    result = TaskRunResult(envelope=env, pre_assessment=pre)
    plan = CandidatePlan(task_id=env.task_id, planner_id="test",
                         planning_mode="direct", actions=[action])
    rt._execute_actions(plan, env, pre, None, result)
    return result


def test_guardian_income_differential_is_red_exactly_once(isolated_workspace: Path):
    """The flagship self-governance RED: the agent's OWN plan proposes using
    guardian income to differentiate parent communication → 101D RED, with
    EXACTLY ONE RED decision recorded (§H), blocked before execution."""
    rt = _runtime(isolated_workspace)
    action = CandidateAction(
        tool="chat", operation="answer",
        purpose="personalise parent replies using guardian household income",
        metadata={
            "data_categories": ["guardian_income"],
            "data_use_purpose": ("use guardian household income to personalise "
                                 "parent replies"),
            "output_scope": "internal",
        })
    result = _one_action_result(
        rt, "personalise parent replies by household income", action)
    reds = [d for d in result.decisions if d.route == "RED"]
    assert len(reds) == 1, f"expected exactly one RED, got {result.routes}"
    assert any("differential treatment" in r for r in reds[0].reasons)
    assert not any(getattr(e, "affected_resources", None) for e in result.executions)


def test_safe_alternative_is_not_red(isolated_workspace: Path):
    """Using student progress / homework completion (the safe alternative) for
    the same personalisation goal must NOT be RED (test 7)."""
    rt = _runtime(isolated_workspace)
    action = CandidateAction(
        tool="chat", operation="answer",
        purpose=("personalise parent replies using student progress and "
                 "homework completion"),
        metadata={
            "data_categories": ["student_progress", "homework_completion"],
            "data_use_purpose": ("use student progress and homework completion "
                                 "to personalise parent replies"),
            "output_scope": "internal",
        })
    result = _one_action_result(rt, "personalise parent replies safely", action)
    assert not any(d.route == "RED" for d in result.decisions), result.routes


def test_guard_assess_red_and_safe_directly(isolated_workspace: Path):
    """Unit-level: assess() RED on socio+differential, NO_OVERRIDE on the safe
    variant, and inert NO_OVERRIDE on a plain legacy action."""
    g = DataUseGuard(config_dir=isolated_workspace / "configs")

    red = g.assess(CandidateAction(
        tool="chat", operation="answer",
        purpose="differentiate parent messages by guardian income",
        metadata={"data_categories": ["guardian_income"], "output_scope": "internal"}))
    assert red["decision"] == "RED"

    safe = g.assess(CandidateAction(
        tool="chat", operation="answer",
        purpose="differentiate parent messages by student progress",
        metadata={"data_categories": ["student_progress"], "output_scope": "internal"}))
    assert safe["decision"] != "RED"

    # legacy action, no workflow/data-use metadata, nothing sensitive → inert
    inert = g.assess(CandidateAction(
        tool="fs", operation="save_under_outputs",
        purpose="save the sports day schedule as a word file", metadata={}))
    assert inert["decision"] == "NO_OVERRIDE"
    assert inert["features"].get("inert_default") is True


def test_workflow_outputs_are_non_empty(isolated_workspace: Path):
    """§C / §N.9 — the workflow produces non-empty, recognisable deliverables in
    smart_mock mode: real bilingual drafts, never an apology or a blank file.
    BLUE save steps execute regardless of the GREEN gate, so reject_all is fine."""
    rt = _runtime(isolated_workspace)
    rt.run(raw_goal=WORKFLOW_GOAL_CN)
    files = [p for p in (isolated_workspace / "outputs").rglob("*.md") if p.is_file()]
    assert files, "workflow wrote no output files"
    bodies = [p.read_text(encoding="utf-8") for p in files]
    assert any(len(b) >= 200 for b in bodies), [len(b) for b in bodies]
    joined = "\n".join(bodies)
    assert "很抱歉" not in joined and "Sorry — I couldn't" not in joined, "apology leaked into a deliverable"
    assert any(("报告" in b or "Report" in b or "Facebook" in b) for b in bodies), \
        "no recognisable workflow content in the deliverables"


def test_trace_has_102w_and_101d(isolated_workspace: Path):
    """The audit trail surfaces both new modules for a workflow task (test 8)."""
    rt = _runtime(isolated_workspace)
    cap = _capture(rt)
    rt.run(raw_goal=WORKFLOW_GOAL_CN)
    mods = [(e["module"], e["event_type"]) for e in cap]
    assert ("102W", "workflow_detected") in mods
    assert ("101D", "data_use_assessed") in mods


# ── UI plumbing: server serves the workflow panel (Phase 6/7) ───────────────

def test_server_exposes_workflow_panel(monkeypatch):
    """The server builds + serves the workflow view the UI panel renders:
    detected workflow, five steps with real routes (incl. the GREEN external
    release), so the judge-visible panel is never empty for a workflow task."""
    import time

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TEOW_AGL_PLANNER", "smart_mock")
    from fastapi.testclient import TestClient
    from server.app import app

    c = TestClient(app)
    tid = c.post("/api/tasks", json={"raw_goal": WORKFLOW_GOAL_CN}).json()["task_id"]

    def _poll(*want, timeout=15):
        dl = time.time() + timeout
        st = None
        while time.time() < dl:
            st = c.get(f"/api/tasks/{tid}").json()
            if st["status"] in want:
                return st
            time.sleep(0.2)
        return st

    # The external-release step is GREEN → the runner blocks for human
    # approval. Reject it (demo: no real external action) so the run completes
    # and the server builds the workflow view.
    state = _poll("awaiting_approval", "done", "error")
    if state["status"] == "awaiting_approval":
        appr = state["pending_approvals"][0]
        c.post(f"/api/tasks/{tid}/decide",
               json={"approval_id": appr["approval_id"], "status": "rejected",
                     "note": "demo: no real external publish"})
        state = _poll("done", "error")
    assert state is not None and state["status"] == "done", f"final: {state}"
    wf = state.get("workflow")
    assert wf and wf["detected"] is True
    assert wf["workflow_id"] == "post_event_reporting"
    assert len(wf["steps"]) == 6
    routes = [s["route"] for s in wf["steps"]]
    assert "GREEN" in routes, routes  # external release elevated by 101D
    assert "RED" in routes, routes    # in-workflow self-block (income personalisation)
    assert all(r in ("BLUE", "GREEN", "RED") for r in routes), routes
    # the self-block surfaces in the panel's blocked list with its reason
    assert wf["blocked"], "expected a self-blocked action in the workflow panel"
    assert any("differential treatment" in r
               for b in wf["blocked"] for r in b.get("reasons", []))


# ── natural-language self-governance, end-to-end (the flagship claim) ────────

def test_natural_language_guardian_income_is_red(isolated_workspace: Path):
    """Free-text (UI path), EN + CJK: a request to use guardian income for
    differential parent treatment routes RED via 101D — not the GREEN-failsafe.
    This is the flagship claim, now true for natural input, not just metadata."""
    rt = _runtime(isolated_workspace)
    for goal in (
        "Use guardian household income to personalise which parents get called first.",
        "用家长的家庭收入来决定先打电话给哪些家长。",
    ):
        res = rt.run(raw_goal=goal)
        assert res.final_route == "RED", f"{goal!r} -> {res.final_route}"
        assert any("differential treatment" in r
                   for d in res.decisions if d.route == "RED" for r in d.reasons)


def test_safe_personalisation_not_red(isolated_workspace: Path):
    """Same differential intent but with SAFE data (student progress) must NOT
    be RED — guards the lexicon against over-firing on legitimate work."""
    rt = _runtime(isolated_workspace)
    res = rt.run(raw_goal="Personalise the parent messages using each student's "
                          "progress and homework completion.")
    assert res.final_route != "RED", res.final_route


def test_workflow_contains_self_block_step(isolated_workspace: Path):
    """The headline workflow itself proposes an income-based personalisation and
    self-blocks it (RED) — 'AI governs its own actions', with no user prompt."""
    rt = _runtime(isolated_workspace)
    res = rt.run(raw_goal=WORKFLOW_GOAL_CN)
    sb = next(a for a in res.plan.actions
              if a.metadata.get("workflow_step_id") == "consider_income_personalisation")
    dec = next(d for d in res.decisions if d.action_id == sb.action_id)
    assert dec.route == "RED"
    assert sb.metadata.get("data_use_decision") == "RED"
    assert not [e for e in res.executions
                if e.action_id == sb.action_id and getattr(e, "affected_resources", None)]
    # workflow not derailed: the external release still reaches the human gate
    assert any(d.route == "GREEN" for d in res.decisions)
