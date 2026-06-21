"""Module 102 — Planning-only LLM wrapper."""
from __future__ import annotations

from pathlib import Path

from ..adapters.base import PlannerAdapter
from ..models import CandidateAction, CandidatePlan, PlannerRefusal


class PlannerModule:
    module_id = "102"

    def __init__(self, adapter: PlannerAdapter, system_prompt_path: str | Path) -> None:
        self.adapter = adapter
        self.system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")

    def plan(self, planning_brief: dict) -> CandidatePlan | PlannerRefusal:
        raw = self.adapter.plan(planning_brief, self.system_prompt)
        if "refusal_type" in raw:
            return PlannerRefusal(
                refusal_id=raw.get("refusal_id", ""),
                task_id=raw.get("task_id", planning_brief.get("task_id", "unknown")),
                planner_id=raw.get("planner_id", getattr(self.adapter, "planner_id", "unknown")),
                refusal_type=raw["refusal_type"],
                message=raw.get("message", ""),
                raw_output_hash=raw.get("raw_output_hash", ""),
                recovery_allowed=bool(raw.get("recovery_allowed", True)),
            )
        actions: list[CandidateAction] = []
        for a in raw.get("actions", []):
            # Coerce common LLM omissions: target/purpose/expected_effect may
            # come back as null; the schema requires strings. Empty string
            # is then handled by the safety stack downstream.
            a = dict(a)
            for k in ("target", "purpose", "expected_effect"):
                if a.get(k) is None:
                    a[k] = ""
            if a.get("risk_factors") is None:
                a["risk_factors"] = []
            if a.get("metadata") is None:
                a["metadata"] = {}
            try:
                actions.append(CandidateAction(**a))
            except Exception:
                # skip malformed actions rather than crash the whole plan
                continue
        return CandidatePlan(
            plan_id=raw.get("plan_id", ""),
            task_id=raw.get("task_id", planning_brief.get("task_id", "unknown")),
            planner_id=raw.get("planner_id", getattr(self.adapter, "planner_id", "unknown")),
            planning_mode=raw.get("planning_mode", planning_brief.get("planning_mode", "direct")),
            used_refusal_recovery=bool(raw.get("used_refusal_recovery", False)),
            actions=actions,
            notes=list(raw.get("notes", [])),
        )
