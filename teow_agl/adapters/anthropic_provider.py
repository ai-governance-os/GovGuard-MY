"""Anthropic (Claude) provider — planner adapter + chat helper.

Phase B of SANDBOX_PLAN: lets Module 102 (and the generic ChatLLM)
run on Claude. Same contract as GroqPlanner / OpenAIPlanner:
``plan()`` returns either a plan dict or a refusal dict, never raises.

Model selection precedence:
  1. explicit ``model=`` arg
  2. env ANTHROPIC_PLANNER_MODEL (planner-specific override)
  3. env ANTHROPIC_MODEL
  4. default claude-sonnet-4-6

Short aliases (case-insensitive):
  opus   -> claude-opus-4-8
  sonnet -> claude-sonnet-4-6
  haiku  -> claude-haiku-4-5-20251001

API notes:
  * Endpoint https://api.anthropic.com/v1/messages with ``x-api-key``
    + ``anthropic-version`` headers; the system prompt is a top-level
    ``system`` field, not a message.
  * No ``response_format: json_object`` equivalent — the planner
    system prompt already commands JSON-only output, and
    ``_extract_json`` defensively recovers a JSON object wrapped in
    prose. Same recovery path Groq relies on for non-JSON models.

Failure mode: ``anthropic_chat`` returns "" on any error; the planner
returns a ``model_error`` refusal — matching the failure-isolation
contract of every other adapter.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

_DEFAULT_MODEL = "claude-sonnet-4-6"
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

_MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def _resolve_model(raw: str) -> str:
    return _MODEL_ALIASES.get(raw.strip().lower(), raw)


def _planner_id_from_model(model: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"anthropic_{safe}"


def _api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _parse_retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        n = float(str(value).strip())
    except ValueError:
        return default
    return max(1.0, min(60.0, n))


def _extract_text(data: dict) -> str:
    """Concatenate the text blocks of a /v1/messages response."""
    try:
        blocks = data.get("content") or []
        return "".join(
            str(b.get("text") or "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
    except Exception:
        return ""


def anthropic_chat(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    timeout: int = 60,
    api_key: str | None = None,
) -> str:
    """Plain-text chat via /v1/messages. Returns "" on any failure."""
    key = api_key if api_key is not None else _api_key()
    if not key:
        return ""
    try:
        import httpx  # type: ignore
    except ImportError:
        return ""
    chosen = _resolve_model(model or os.environ.get("ANTHROPIC_MODEL")
                            or _DEFAULT_MODEL)
    payload = {
        "model": chosen,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": _API_VERSION,
        "Content-Type": "application/json",
    }
    try:
        r = httpx.post(_ENDPOINT, headers=headers, json=payload,
                       timeout=timeout)
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(_parse_retry_after(r.headers.get("Retry-After"), 5))
            r = httpx.post(_ENDPOINT, headers=headers, json=payload,
                           timeout=timeout)
        if r.status_code >= 400:
            return ""
        return _extract_text(r.json())
    except Exception:
        return ""


class AnthropicPlanner:
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 timeout: int = 90) -> None:
        chosen = (model
                  or os.environ.get("ANTHROPIC_PLANNER_MODEL")
                  or os.environ.get("ANTHROPIC_MODEL")
                  or _DEFAULT_MODEL)
        self.model = _resolve_model(chosen)
        self.planner_id = _planner_id_from_model(self.model)
        self.api_key = api_key if api_key is not None else _api_key()
        self.timeout = timeout
        self.endpoint = _ENDPOINT

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        if not self.api_key:
            return self._refusal(planning_brief, "model_error",
                                 "ANTHROPIC_API_KEY missing")
        try:
            import httpx  # type: ignore
        except ImportError:
            return self._refusal(planning_brief, "model_error",
                                 "httpx not installed")
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [
                {"role": "user",
                 "content": "PlanningBrief:\n" + json.dumps(planning_brief)},
            ],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _API_VERSION,
            "Content-Type": "application/json",
        }
        try:
            r = httpx.post(self.endpoint, headers=headers, json=payload,
                           timeout=self.timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                time.sleep(_parse_retry_after(r.headers.get("Retry-After"), 5))
                r = httpx.post(self.endpoint, headers=headers, json=payload,
                               timeout=self.timeout)
            r.raise_for_status()
            text = _extract_text(r.json())
        except Exception as exc:
            return self._refusal(planning_brief, "model_error", str(exc))
        try:
            parsed = self._extract_json(text)
        except ValueError:
            return self._refusal(planning_brief, "format_failure",
                                 "non_json_response")
        if "refusal_type" in parsed:
            return parsed
        if not parsed.get("actions"):
            return self._refusal(planning_brief, "empty_plan",
                                 "no_actions_returned")
        parsed.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:12]}")
        parsed.setdefault("task_id", planning_brief.get("task_id", "unknown"))
        parsed.setdefault("planner_id", self.planner_id)
        parsed.setdefault("planning_mode",
                          planning_brief.get("planning_mode", "direct"))
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
