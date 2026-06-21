"""High-value asset routing."""
from __future__ import annotations

from pathlib import Path


def test_patent_file_delete_routes_green_and_rejection_blocks(make_runtime_factory):
    rt = make_runtime_factory(gate="reject_all")
    workspace = rt.profile.workspace_roots[0]
    target = Path(workspace) / "patent" / "claims.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("patent claims")

    def responder(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [
                {"action_id": "a1", "tool": "fs", "operation": "delete",
                 "target": str(target), "purpose": "delete patent",
                 "expected_effect": "removed",
                 "reversibility": "low", "uncertainty": "high",
                 "risk_factors": ["patent_high_value"], "requires_governance": True, "metadata": {}}
            ],
        }
    rt.planner.adapter.responder = responder
    result = rt.run(raw_goal=f"Delete {target}")
    assert any(d.route == "GREEN" for d in result.decisions)
    assert any(r.features.get("high_value_asset") for r in result.risk_assessments)
    assert target.exists()
    assert result.executions == []
