"""102R refusal recovery."""
from __future__ import annotations

from teow_agl.adapters.mock_planner import MockPlanner


def test_refusal_recovery_for_delete(make_runtime_factory):
    rt = make_runtime_factory(planner=MockPlanner(force_refusal="context_sensitive_overrefusal"),
                              gate="approve_all")
    workspace = rt.profile.workspace_roots[0]
    target = workspace + "/temp/old.txt"
    result = rt.run(raw_goal=f"Delete the file {target}")
    assert result.refusal is not None
    assert result.refusal.refusal_type == "context_sensitive_overrefusal"
    assert result.plan is not None
    assert result.plan.used_refusal_recovery is True
    operations = [a.operation for a in result.plan.actions]
    assert "request_approval" in operations


def test_refusal_recovery_universal_hard_safety_escalates(make_runtime_factory):
    rt = make_runtime_factory(planner=MockPlanner(force_refusal="universal_hard_safety_refusal"))
    result = rt.run(raw_goal="Delete all files in outputs/temp")
    assert result.escalation is not None
    assert any(d.route == "RED" for d in result.decisions)
    assert result.plan is None
