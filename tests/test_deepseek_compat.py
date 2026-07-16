"""DeepSeek/OpenAI compatibility and provider-isolation tests.

All HTTP is intercepted.  These tests prove the operator's short DeepSeek
startup block targets DeepSeek only, while the same adapter remains compatible
with OpenAI.  No paid API call or real credential is used.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

import server.app as appmod
from teow_agl.adapters import openai_provider as opi
from teow_agl.adapters.chat_llm import ChatLLM


class _Resp:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._body = body
        self.status_code = status
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _chat_body(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    for name in (
        "TEOW_AGL_LIVE_PROVIDER", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
        "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_PLANNER_MODEL",
        "DEEPSEEK_MODEL", "DEEPSEEK_THINKING",
        "OPENAI_EMBED_API_KEY", "OPENAI_EMBED_BASE_URL",
        "TEOW_AGL_PLANNER_MAX_TOKENS", "TEOW_AGL_LIVE_WORKFLOWS",
        "TEOW_AGL_LIVE_SCHOOL_INPUTS", "TEOW_AGL_PLANNER",
        "TEOW_AGL_CHAT_LLM",
    ):
        monkeypatch.delenv(name, raising=False)


def _configure_exact_user_deepseek_startup(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "deepseek-unit-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")


def test_exact_compat_startup_is_identified_as_deepseek(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)

    assert opi.active_chat_provider() == "deepseek"
    assert opi.active_chat_model() == "deepseek-v4-flash"
    assert opi._chat_endpoint() == \
        "https://api.deepseek.com/v1/chat/completions"
    assert opi.chat_api_configured() is True


def test_deepseek_json_call_never_targets_openai(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)
    seen: dict = {}

    def _capture(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return _Resp(_chat_body('{"route": "deepseek"}'))

    with patch("httpx.post", side_effect=_capture):
        out = opi.openai_chat_json("system", "user")

    assert out == {"route": "deepseek"}
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert "api.openai.com" not in seen["url"]
    # Keep the expected value split so the repository's secret scanner never
    # mistakes this deliberately fake unit-test credential for a real token.
    expected_auth = "Bearer " + "deepseek-" + "unit-test-key"
    assert seen["headers"]["Authorization"] == expected_auth
    assert seen["payload"]["model"] == "deepseek-v4-flash"
    # DeepSeek thinking is explicit and cost-controlled by default.
    assert seen["payload"]["thinking"] == {"type": "disabled"}


def test_deepseek_thinking_can_be_enabled_explicitly(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_THINKING", "enabled")
    seen: dict = {}

    def _capture(url, *, headers, json, timeout):
        seen["payload"] = json
        return _Resp(_chat_body("ok"))

    with patch("httpx.post", side_effect=_capture):
        assert opi.openai_chat("system", "user") == "ok"
    assert seen["payload"]["thinking"] == {"type": "enabled"}


def test_lingering_deepseek_thinking_is_not_sent_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-unit-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("DEEPSEEK_THINKING", "enabled")
    seen: dict = {}

    def _capture(url, *, headers, json, timeout):
        seen.update(url=url, payload=json)
        return _Resp(_chat_body("openai"))

    with patch("httpx.post", side_effect=_capture):
        assert opi.openai_chat("system", "user") == "openai"
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert "thinking" not in seen["payload"]


def test_obvious_provider_mismatch_fails_closed_without_http(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_LIVE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "some-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")

    assert opi.chat_api_configured() is False
    with patch("httpx.post", side_effect=AssertionError("HTTP forbidden")):
        assert opi.openai_chat("system", "user") == ""
        assert opi.openai_chat_json("system", "user") == {}


def test_deepseek_planner_uses_same_provider_and_token_cap(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_PLANNER_MAX_TOKENS", "900")
    monkeypatch.setenv("DEEPSEEK_THINKING", "enabled")
    seen: dict = {}
    plan = {
        "planning_mode": "direct",
        "actions": [{"action_id": "a1", "tool": "report"}],
    }

    def _capture(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers, payload=json)
        return _Resp(_chat_body(json_module.dumps(plan)))

    # Keep the local variable name ``json`` in the HTTP signature while still
    # having access to the imported module for response construction.
    json_module = json
    planner = opi.OpenAIPlanner(provider="deepseek")
    with patch("httpx.post", side_effect=_capture):
        out = planner.plan({"task_id": "task_ds"}, "system")

    assert planner.planner_id == "deepseek_deepseek_v4_flash"
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert seen["payload"]["model"] == "deepseek-v4-flash"
    assert seen["payload"]["max_tokens"] == 900
    assert seen["payload"]["thinking"] == {"type": "enabled"}
    assert out["planner_id"] == "deepseek_deepseek_v4_flash"


def test_deepseek_compat_key_is_never_reused_for_openai_embeddings(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)

    assert opi.embedding_api_configured() is False
    with patch("httpx.post", side_effect=AssertionError("HTTP forbidden")):
        assert opi.openai_embed(["school procedure"]) is None


def test_independent_openai_embedding_key_is_supported(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)
    monkeypatch.setenv("OPENAI_EMBED_API_KEY", "embedding-only-key")
    seen: dict = {}

    def _capture(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers)
        return _Resp({"data": [{"embedding": [0.1, 0.2]}]})

    with patch("httpx.post", side_effect=_capture):
        assert opi.openai_embed(["school procedure"]) == [[0.1, 0.2]]
    assert seen["url"] == "https://api.openai.com/v1/embeddings"
    assert seen["headers"]["Authorization"] == "Bearer embedding-only-key"


def test_chatllm_deepseek_backend_uses_shared_isolated_transport(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)
    with patch("httpx.post", return_value=_Resp(_chat_body("deepseek reply"))):
        assert ChatLLM(backend="deepseek").chat("system", "user") == \
            "deepseek reply"


def test_server_config_and_mixed_runtime_disclose_deepseek(monkeypatch):
    from fastapi.testclient import TestClient

    _configure_exact_user_deepseek_startup(monkeypatch)
    monkeypatch.setenv(
        "TEOW_AGL_LIVE_WORKFLOWS", "ad_hoc_school_event_reporting",
    )
    body = TestClient(appmod.app).get("/api/config").json()
    assert body["live_configured"] is True
    assert body["live_provider"] == "deepseek"
    assert body["live_model"] == "deepseek-v4-flash"

    seen: dict = {}

    def _fake_build():
        seen["planner"] = appmod.os.environ.get("TEOW_AGL_PLANNER")
        seen["chat"] = appmod.os.environ.get("TEOW_AGL_CHAT_LLM")
        return object()

    monkeypatch.setattr(appmod, "_build_runtime", _fake_build)
    semantics = {
        "checked": True,
        "source": "school_semantic_llm+boundary_guard",
        "school_domain": True,
        "case_relation": "new_case",
    }
    monkeypatch.setenv("TEOW_AGL_LIVE_SCHOOL_INPUTS", "1")
    _, mode = appmod._make_runtime_for_goal(
        "Prepare a school incident response pack.",
        school_semantics=semantics,
    )
    assert mode == "live"
    assert seen == {"planner": "deepseek", "chat": "deepseek"}


def test_globally_live_deepseek_runtime_is_labelled_live(monkeypatch):
    _configure_exact_user_deepseek_startup(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_PLANNER", "deepseek")
    monkeypatch.setattr(appmod, "_build_runtime", lambda: object())

    _, mode = appmod._make_runtime_for_goal("Prepare a school report.")

    assert mode == "live"
