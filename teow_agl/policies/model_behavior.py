"""Model behavior profile reader/updater."""
from __future__ import annotations


class ModelBehaviorView:
    def __init__(self, profile: dict) -> None:
        self.profile = profile

    def model_entry(self, model_id: str) -> dict:
        return self.profile.setdefault("models", {}).setdefault(
            model_id,
            {"calls": 0, "refusals": 0, "recoveries": 0, "successful_recoveries": 0,
             "refusal_prone_categories": {}},
        )

    def record_call(self, model_id: str) -> None:
        e = self.model_entry(model_id)
        e["calls"] = int(e.get("calls", 0)) + 1
        g = self.profile.setdefault("global_refusal_recovery_stats", {})
        g["total_planner_calls"] = int(g.get("total_planner_calls", 0)) + 1

    def record_refusal(self, model_id: str, task_category: str, refusal_type: str) -> None:
        e = self.model_entry(model_id)
        e["refusals"] = int(e.get("refusals", 0)) + 1
        cats = e.setdefault("refusal_prone_categories", {})
        key = f"{task_category}|{refusal_type}"
        cats[key] = int(cats.get(key, 0)) + 1
        g = self.profile.setdefault("global_refusal_recovery_stats", {})
        g["total_refusals"] = int(g.get("total_refusals", 0)) + 1

    def record_recovery(self, model_id: str, success: bool) -> None:
        e = self.model_entry(model_id)
        e["recoveries"] = int(e.get("recoveries", 0)) + 1
        if success:
            e["successful_recoveries"] = int(e.get("successful_recoveries", 0)) + 1
        g = self.profile.setdefault("global_refusal_recovery_stats", {})
        g["total_recoveries"] = int(g.get("total_recoveries", 0)) + 1
        if success:
            g["successful_recoveries"] = int(g.get("successful_recoveries", 0)) + 1
