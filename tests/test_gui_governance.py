"""GUI automation governance: routing + hard-block patterns + plan shape."""
from __future__ import annotations

from teow_agl.adapters.smart_mock_planner import SmartMockPlanner


def test_screenshot_request_classifies_gui_automation(make_runtime_factory):
    rt = make_runtime_factory(planner=SmartMockPlanner(), gate="approve_all")
    result = rt.run(raw_goal="Take a screenshot of my screen")
    assert result.pre_assessment.task_category == "gui_automation"
    assert result.pre_assessment.planning_mode == "approval_first"
    # screenshot is read-only -> should appear; with profile.gui_control_action
    # in approval_required_actions, route should be at least GREEN... BUT
    # screenshot isn't in gui_operations (only in gui_safe_operations), so
    # gui_automation feature is False -> recommended BLUE. That's the correct
    # behavior: pure read-only operations don't need approval.
    routes = [d.route for d in result.decisions]
    assert "RED" not in routes


def test_click_request_routes_green_with_screenshot_first(make_runtime_factory):
    rt = make_runtime_factory(planner=SmartMockPlanner(), gate="reject_all")
    result = rt.run(raw_goal="Click at (500, 600)")
    assert result.pre_assessment.task_category == "gui_automation"
    operations = [a.operation for a in result.plan.actions]
    # plan should be screenshot first, click second
    assert operations[0] == "screenshot"
    assert "mouse_click" in operations
    routes = [d.route for d in result.decisions]
    # screenshot routes BLUE, mouse_click should route GREEN
    assert "GREEN" in routes
    # rejected -> click should not have executed
    assert all(e.output_summary != "mock_mouse_click" for e in result.executions)


def test_type_request_routes_green(make_runtime_factory):
    rt = make_runtime_factory(planner=SmartMockPlanner(), gate="approve_all")
    result = rt.run(raw_goal="Type 'hello world' into the active window")
    assert result.pre_assessment.task_category == "gui_automation"
    operations = [a.operation for a in result.plan.actions]
    assert "keyboard_type" in operations
    assert any(d.route == "GREEN" for d in result.decisions)


def test_dangerous_hotkey_in_user_goal_blocked_at_101a(make_runtime_factory):
    """User goal containing 'ctrl+alt+del' (a hard_red_pattern) routes RED at 101A."""
    rt = make_runtime_factory(planner=SmartMockPlanner())
    result = rt.run(raw_goal="Press ctrl+alt+del to lock the workstation")
    assert result.pre_assessment.hard_block is True
    assert any(d.route == "RED" for d in result.decisions)
    assert result.plan is None


def test_winr_in_user_goal_blocked_at_101a(make_runtime_factory):
    rt = make_runtime_factory(planner=SmartMockPlanner())
    result = rt.run(raw_goal="Press win+r and type cmd")
    assert result.pre_assessment.hard_block is True
    assert any(d.route == "RED" for d in result.decisions)
