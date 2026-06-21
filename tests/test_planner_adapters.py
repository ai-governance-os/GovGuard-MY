"""Phase B — OpenAIPlanner / AnthropicPlanner adapter tests (offline).

httpx is monkeypatched; no network, no keys. Covers the PlannerAdapter
contract both new adapters must honour: plan dict with defaults on
success, refusal dict (never an exception) on every failure mode, and
server env-var dispatch.
"""
from __future__ import annotations

import json

import pytest

from teow_agl.adapters.openai_provider import OpenAIPlanner
from teow_agl.adapters.anthropic_provider import (
    AnthropicPlanner, anthropic_chat,
)


_BRIEF = {"task_id": "task_x", "planning_mode": "direct"}

_PLAN_BODY = {
    "planning_mode": "direct",
    "actions": [
        {"action_id": "a1", "tool": "report", "operation": "draft_report",
         "target": "outputs/r.md", "purpose": "t", "expected_effect": "t",
         "reversibility": "high", "uncertainty": "low",
         "risk_factors": [], "requires_governance": True, "metadata": {}}
    ],
}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _openai_resp(content: str) -> _Resp:
    return _Resp({"choices": [{"message": {"content": content}}]})

def _anthropic_resp(text: str) -> _Resp:
    return _Resp({"content": [{"type": "text", "text": text}]})


# ===========================================================================
# OpenAIPlanner
# ===========================================================================

def test_openai_missing_key_refuses_gracefully():
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "model_error"
    assert out["recovery_allowed"] is True
    assert out["task_id"] == "task_x"
    assert p.planner_id == "openai_gpt_4o_mini"


def test_openai_happy_path_sets_defaults(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _openai_resp(json.dumps(_PLAN_BODY)))
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert "refusal_type" not in out
    assert out["actions"]
    assert out["task_id"] == "task_x"
    assert out["planner_id"] == "openai_gpt_4o_mini"
    assert out["plan_id"].startswith("plan_")
    assert out["used_refusal_recovery"] is False


def test_openai_json_wrapped_in_prose_recovered(monkeypatch):
    wrapped = "Here is the plan:\n" + json.dumps(_PLAN_BODY) + "\nDone."
    monkeypatch.setattr("httpx.post", lambda *a, **k: _openai_resp(wrapped))
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert "refusal_type" not in out
    assert out["actions"]


def test_openai_non_json_is_format_failure(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _openai_resp("sorry, no JSON today"))
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "format_failure"


def test_openai_empty_actions_is_empty_plan(monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _openai_resp(json.dumps({"actions": []})))
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "empty_plan"


def test_openai_http_error_is_model_error(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp({}, status=401))
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="bad_key")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "model_error"


def test_openai_refusal_passthrough(monkeypatch):
    refusal = {"refusal_type": "scope_refusal", "message": "no"}
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _openai_resp(json.dumps(refusal)))
    p = OpenAIPlanner(model="gpt-4o-mini", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "scope_refusal"


# ===========================================================================
# AnthropicPlanner
# ===========================================================================

def test_anthropic_alias_resolution():
    assert AnthropicPlanner(model="sonnet", api_key="k").model == "claude-sonnet-4-6"
    assert AnthropicPlanner(model="opus", api_key="k").model == "claude-opus-4-8"
    assert AnthropicPlanner(model="haiku", api_key="k").model == \
        "claude-haiku-4-5-20251001"
    # canonical IDs pass through untouched
    assert AnthropicPlanner(model="claude-sonnet-4-6", api_key="k").model == \
        "claude-sonnet-4-6"


def test_anthropic_default_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_PLANNER_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    p = AnthropicPlanner(api_key="k")
    assert p.model == "claude-sonnet-4-6"
    assert p.planner_id == "anthropic_claude_sonnet_4_6"


def test_anthropic_missing_key_refuses_gracefully():
    p = AnthropicPlanner(model="sonnet", api_key="")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "model_error"
    assert out["recovery_allowed"] is True


def test_anthropic_happy_path_sets_defaults(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _anthropic_resp(json.dumps(_PLAN_BODY)))
    p = AnthropicPlanner(model="sonnet", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert "refusal_type" not in out
    assert out["actions"]
    assert out["planner_id"] == "anthropic_claude_sonnet_4_6"
    assert out["planning_mode"] == "direct"


def test_anthropic_json_wrapped_in_prose_recovered(monkeypatch):
    wrapped = "```json\n" + json.dumps(_PLAN_BODY) + "\n```"
    monkeypatch.setattr("httpx.post", lambda *a, **k: _anthropic_resp(wrapped))
    p = AnthropicPlanner(model="sonnet", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert "refusal_type" not in out
    assert out["actions"]


def test_anthropic_non_json_is_format_failure(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _anthropic_resp("I cannot do that."))
    p = AnthropicPlanner(model="sonnet", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert out["refusal_type"] == "format_failure"


def test_anthropic_multi_block_text_concatenated(monkeypatch):
    half = json.dumps(_PLAN_BODY)
    resp = _Resp({"content": [
        {"type": "text", "text": half[:30]},
        {"type": "text", "text": half[30:]},
    ]})
    monkeypatch.setattr("httpx.post", lambda *a, **k: resp)
    p = AnthropicPlanner(model="sonnet", api_key="test_key")
    out = p.plan(_BRIEF, "system")
    assert "refusal_type" not in out


def test_anthropic_chat_missing_key_returns_empty():
    assert anthropic_chat("sys", "hi", api_key="") == ""


def test_anthropic_chat_happy_path(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _anthropic_resp("hello back"))
    assert anthropic_chat("sys", "hi", api_key="test_key") == "hello back"


# ===========================================================================
# Server env dispatch
# ===========================================================================

def test_planner_from_env_dispatch(monkeypatch):
    from server.app import _planner_from_env
    monkeypatch.setenv("TEOW_AGL_PLANNER", "openai")
    assert type(_planner_from_env()).__name__ == "OpenAIPlanner"
    monkeypatch.setenv("TEOW_AGL_PLANNER", "anthropic")
    assert type(_planner_from_env()).__name__ == "AnthropicPlanner"
    monkeypatch.setenv("TEOW_AGL_PLANNER", "smart_mock")
    assert type(_planner_from_env()).__name__ == "SmartMockPlanner"


def test_chat_llm_anthropic_backend_wired(monkeypatch):
    from teow_agl.adapters.chat_llm import ChatLLM
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _anthropic_resp("pong"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    llm = ChatLLM(backend="anthropic")
    assert llm.chat("sys", "ping") == "pong"
