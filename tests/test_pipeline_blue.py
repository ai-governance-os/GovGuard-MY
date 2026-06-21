"""BLUE_REPORT_GENERATION."""
from __future__ import annotations

from pathlib import Path

from teow_agl.adapters.mock_planner import MockPlanner


def _report_responder(brief, target_path):
    return {
        "planning_mode": brief["planning_mode"],
        "actions": [
            {"action_id": "a1", "tool": "report", "operation": "draft_report",
             "target": str(target_path), "purpose": "draft a report",
             "expected_effect": "produce report markdown",
             "reversibility": "high", "uncertainty": "low",
             "risk_factors": [], "requires_governance": True,
             "metadata": {"topic": "AI governance"}},
            {"action_id": "a2", "tool": "fs", "operation": "save_under_outputs",
             "target": str(target_path), "purpose": "save report",
             "expected_effect": "file written",
             "reversibility": "high", "uncertainty": "low",
             "risk_factors": [], "requires_governance": True,
             "metadata": {"content": "# Report\nsample"}},
        ],
    }


def test_blue_report_generation(make_runtime_factory):
    rt = make_runtime_factory(planner=MockPlanner(), gate="reject_all")
    outputs = rt.profile.workspace_roots[1]
    Path(outputs).mkdir(parents=True, exist_ok=True)
    target_path = Path(outputs) / "report.md"
    rt.planner.adapter.responder = lambda b: _report_responder(b, target_path)
    result = rt.run(raw_goal="Create a short report about AI governance and save it under outputs/report.md")
    assert result.pre_assessment.task_category == "report_generation"
    assert all(d.route == "BLUE" for d in result.decisions), [d.reasons for d in result.decisions]
    assert any(e.status == "success" for e in result.executions)
    assert target_path.exists()
