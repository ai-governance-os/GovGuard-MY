"""Deterministic planner for tests."""
from __future__ import annotations

import uuid
from typing import Callable


class MockPlanner:
    planner_id = "mock_planner"

    def __init__(
        self,
        responder: Callable[[dict], dict] | None = None,
        force_refusal: str | None = None,
    ) -> None:
        self.responder = responder
        self.force_refusal = force_refusal

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        if self.force_refusal:
            return self._refusal(planning_brief, self.force_refusal, "forced_by_test")
        if self.responder is not None:
            out = self.responder(planning_brief)
            if "refusal_type" not in out:
                out.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:12]}")
                out.setdefault("task_id", planning_brief.get("task_id", "unknown"))
                out.setdefault("planner_id", self.planner_id)
                out.setdefault("planning_mode", planning_brief.get("planning_mode", "direct"))
                out.setdefault("used_refusal_recovery", False)
                out.setdefault("notes", [])
            return out
        return self._refusal(planning_brief, "empty_plan", "no_responder_configured")

    def _refusal(self, brief: dict, refusal_type: str, message: str) -> dict:
        return {
            "refusal_id": f"refusal_{uuid.uuid4().hex[:12]}",
            "task_id": brief.get("task_id", "unknown"),
            "planner_id": self.planner_id,
            "refusal_type": refusal_type,
            "message": message,
            "raw_output_hash": "",
            "recovery_allowed": refusal_type != "universal_hard_safety_refusal",
        }
