"""GREEN delete: approve / reject."""
from __future__ import annotations

from pathlib import Path


def _delete_responder(target):
    def r(brief):
        return {
            "planning_mode": brief["planning_mode"],
            "actions": [
                {"action_id": "a1", "tool": "fs", "operation": "delete",
                 "target": str(target), "purpose": "delete user notes",
                 "expected_effect": "file removed",
                 "reversibility": "low", "uncertainty": "medium",
                 "risk_factors": [], "requires_governance": True, "metadata": {}}
            ],
        }
    return r


def test_green_delete_with_approval(make_runtime_factory):
    rt = make_runtime_factory(gate="approve_all")
    workspace = rt.profile.workspace_roots[0]
    target = Path(workspace) / "docs" / "old_notes.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("notes")
    rt.planner.adapter.responder = _delete_responder(target)
    result = rt.run(raw_goal=f"Delete {target}")
    assert any(d.route == "GREEN" for d in result.decisions)
    assert any(a.status == "approved" for a in result.approvals)
    assert any(e.status == "success" for e in result.executions)
    assert not target.exists()


def test_green_delete_rejected(make_runtime_factory):
    rt = make_runtime_factory(gate="reject_all")
    workspace = rt.profile.workspace_roots[0]
    target = Path(workspace) / "docs" / "old_notes.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("notes")
    rt.planner.adapter.responder = _delete_responder(target)
    result = rt.run(raw_goal=f"Delete {target}")
    assert any(d.route == "GREEN" for d in result.decisions)
    assert all(a.status == "rejected" for a in result.approvals)
    assert result.executions == []
    assert target.exists()
