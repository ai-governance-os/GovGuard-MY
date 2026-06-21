"""Audit trace contract."""
from __future__ import annotations

from teow_agl.adapters.mock_planner import MockPlanner


def test_trace_contains_module_events(make_runtime_factory):
    rt = make_runtime_factory(planner=MockPlanner(force_refusal="context_sensitive_overrefusal"),
                              gate="approve_all")
    rt.run(raw_goal="Email the report to client@example.com")
    events = rt.trace.read_all()
    assert events
    modules = {e["module"] for e in events}
    expected = {"106", "101A", "102", "102R", "101B", "103", "105"}
    assert expected.issubset(modules), f"missing: {expected - modules}"
    required = {"event_id", "timestamp", "session_id", "task_id", "module", "event_type"}
    for ev in events:
        assert required.issubset(ev.keys())


def test_trace_redacts_secret_keys(make_runtime_factory):
    rt = make_runtime_factory()
    rt.trace.emit(session_id="s", task_id="t", module="test", event_type="manual",
                  details={"password": "abc", "ok": "ok"})
    last = rt.trace.read_all()[-1]
    assert last["details"]["password"] == "<REDACTED>"
    assert last["details"]["ok"] == "ok"
