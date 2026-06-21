"""Ollama planner adapter (local, free)."""
from __future__ import annotations

import json
import re
import uuid


class OllamaPlanner:
    planner_id = "ollama_local"

    def __init__(self, host: str = "http://localhost:11434", model: str = "qwen2.5-coder:7b", timeout: int = 120) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        try:
            import httpx  # type: ignore
        except ImportError:
            return self._refusal(planning_brief, "model_error", "httpx not installed")
        prompt = system_prompt + "\n\nPlanningBrief:\n" + json.dumps(planning_brief)
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": {"temperature": 0.1}}
        try:
            r = httpx.post(f"{self.host}/api/generate", json=payload, timeout=self.timeout)
            r.raise_for_status()
            text = r.json().get("response", "")
        except Exception as exc:
            return self._refusal(planning_brief, "model_error", str(exc))
        try:
            parsed = self._extract_json(text)
        except ValueError:
            return self._refusal(planning_brief, "format_failure", "non_json_response")
        if "refusal_type" in parsed:
            return parsed
        if not parsed.get("actions"):
            return self._refusal(planning_brief, "empty_plan", "no_actions_returned")
        parsed.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:12]}")
        parsed.setdefault("task_id", planning_brief.get("task_id", "unknown"))
        parsed.setdefault("planner_id", self.planner_id)
        parsed.setdefault("planning_mode", planning_brief.get("planning_mode", "direct"))
        parsed.setdefault("used_refusal_recovery", False)
        return parsed

    def _refusal(self, brief: dict, refusal_type: str, message: str) -> dict:
        return {
            "refusal_id": f"refusal_{uuid.uuid4().hex[:12]}",
            "task_id": brief.get("task_id", "unknown"),
            "planner_id": self.planner_id,
            "refusal_type": refusal_type, "message": message,
            "raw_output_hash": "",
            "recovery_allowed": refusal_type != "universal_hard_safety_refusal",
        }

    @staticmethod
    def _extract_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("no_json_object")
        return json.loads(match.group(0))
