"""OpenAI provider unit tests — Phase 2 (L4.1).

We mock `httpx.post` so the tests stay deterministic and don't burn
real OpenAI tokens during CI. The live smoke test that proves the
network path actually works lives in `scripts/verify_openai_phase2.py`
(separate from pytest — runs only when you ask for it).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from teow_agl.adapters import openai_provider as opi


def test_dotenv_fills_missing_values_without_overriding_current_shell(
    tmp_path, monkeypatch,
):
    project = tmp_path / "project"
    adapter_dir = project / "teow_agl" / "adapters"
    adapter_dir.mkdir(parents=True)
    fake_module = adapter_dir / "openai_provider.py"
    fake_module.write_text("# test path\n", encoding="utf-8")
    (project / ".env").write_text(
        "OPENAI_MODEL=stale-file-model\n"
        "OPENAI_API_KEY=file-only-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(opi, "__file__", str(fake_module))
    monkeypatch.setattr(opi, "_DOTENV_LOADED", False)
    monkeypatch.delenv("TEOW_AGL_SKIP_DOTENV", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "current-shell-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    opi._load_dotenv_once()

    assert opi.os.environ["OPENAI_MODEL"] == "current-shell-model"
    assert opi.os.environ["OPENAI_API_KEY"] == "file-only-key"
from teow_agl.adapters.chat_llm import ChatLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _FakeResp:
    """Mimics the slice of httpx.Response we touch."""

    def __init__(self, status_code: int, body: Any,
                 retry_after: str | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        return json.loads(self._body)


def _chat_body(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 5},
        "model": "gpt-4o-mini",
    }


def _embed_body(vectors: list[list[float]]) -> dict:
    return {"data": [{"embedding": v} for v in vectors],
            "model": "text-embedding-3-small"}


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    """Always have a key present so we exercise the http path, not the
    early-return-on-missing-key branch (covered separately)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-for-unit-tests")


# ===========================================================================
# openai_chat — happy path + failures
# ===========================================================================

def test_openai_chat_happy_path():
    with patch("httpx.post", return_value=_FakeResp(200, _chat_body("hi there"))):
        out = opi.openai_chat("sys", "user")
    assert out == "hi there"


def test_openai_chat_missing_key_returns_empty(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # httpx.post should NEVER be called when key is missing — assert via
    # raising side_effect.
    with patch("httpx.post", side_effect=AssertionError("should not be called")):
        out = opi.openai_chat("sys", "user")
    assert out == ""


def test_openai_chat_4xx_returns_empty_no_retry():
    """Non-transient 4xx (e.g. 400 bad request, 401 bad key) → "" and
    we DON'T retry (only 429 / 5xx are retried)."""
    calls = []

    def _track(*a, **kw):
        calls.append(1)
        return _FakeResp(400, {"error": "bad request"})

    with patch("httpx.post", side_effect=_track):
        out = opi.openai_chat("sys", "user")
    assert out == ""
    assert len(calls) == 1  # no retry on 400


def test_openai_chat_429_retries_then_succeeds(monkeypatch):
    calls = []

    def _two_step(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResp(429, {}, retry_after="1")
        return _FakeResp(200, _chat_body("done"))

    # Speed up the retry sleep so the test stays fast.
    monkeypatch.setattr("teow_agl.adapters.openai_provider.time.sleep",
                        lambda *_: None)
    with patch("httpx.post", side_effect=_two_step):
        out = opi.openai_chat("sys", "user")
    assert out == "done"
    assert len(calls) == 2


def test_openai_chat_500_retries(monkeypatch):
    monkeypatch.setattr("teow_agl.adapters.openai_provider.time.sleep",
                        lambda *_: None)
    calls = []

    def _step(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResp(503, {})
        return _FakeResp(200, _chat_body("recovered"))

    with patch("httpx.post", side_effect=_step):
        out = opi.openai_chat("sys", "user")
    assert out == "recovered"
    assert len(calls) == 2


def test_openai_chat_network_error_returns_empty():
    with patch("httpx.post", side_effect=RuntimeError("boom")):
        out = opi.openai_chat("sys", "user")
    assert out == ""


# ===========================================================================
# openai_chat_json — strict json_object mode
# ===========================================================================

def test_openai_chat_json_strict_parse():
    with patch("httpx.post",
               return_value=_FakeResp(200, _chat_body('{"k": 1, "x": "y"}'))):
        obj = opi.openai_chat_json("sys", "user")
    assert obj == {"k": 1, "x": "y"}


def test_openai_chat_json_recovers_from_prose_wrapper():
    """OpenAI's json_object mode is usually clean, but defend against
    a model that still emits prose around the JSON."""
    payload = "Here is your data:\n```\n{\"k\": 2}\n```"
    with patch("httpx.post", return_value=_FakeResp(200, _chat_body(payload))):
        obj = opi.openai_chat_json("sys", "user")
    assert obj == {"k": 2}


def test_openai_chat_json_returns_empty_dict_on_failure():
    with patch("httpx.post", return_value=_FakeResp(401, {})):
        obj = opi.openai_chat_json("sys", "user")
    assert obj == {}


# ===========================================================================
# openai_embed — batch
# ===========================================================================

def test_openai_embed_batch_returns_list_of_vectors():
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    with patch("httpx.post", return_value=_FakeResp(200, _embed_body(vectors))):
        out = opi.openai_embed(["a", "b"])
    assert out == vectors


def test_openai_embed_empty_input_no_http_call():
    """Empty input → return [] without calling the API at all (cheaper)."""
    with patch("httpx.post", side_effect=AssertionError("should not be called")):
        out = opi.openai_embed([])
    assert out == []


def test_openai_embed_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("httpx.post", side_effect=AssertionError("should not be called")):
        out = opi.openai_embed(["text"])
    assert out is None


def test_openai_embed_4xx_returns_none():
    with patch("httpx.post", return_value=_FakeResp(401, {})):
        out = opi.openai_embed(["text"])
    assert out is None


def test_openai_embed_one_convenience():
    vectors = [[0.7, 0.8, 0.9]]
    with patch("httpx.post", return_value=_FakeResp(200, _embed_body(vectors))):
        out = opi.openai_embed_one("solo")
    assert out == [0.7, 0.8, 0.9]


# ===========================================================================
# Model resolution precedence
# ===========================================================================

def test_chat_model_explicit_arg_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "ignored-env")
    monkeypatch.setenv("SKILL_ABSTRACTION_MODEL", "also-ignored")
    assert opi._resolve_chat_model("gpt-4o") == "gpt-4o"


def test_chat_model_env_chain(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("SKILL_ABSTRACTION_MODEL", "from-abstraction-env")
    assert opi._resolve_chat_model() == "from-abstraction-env"


def test_chat_model_default(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("SKILL_ABSTRACTION_MODEL", raising=False)
    assert opi._resolve_chat_model() == "gpt-4o-mini"


def test_embed_model_default(monkeypatch):
    monkeypatch.delenv("OPENAI_EMBED_MODEL", raising=False)
    monkeypatch.delenv("SKILL_EMBEDDING_MODEL", raising=False)
    assert opi._resolve_embed_model() == "text-embedding-3-small"


# ===========================================================================
# ChatLLM "openai" backend dispatch
# ===========================================================================

def test_chatllm_openai_backend_delegates_to_provider():
    with patch("httpx.post", return_value=_FakeResp(200, _chat_body("from openai"))):
        llm = ChatLLM(backend="openai")
        out = llm.chat("sys", "user")
    assert out == "from openai"


def test_chatllm_openai_chat_json_uses_strict_mode():
    with patch("httpx.post",
               return_value=_FakeResp(200, _chat_body('{"strict": true}'))):
        llm = ChatLLM(backend="openai")
        obj = llm.chat_json_openai("sys", "user")
    assert obj == {"strict": True}
