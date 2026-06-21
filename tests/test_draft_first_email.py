"""Draft-first email."""
from __future__ import annotations

from teow_agl.adapters.mock_planner import MockPlanner


def test_draft_first_email_routes_green_after_draft(make_runtime_factory):
    rt = make_runtime_factory(planner=MockPlanner(force_refusal="context_sensitive_overrefusal"),
                              gate="approve_all")
    result = rt.run(raw_goal="Email the report to client@example.com")
    assert result.pre_assessment.task_category == "external_email"
    assert result.pre_assessment.planning_mode == "draft_first"
    operations = [a.operation for a in result.plan.actions]
    assert operations[0] == "compose_draft"
    assert "request_approval" in operations
    assert any(d.route == "GREEN" for d in result.decisions)
