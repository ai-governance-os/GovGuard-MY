"""Integration tests for Module 110 + the full runtime.

The unit tests in test_verifier.py prove the verifier's own logic.
THIS file proves the *runtime integration*:

  * verifier runs after _execute_actions, before _record_task_outcome
  * a failed verification appends a synthetic 'failed' execution so
    SubjectConfidence + plan_cache see the task as a failure
  * result.verification is populated regardless of pass/fail
  * VERIFIER_ENABLED=0 disables the step end-to-end
  * verifier exceptions are caught (don't break the pipeline)
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool


# ---------------------------------------------------------------------------
# Deterministic planner — copies the pattern from test_execute_direct.py
# and test_reflector_governance_hooks.py, kept local to this file so we
# can tweak the planned action per-test without touching shared fixtures.
# ---------------------------------------------------------------------------
class _StubPlanner:
    planner_id = "stub_planner"

    def __init__(self, responder):
        self.responder = responder

    def plan(self, brief, system_prompt):
        out = self.responder(brief)
        out.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:8]}")
        out.setdefault("task_id", brief.get("task_id", "unknown"))
        out.setdefault("planner_id", self.planner_id)
        out.setdefault("planning_mode", brief.get("planning_mode", "direct"))
        out.setdefault("used_refusal_recovery", False)
        out.setdefault("notes", [])
        return out


def _make_runtime(isolated_workspace: Path, responder) -> Runtime:
    workspace_roots = [
        str(isolated_workspace / "workspace"),
        str(isolated_workspace / "outputs"),
    ]
    tools = {n: MockTool(n) for n in
             ["report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "human", "memory",
              "chat", "image_gen"]}
    tools["fs"] = FilesystemTool(workspace_roots)
    rt = Runtime(
        config_dir=isolated_workspace / "configs",
        prompts_dir=isolated_workspace / "prompts",
        planner=_StubPlanner(responder),
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=isolated_workspace / "traces",
        subject_confidence_path=isolated_workspace / "state" / "sc.jsonl",
    )
    rt.profile.profile["workspace_roots"] = workspace_roots
    return rt


def _good_fs_action(target: Path) -> dict:
    return {
        "action_id": "a1",
        "tool": "fs",
        "operation": "save_under_outputs",
        "target": str(target),
        "purpose": "write content",
        "expected_effect": "file written",
        "reversibility": "high",
        "uncertainty": "low",
        "risk_factors": [],
        "requires_governance": True,
        "metadata": {"content": "some content that's reasonable in length"},
    }


# ===========================================================================
# verifier is wired in by default
# ===========================================================================

def test_verifier_attached_by_default(isolated_workspace):
    """Construct a Runtime without passing `verifier=...` and confirm
    a VerifierModule is wired in from configs/verifier_rules.json."""
    def responder(brief):
        target = isolated_workspace / "outputs" / "default.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target)]}
    rt = _make_runtime(isolated_workspace, responder)
    assert rt.verifier is not None
    assert isinstance(rt.verifier, VerifierModule)
    assert rt.verifier_rules  # non-empty dict loaded from JSON


# ===========================================================================
# Pass path
# ===========================================================================

def test_verification_passes_for_simple_task(isolated_workspace):
    """Simple file-write task with no length / format / refusal
    issues → result.verification.pass == True."""
    target = isolated_workspace / "outputs" / "simple.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target)]}

    rt = _make_runtime(isolated_workspace, responder)
    result = rt.run(raw_goal="Save a quick note for the verifier")
    assert any(e.status == "success" for e in result.executions)
    assert result.verification is not None
    assert result.verification["pass"] is True
    assert result.verification["enabled"] is True


# ===========================================================================
# Fail path — length too short coerces outcome to failure
# ===========================================================================

def test_failed_verification_coerces_outcome_to_failure(isolated_workspace):
    """The runtime appends a synthetic 'failed' execution when verification
    fails, so SubjectConfidence records a failure outcome."""
    target = isolated_workspace / "outputs" / "tiny.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [{
                "action_id": "a1",
                "tool": "fs",
                "operation": "save_under_outputs",
                "target": str(target),
                "purpose": "write a tiny note for an essay request",
                "expected_effect": "file written",
                "reversibility": "high",
                "uncertainty": "low",
                "risk_factors": [],
                "requires_governance": True,
                # Tiny content for a 500-word request → length_check fails
                "metadata": {"content": "too short"},
            }],
        }

    rt = _make_runtime(isolated_workspace, responder)
    # Goal triggers length_check (500-word intent)
    result = rt.run(raw_goal="Write a 500-word markdown essay on AGI ethics")

    assert result.verification is not None
    assert result.verification["pass"] is False
    # The synthetic failed execution must be present
    synthetic_failures = [
        e for e in result.executions
        if e.action_id == "verifier_synthetic" and e.status == "failed"
    ]
    assert len(synthetic_failures) == 1
    assert "verifier_failed" in (synthetic_failures[0].error or "")


def test_failed_verification_emits_trace_event(isolated_workspace):
    target = isolated_workspace / "outputs" / "tiny2.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [{
                "action_id": "a1", "tool": "fs",
                "operation": "save_under_outputs", "target": str(target),
                "purpose": "tiny note", "expected_effect": "file",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": "x"},
            }],
        }

    rt = _make_runtime(isolated_workspace, responder)
    emitted: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        emitted.append({"module": ev.module, "event_type": ev.event_type})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    rt.run(raw_goal="Write a 500-word markdown essay")
    modules = [e["module"] for e in emitted]
    types = [e["event_type"] for e in emitted]
    assert "110" in modules
    assert "verification_failed" in types


def test_passing_verification_emits_passed_event(isolated_workspace):
    target = isolated_workspace / "outputs" / "ok.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target)]}

    rt = _make_runtime(isolated_workspace, responder)
    emitted: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        emitted.append({"module": ev.module, "event_type": ev.event_type})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    rt.run(raw_goal="Save a quick note")
    types_110 = [e["event_type"] for e in emitted if e["module"] == "110"]
    # Could be `verification_passed` or `verification_skipped` depending
    # on whether any check applied; both are acceptable here.
    assert types_110, "verifier should have emitted at least one event"
    assert any(t in ("verification_passed", "verification_skipped")
               for t in types_110)


# ===========================================================================
# Kill switch
# ===========================================================================

def test_verifier_disabled_via_env(isolated_workspace, monkeypatch):
    """`VERIFIER_ENABLED=0` short-circuits the verifier step so the
    runtime behaves exactly as before Phase 12."""
    monkeypatch.setenv("VERIFIER_ENABLED", "0")
    target = isolated_workspace / "outputs" / "kill.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [{
                "action_id": "a1", "tool": "fs",
                "operation": "save_under_outputs", "target": str(target),
                "purpose": "tiny", "expected_effect": "file",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": "x"},
            }],
        }

    rt = _make_runtime(isolated_workspace, responder)
    result = rt.run(raw_goal="Write a 500-word markdown essay")
    # Disabled → verification stays None
    assert result.verification is None
    # And the synthetic failed execution must NOT have been appended
    synthetic = [
        e for e in result.executions
        if e.action_id == "verifier_synthetic"
    ]
    assert synthetic == []


# ===========================================================================
# Robustness — exceptions don't break the pipeline
# ===========================================================================

def test_verifier_exception_does_not_break_pipeline(isolated_workspace,
                                                    monkeypatch):
    """If the verifier raises (e.g. malformed rules), the runtime
    emits a verification_error trace event and proceeds normally."""
    target = isolated_workspace / "outputs" / "boom.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {"planning_mode": brief["planning_mode"],
                "actions": [_good_fs_action(target)]}

    rt = _make_runtime(isolated_workspace, responder)

    # Monkey-patch verify() to raise
    def _broken_verify(**kwargs):
        raise RuntimeError("synthetic boom for test")

    rt.verifier.verify = _broken_verify  # type: ignore[method-assign]

    emitted: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        emitted.append({"module": ev.module, "event_type": ev.event_type})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    # Should not raise
    result = rt.run(raw_goal="Save a note")
    types_110 = [e["event_type"] for e in emitted if e["module"] == "110"]
    assert "verification_error" in types_110
    # Pipeline still completed
    assert result.final_route != ""


# ===========================================================================
# Interaction with reflector — reflector should see the failed verification
# in the result (via the synthetic execution) and treat the task as failed.
# ===========================================================================

def test_failed_verification_makes_outcome_failure_for_learning(
    isolated_workspace,
):
    """SubjectConfidence should record a failure outcome for a task
    whose verification failed, even though the executor itself
    succeeded. This is what stops the agent from cache-hitting broken
    outputs."""
    target = isolated_workspace / "outputs" / "regress.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [{
                "action_id": "a1", "tool": "fs",
                "operation": "save_under_outputs", "target": str(target),
                "purpose": "tiny", "expected_effect": "file",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": "tiny"},
            }],
        }

    rt = _make_runtime(isolated_workspace, responder)
    result = rt.run(raw_goal="Write a 500-word markdown essay")

    # Outcome should be `failure` (not `success`) for SubjectConfidence.
    # The snapshot uses pluralised keys (`failures` / `successes`).
    snap = rt.subject_confidence.snapshot()
    assert result.pre_assessment.task_category in snap
    cat_stats = snap[result.pre_assessment.task_category]
    assert cat_stats.get("failures", 0) >= 1, \
        f"expected at least one failure recorded; got snapshot={cat_stats}"
    # And no false success
    assert cat_stats.get("successes", 0) == 0, \
        f"verifier should have coerced this to failure, not success"
