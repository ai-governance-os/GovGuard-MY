"""Google Gemini planner adapter (free tier on Gemini 2.0 Flash)."""
from __future__ import annotations

import json
import os
import re
import uuid


class GeminiPlanner:
    planner_id = "gemini_2_0_flash"

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None, timeout: int = 60) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.timeout = timeout

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        if not self.api_key:
            return self._refusal(planning_brief, "model_error", "GEMINI_API_KEY missing")
        try:
            import httpx  # type: ignore
        except ImportError:
            return self._refusal(planning_brief, "model_error", "httpx not installed")
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": "PlanningBrief:\n" + json.dumps(planning_brief)}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        try:
            r = httpx.post(endpoint, json=payload, timeout=self.timeout)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
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
