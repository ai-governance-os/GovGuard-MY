"""Groq planner adapter — supports any chat-completion model Groq hosts.

Model is chosen in this order:
  1. explicit `model=` arg to constructor
  2. env var GROQ_MODEL
  3. default llama-3.3-70b-versatile (kept for backwards compatibility)

Convenience aliases (case-insensitive) so the user can set short names:
  qwen   -> qwen/qwen3-32b              (strong native Chinese, free tier)
  kimi   -> moonshotai/kimi-k2-instruct  (top Chinese, paid tier)
  llama  -> llama-3.3-70b-versatile      (English-default, free tier)
  gpt-oss-> openai/gpt-oss-120b          (English reasoning, free tier)
"""
from __future__ import annotations

import json
import os
import re
import uuid


# Short aliases the user can set via GROQ_MODEL=... — saves typing the
# vendor-prefixed canonical name and avoids typos on long IDs.
_MODEL_ALIASES = {
    "qwen": "qwen/qwen3-32b",
    "qwen3": "qwen/qwen3-32b",
    "qwen3-32b": "qwen/qwen3-32b",
    "kimi": "moonshotai/kimi-k2-instruct",
    "kimi-k2": "moonshotai/kimi-k2-instruct",
    "llama": "llama-3.3-70b-versatile",
    "llama-3.3": "llama-3.3-70b-versatile",
    "gpt-oss": "openai/gpt-oss-120b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "gpt-oss-20b": "openai/gpt-oss-20b",
}


def _resolve_model(raw: str) -> str:
    """Map a short alias to the canonical Groq model ID, or return raw
    unchanged if it's already canonical."""
    return _MODEL_ALIASES.get(raw.strip().lower(), raw)


def _planner_id_from_model(model: str) -> str:
    """Sanitise model id into a stable planner_id string for traces."""
    # turn "qwen/qwen3-32b" -> "groq_qwen_qwen3_32b"
    safe = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"groq_{safe}"


# Models that emit <think>...</think> reasoning blocks. For these we add
# `reasoning_format: "hidden"` so Groq strips the block server-side before
# the response hits us. Without it, the think block leaks into chat replies
# and corrupts CJK output (the user sees 300 chars of reasoning followed by
# the actual reply, and synthesizer quality checks reject it as garbage).
_REASONING_MODELS_THINK_TAG = (
    "qwen3", "qwen-3",
    "deepseek-r1", "deepseek/r1",
)
# GPT-OSS uses a different field (`include_reasoning: false`) and is
# mutually exclusive with reasoning_format per Groq docs.
_REASONING_MODELS_INCLUDE_FLAG = ("gpt-oss",)


def _add_reasoning_suppression(payload: dict, model: str) -> dict:
    """Mutate `payload` in-place to suppress reasoning tokens for known
    reasoning models. No-op for plain chat models like llama-3.3.

    Returns the same dict for call-chain convenience.
    """
    m = model.lower()
    if any(tag in m for tag in _REASONING_MODELS_THINK_TAG):
        payload["reasoning_format"] = "hidden"
    elif any(tag in m for tag in _REASONING_MODELS_INCLUDE_FLAG):
        payload["include_reasoning"] = False
    return payload


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.IGNORECASE | re.DOTALL)
_DANGLING_THINK_OPEN_RE = re.compile(r"^.*?</think>\s*", flags=re.IGNORECASE | re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Defensive post-processor: remove <think>...</think> blocks even if
    reasoning_format suppression failed (older models, API edge cases, or
    Groq routing variants that don't honour the flag).

    Also handles the case where the response begins with content that
    belongs inside a think block but the opening <think> tag was already
    consumed — we strip everything up to and including </think>.
    """
    if not text or "<think" not in text.lower() and "</think>" not in text.lower():
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # If we still see a dangling </think> (i.e. response started mid-think),
    # drop everything up to and including it.
    if "</think>" in cleaned.lower():
        cleaned = _DANGLING_THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


class GroqPlanner:
    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: int = 60) -> None:
        chosen = model or os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
        self.model = _resolve_model(chosen)
        self.planner_id = _planner_id_from_model(self.model)
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.timeout = timeout
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        if not self.api_key:
            return self._refusal(planning_brief, "model_error", "GROQ_API_KEY missing")
        try:
            import httpx  # type: ignore
        except ImportError:
            return self._refusal(planning_brief, "model_error", "httpx not installed")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "PlanningBrief:\n" + json.dumps(planning_brief)},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        # Reasoning models (Qwen3, DeepSeek-R1, GPT-OSS) emit <think> blocks
        # that would smuggle non-JSON garbage past the json_object guard on
        # some Groq routing paths. Force them off.
        _add_reasoning_suppression(payload, self.model)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            r = httpx.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
            # P1 — Groq free tier hits 429 under bursty load. Sleep + 1
            # retry rides through most rate-limit windows. Without this,
            # several demo tasks back-to-back all degrade into the A4
            # graceful_fallback "sorry, try again" message.
            if r.status_code == 429:
                import time as _time
                retry_after = r.headers.get("Retry-After")
                try:
                    wait = max(1.0, min(60.0, float(retry_after))) \
                        if retry_after else 25.0
                except (TypeError, ValueError):
                    wait = 25.0
                _time.sleep(wait)
                r = httpx.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            return self._refusal(planning_brief, "model_error", str(exc))
        # Defensive: even with reasoning_format=hidden, strip residual
        # <think> blocks before JSON extraction.
        text = strip_think_blocks(text)
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
