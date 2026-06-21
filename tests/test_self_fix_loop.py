"""Phase 14 — Self-fix loop integration tests.

The verifier-judge unit tests in test_verifier_llm_judge.py only cover
the judge method in isolation. THIS file proves the *runtime self-fix
loop* — that when the judge fails, runtime really does re-run the
pipeline with feedback in the brief, capped per config, with proper
trace events and kill-switches.

Covers:
  * Judge pass → no retry (single _run_once call)
  * Judge fail + self-fix enabled → 1 retry runs; brief contains
    `prior_attempt` with judge issues + suggestions
  * Judge keeps failing → exhausts max_iterations; emits
    `self_fix_exhausted`
  * Judge fails then passes → emits `self_fix_succeeded`;
    verification.self_fix_recovered = True
  * AUTO_FIX_ENABLED=0 → no retry even on failure
  * LLM_JUDGE_ENABLED=0 → no retry even on failure
  * self_fix.enabled=False in config → no retry
  * sub-goal task (`_is_subgoal=True`) → no self-fix at sub-task level
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool


# ---------------------------------------------------------------------------
# Stub planner that always emits the same fs.save action; counts calls
# so we can assert "1 attempt", "3 attempts", etc.
# ---------------------------------------------------------------------------
class _StubPlanner:
    planner_id = "stub_planner"

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0
        self.briefs_seen: list[dict] = []

    def plan(self, brief, system_prompt):
        self.calls += 1
        self.briefs_seen.append(dict(brief))
        out = self.responder(brief)
        out.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:8]}")
        out.setdefault("task_id", brief.get("task_id", "unknown"))
        out.setdefault("planner_id", self.planner_id)
        out.setdefault("planning_mode", brief.get("planning_mode", "direct"))
        out.setdefault("used_refusal_recovery", False)
        out.setdefault("notes", [])
        return out


# ---------------------------------------------------------------------------
# Programmable stub judge — returns different scores on successive calls
# so we can simulate "fail then pass" / "fail every time" / etc.
# ---------------------------------------------------------------------------
class _ScriptedJudgeLLM:
    """Each chat_json call pops the next scripted response.

    The runtime calls chat_json once per _run_once invocation (since the
    judge runs in _run_verification). Test scripts a list of dicts;
    when exhausted, returns the last one again so we don't crash if
    a test re-runs more than expected.
    """

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.calls = 0

    def chat_json(self, system: str, user: str, max_tokens: int = 600) -> dict:
        self.calls += 1
        if self.calls <= len(self.script):
            return dict(self.script[self.calls - 1])
        return dict(self.script[-1])

    def chat(self, system: str, user: str, max_tokens: int = 600) -> str:
        return "{}"


# ---------------------------------------------------------------------------
# Fixture
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


def _make_runtime(workspace: Path,
                  planner: _StubPlanner,
                  judge_script: list[dict]) -> Runtime:
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
        planner=planner,
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=workspace / "traces",
    )
    rt.profile.profile["workspace_roots"] = workspace_roots
    # Inject the scripted judge LLM
    judge_llm = _ScriptedJudgeLLM(judge_script)
    rt.verifier.chat_llm = judge_llm
    # Stash the LLM on the runtime so tests can poke at it
    rt._test_judge_llm = judge_llm  # type: ignore[attr-defined]
    return rt


# ===========================================================================
# Pass path — no retry
# ===========================================================================

def test_judge_pass_means_no_retry(isolated_workspace):
    """Judge returns score=80 (≥ default threshold 60) → no retry."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="anything")],
    })
    rt = _make_runtime(isolated_workspace, planner,
                       judge_script=[{"score": 80, "issues": [],
                                      "suggestions": []}])
    result = rt.run(raw_goal="A question that needs answering "
                              "(must be > 12 chars to skip min_task)")
    assert planner.calls == 1
    assert result.verification is not None
    judge = result.verification.get("judge") or {}
    assert judge.get("pass") is True
    # No retry → no self_fix_iterations annotation
    assert result.verification.get("self_fix_iterations") in (None, 0)


# ===========================================================================
# Fail → retry → succeed
# ===========================================================================

def test_judge_fail_then_pass_recovers(isolated_workspace):
    """First attempt fails judge (score 20); second attempt passes
    (score 90). Should see planner called twice and
    verification.self_fix_recovered = True."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="content")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 20, "issues": ["gibberish"], "suggestions": ["rewrite"]},
        {"score": 90, "issues": [], "suggestions": []},
    ])
    result = rt.run(raw_goal="A question that needs an answer for testing")
    assert planner.calls == 2  # 1 initial + 1 retry
    assert result.verification is not None
    assert result.verification.get("self_fix_iterations") == 1
    assert result.verification.get("self_fix_recovered") is True


def test_retry_brief_contains_prior_attempt_feedback(isolated_workspace):
    """The second planner call's brief MUST contain `prior_attempt`
    with the judge's issues + suggestions from the first attempt."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="content")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 20, "issues": ["body is too short", "wrong tone"],
         "suggestions": ["write 200 more words", "use formal voice"]},
        {"score": 90, "issues": [], "suggestions": []},
    ])
    rt.run(raw_goal="A question that needs an answer for testing")
    # First brief: no prior_attempt yet
    assert "prior_attempt" not in planner.briefs_seen[0]
    # Second brief: has prior_attempt with the judge's feedback
    second = planner.briefs_seen[1]
    assert "prior_attempt" in second
    pa = second["prior_attempt"]
    assert pa["iteration"] == 1
    assert pa["judge_score"] == 20
    assert "body is too short" in pa["judge_issues"]
    assert "use formal voice" in pa["judge_suggestions"]


# ===========================================================================
# Exhaustion path
# ===========================================================================

def test_judge_fail_exhausts_max_iterations(isolated_workspace):
    """Judge fails on every attempt → runtime stops after max_iterations
    retries. Default is 2, so total attempts = 1 + 2 = 3."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="bad")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 10, "issues": ["bad"], "suggestions": ["try again"]},
        {"score": 10, "issues": ["still bad"], "suggestions": ["try again"]},
        {"score": 10, "issues": ["still bad"], "suggestions": ["try again"]},
    ])
    result = rt.run(raw_goal="A question that needs an answer for testing")
    # max_iterations=2 in default config → 1 initial + 2 retries = 3
    assert planner.calls == 3
    assert result.verification is not None
    assert result.verification.get("self_fix_iterations") == 2
    assert result.verification.get("self_fix_recovered") is False


def test_self_fix_emits_trace_events(isolated_workspace):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="bad")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 10, "issues": [], "suggestions": []},
        {"score": 90, "issues": [], "suggestions": []},
    ])
    emitted: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        emitted.append({"module": ev.module, "event_type": ev.event_type})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    rt.run(raw_goal="A question that needs an answer for testing")
    types = [e["event_type"] for e in emitted if e["module"] == "LOOP"]
    assert "self_fix_retry" in types
    assert "self_fix_succeeded" in types


def test_exhaustion_emits_self_fix_exhausted(isolated_workspace):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="bad")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 10}, {"score": 10}, {"score": 10}, {"score": 10},
    ])
    emitted: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        emitted.append({"module": ev.module, "event_type": ev.event_type})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    rt.run(raw_goal="A question that needs an answer for testing")
    types_loop = [e["event_type"] for e in emitted if e["module"] == "LOOP"]
    assert "self_fix_exhausted" in types_loop


# ===========================================================================
# Kill switches
# ===========================================================================

def test_auto_fix_env_disabled_skips_retry(isolated_workspace, monkeypatch):
    """AUTO_FIX_ENABLED=0 → no retry even if judge fails."""
    monkeypatch.setenv("AUTO_FIX_ENABLED", "0")
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="bad")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 10, "issues": [], "suggestions": []},
        {"score": 10, "issues": [], "suggestions": []},
    ])
    result = rt.run(raw_goal="A question that needs an answer for testing")
    assert planner.calls == 1  # no retry
    # The verification still shows judge fail, but no self-fix annotation
    assert result.verification is not None
    assert result.verification.get("self_fix_iterations") in (None, 0)


def test_llm_judge_env_disabled_skips_retry(isolated_workspace, monkeypatch):
    """LLM_JUDGE_ENABLED=0 → self-fix won't fire either (since the
    judge feedback is what drives the loop)."""
    monkeypatch.setenv("LLM_JUDGE_ENABLED", "0")
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="bad")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 10}, {"score": 10},
    ])
    result = rt.run(raw_goal="A question that needs an answer for testing")
    assert planner.calls == 1


def test_self_fix_disabled_in_config_skips_retry(isolated_workspace):
    """If `self_fix.enabled = false` in verifier_rules, no retry."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    planner = _StubPlanner(lambda brief: {
        "planning_mode": brief["planning_mode"],
        "actions": [_good_fs_action(target, content="bad")],
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        {"score": 10}, {"score": 10},
    ])
    # Mutate config in-place after Runtime construction
    rt.verifier_rules["self_fix"]["enabled"] = False
    result = rt.run(raw_goal="A question that needs an answer for testing")
    assert planner.calls == 1


# ===========================================================================
# Phase A4 — model_error refusal must NOT trigger the self-fix loop.
# A Groq 413/429 is an API failure, not a content-quality problem.
# Retrying just re-hits the limit and burns quota (trace 2026-05-20
# showed a single 413'd task retried 3× → 49 wasted events).
# ===========================================================================

def test_model_error_refusal_skips_self_fix(isolated_workspace):
    """Planner returns a model_error refusal (simulating Groq 413).
    Even with a failing judge, self-fix must NOT retry."""
    planner = _StubPlanner(lambda brief: {
        "refusal_type": "model_error",
        "message": "Client error '413 Payload Too Large'",
        "recovery_allowed": True,
    })
    rt = _make_runtime(isolated_workspace, planner, judge_script=[
        # Judge would fail the graceful-fallback message — without the
        # guard this would drive 2 retries.
        {"score": 10, "issues": ["did not answer"], "suggestions": ["retry"]},
        {"score": 10}, {"score": 10},
    ])
    result = rt.run(raw_goal="A question that needs an answer for testing")
    # Exactly ONE planner call — the refusal — and no self-fix retries.
    assert planner.calls == 1
    assert result.refusal is not None
    assert result.refusal.refusal_type == "model_error"
    # No self-fix annotation: the loop never ran.
    assert (result.verification or {}).get("self_fix_iterations") in (None, 0)


def test_model_error_refusal_still_produces_recovery_answer(isolated_workspace):
    """The model_error short-circuit must not swallow the recovery plan —
    the user still gets the graceful 102R chat.answer."""
    planner = _StubPlanner(lambda brief: {
        "refusal_type": "model_error",
        "message": "Client error '429 Too Many Requests'",
        "recovery_allowed": True,
    })
    rt = _make_runtime(isolated_workspace, planner,
                       judge_script=[{"score": 50}])
    result = rt.run(raw_goal="你能帮我分析这个问题吗")
    # A recovery plan exists and carries a non-empty chat answer.
    assert result.plan is not None
    assert result.plan.used_refusal_recovery is True
    chat_actions = [a for a in result.plan.actions if a.tool == "chat"]
    assert chat_actions, "recovery should include a chat.answer"
    assert chat_actions[0].metadata.get("body", "").strip() != ""
