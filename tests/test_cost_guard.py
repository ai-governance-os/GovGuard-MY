"""Phase C — cost guard tests (offline)."""
from __future__ import annotations

import json
from datetime import date

import pytest

from teow_agl.policies.cost_guard import CostGuard


def _guard(tmp_path, limits=None, enabled=True) -> CostGuard:
    return CostGuard(
        {"enabled": enabled, "daily_limits": limits or {}},
        tmp_path / "ledger.json",
    )


# ===========================================================================
# Unit
# ===========================================================================

def test_allows_until_limit_then_blocks(tmp_path):
    g = _guard(tmp_path, {"planner_calls": 2})
    assert g.allow("planner_calls") is True
    g.record("planner_calls")
    assert g.allow("planner_calls") is True
    g.record("planner_calls")
    assert g.allow("planner_calls") is False


def test_unlimited_kind_always_allowed(tmp_path):
    g = _guard(tmp_path, {"planner_calls": 1})
    for _ in range(5):
        g.record("chat_calls")
    assert g.allow("chat_calls") is True  # no limit configured for kind


def test_disabled_guard_never_blocks(tmp_path):
    g = _guard(tmp_path, {"planner_calls": 0}, enabled=False)
    assert g.allow("planner_calls") is True


def test_env_kill_switch(tmp_path, monkeypatch):
    g = _guard(tmp_path, {"planner_calls": 0})
    assert g.allow("planner_calls") is False
    monkeypatch.setenv("COST_GUARD_ENABLED", "0")
    assert g.allow("planner_calls") is True


def test_ledger_resets_on_date_change(tmp_path):
    g = _guard(tmp_path, {"planner_calls": 1})
    g.record("planner_calls")
    assert g.allow("planner_calls") is False
    # Forge yesterday's ledger — must be discarded on next read.
    (tmp_path / "ledger.json").write_text(
        json.dumps({"date": "2020-01-01", "counts": {"planner_calls": 99}}),
        encoding="utf-8",
    )
    assert g.allow("planner_calls") is True


def test_snapshot_reports_remaining(tmp_path):
    g = _guard(tmp_path, {"planner_calls": 5})
    g.record("planner_calls", 2)
    snap = g.snapshot()
    assert snap["date"] == date.today().isoformat()
    assert snap["counts"]["planner_calls"] == 2
    assert snap["remaining"]["planner_calls"] == 3


def test_corrupt_ledger_recovers(tmp_path):
    (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
    g = _guard(tmp_path, {"planner_calls": 1})
    assert g.allow("planner_calls") is True
    g.record("planner_calls")
    assert g.allow("planner_calls") is False


# ===========================================================================
# Runtime integration — exhausted planner budget degrades gracefully
# ===========================================================================

class _CountingPlanner:
    planner_id = "counting_planner"

    def __init__(self):
        self.calls = 0

    def plan(self, planning_brief, system_prompt):
        self.calls += 1
        return {
            "refusal_id": "r", "task_id": planning_brief.get("task_id", "x"),
            "planner_id": self.planner_id, "refusal_type": "model_error",
            "message": "t", "raw_output_hash": "", "recovery_allowed": True,
        }


def _minimal_runtime(workspace, planner):
    from teow_agl.modules.module_105_human_gate import HumanGate
    from teow_agl.tools.mock_tools import MockTool
    from teow_agl.runtime import Runtime

    tools = {n: MockTool(n) for n in
             ["fs", "report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "memory", "chat",
              "image_gen"]}
    return Runtime(
        config_dir=workspace / "configs", prompts_dir=workspace / "prompts",
        planner=planner, tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=workspace / "traces",
    )


def test_runtime_blocks_planner_when_budget_zero(isolated_workspace):
    (isolated_workspace / "configs" / "cost_guard.json").write_text(
        json.dumps({"enabled": True, "daily_limits": {"planner_calls": 0}}),
        encoding="utf-8",
    )
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner)
    result = rt.run(raw_goal="please do the configured workflow")
    assert planner.calls == 0  # remote planner never reached
    assert result.refusal is not None
    assert result.refusal.refusal_type == "model_error"
    assert "budget_exhausted" in result.refusal.message
    events = [e["event_type"] for e in rt.trace.read_all()]
    assert "budget_exhausted" in events
    # Graceful recovery still produced a user-facing plan, no crash.
    assert result.plan is not None


def test_runtime_records_planner_calls(isolated_workspace):
    (isolated_workspace / "configs" / "cost_guard.json").write_text(
        json.dumps({"enabled": True, "daily_limits": {"planner_calls": 10}}),
        encoding="utf-8",
    )
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner)
    rt.run(raw_goal="please do the configured workflow")
    assert planner.calls == 1
    assert rt.cost_guard.snapshot()["counts"].get("planner_calls", 0) >= 1


def test_runtime_direct_paths_cost_nothing(isolated_workspace):
    """Identity/greeting/cached plans never touch the planner budget."""
    (isolated_workspace / "configs" / "cost_guard.json").write_text(
        json.dumps({"enabled": True, "daily_limits": {"planner_calls": 0}}),
        encoding="utf-8",
    )
    planner = _CountingPlanner()
    rt = _minimal_runtime(isolated_workspace, planner)
    result = rt.run(raw_goal="你是谁？")
    assert result.plan.planner_id == "identity_direct"
    assert not any(e["event_type"] == "budget_exhausted"
                   for e in rt.trace.read_all())


# ===========================================================================
# ChatLLM integration — over budget → "" degradation
# ===========================================================================

def test_chat_llm_returns_empty_when_budget_exhausted(tmp_path, monkeypatch):
    from teow_agl.adapters.chat_llm import ChatLLM
    from teow_agl.policies import cost_guard as cg_mod

    blocked = CostGuard({"enabled": True, "daily_limits": {"chat_calls": 0}},
                        tmp_path / "ledger.json")
    monkeypatch.setattr(cg_mod, "default_guard", lambda: blocked)

    called = {"n": 0}

    def fake_post(*a, **k):
        called["n"] += 1
        raise AssertionError("backend must not be reached over budget")

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    llm = ChatLLM(backend="anthropic")
    assert llm.chat("sys", "hi") == ""
    assert called["n"] == 0


def test_chat_llm_records_successful_calls(tmp_path, monkeypatch):
    from teow_agl.adapters.chat_llm import ChatLLM
    from teow_agl.policies import cost_guard as cg_mod

    guard = CostGuard({"enabled": True, "daily_limits": {"chat_calls": 10}},
                      tmp_path / "ledger.json")
    monkeypatch.setattr(cg_mod, "default_guard", lambda: guard)

    class _Resp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text", "text": "pong"}]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    llm = ChatLLM(backend="anthropic")
    assert llm.chat("sys", "ping") == "pong"
    assert guard.snapshot()["counts"]["chat_calls"] == 1
