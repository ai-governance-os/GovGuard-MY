"""Model behavior profile updated on refusal+recovery."""
from __future__ import annotations

from teow_agl.adapters.mock_planner import MockPlanner


def test_model_behavior_records_refusal_and_recovery(make_runtime_factory):
    rt = make_runtime_factory(planner=MockPlanner(force_refusal="context_sensitive_overrefusal"),
                              gate="approve_all")
    rt.run(raw_goal="Clean up old temp files")
    profile = rt.cfg.model_behavior_profile
    g = profile.get("global_refusal_recovery_stats", {})
    assert g.get("total_planner_calls", 0) >= 1
    assert g.get("total_refusals", 0) >= 1
    assert g.get("total_recoveries", 0) >= 1
    me = profile.get("models", {}).get("mock_planner", {})
    assert me.get("refusals", 0) >= 1
    assert me.get("recoveries", 0) >= 1
