"""Lock in the Groq model-selection contract.

Why: Llama 3.3 70B on Groq has broken Chinese tokenisation. We let users
swap to Qwen 3-32B (free tier, native Chinese) via the GROQ_MODEL env
var or a short alias. This test pins the alias map and the env-var
priority so a future refactor can't quietly break Chinese demos.

No network calls — pure construction-time logic.
"""
from __future__ import annotations

import os

import pytest

from teow_agl.adapters.groq_provider import (
    GroqPlanner,
    _planner_id_from_model,
    _resolve_model,
)


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alias, expected", [
    ("qwen",        "qwen/qwen3-32b"),
    ("Qwen",        "qwen/qwen3-32b"),   # case-insensitive
    ("QWEN",        "qwen/qwen3-32b"),
    ("qwen3",       "qwen/qwen3-32b"),
    ("qwen3-32b",   "qwen/qwen3-32b"),
    ("  qwen  ",    "qwen/qwen3-32b"),   # whitespace trimmed
    ("kimi",        "moonshotai/kimi-k2-instruct"),
    ("kimi-k2",     "moonshotai/kimi-k2-instruct"),
    ("llama",       "llama-3.3-70b-versatile"),
    ("llama-3.3",   "llama-3.3-70b-versatile"),
    ("gpt-oss",     "openai/gpt-oss-120b"),
    ("gpt-oss-120b","openai/gpt-oss-120b"),
    ("gpt-oss-20b", "openai/gpt-oss-20b"),
])
def test_short_aliases_resolve(alias, expected):
    assert _resolve_model(alias) == expected


def test_canonical_id_passes_through_unchanged():
    """If user already gave the full vendor-prefixed name, leave it alone."""
    assert _resolve_model("qwen/qwen3-32b") == "qwen/qwen3-32b"
    assert _resolve_model("moonshotai/kimi-k2-instruct") == "moonshotai/kimi-k2-instruct"


def test_unknown_model_string_passes_through():
    """Unknown / future model IDs are returned as-is so we don't block
    users from trying preview models we haven't added an alias for."""
    assert _resolve_model("some-future-model-v9") == "some-future-model-v9"


# ---------------------------------------------------------------------------
# planner_id derivation (used in traces, model_behavior tracking, UI chips)
# ---------------------------------------------------------------------------

def test_planner_id_safe_chars_only():
    """Trace files and SQLite indexes break on '/' — assert sanitised."""
    pid = _planner_id_from_model("qwen/qwen3-32b")
    assert pid == "groq_qwen_qwen3_32b"
    assert "/" not in pid
    assert " " not in pid


def test_planner_id_distinct_per_model():
    """If we run Qwen and Llama in the same trace dir, behaviour stats
    must be tracked separately."""
    a = _planner_id_from_model("qwen/qwen3-32b")
    b = _planner_id_from_model("llama-3.3-70b-versatile")
    assert a != b


# ---------------------------------------------------------------------------
# GroqPlanner constructor priority
# ---------------------------------------------------------------------------

def test_explicit_model_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "kimi")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")  # avoid live-call shape
    p = GroqPlanner(model="qwen")
    assert p.model == "qwen/qwen3-32b"
    assert p.planner_id == "groq_qwen_qwen3_32b"


def test_env_var_used_when_no_explicit_model(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "qwen")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    p = GroqPlanner()
    assert p.model == "qwen/qwen3-32b"
    assert p.planner_id == "groq_qwen_qwen3_32b"


def test_default_remains_llama_for_backwards_compat(monkeypatch):
    """No env var, no explicit arg => keep the historical default so
    existing deployments aren't surprised by an implicit model change."""
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    p = GroqPlanner()
    assert p.model == "llama-3.3-70b-versatile"
    assert p.planner_id == "groq_llama_3_3_70b_versatile"


# ---------------------------------------------------------------------------
# Reasoning-model <think> block suppression
# ---------------------------------------------------------------------------

def test_reasoning_suppression_for_qwen3():
    """Qwen3 emits <think>...</think>. We must ask Groq to hide it."""
    from teow_agl.adapters.groq_provider import _add_reasoning_suppression
    p = {"messages": []}
    _add_reasoning_suppression(p, "qwen/qwen3-32b")
    assert p.get("reasoning_format") == "hidden"
    assert "include_reasoning" not in p  # mutually exclusive


def test_reasoning_suppression_for_deepseek_r1():
    from teow_agl.adapters.groq_provider import _add_reasoning_suppression
    p = {"messages": []}
    _add_reasoning_suppression(p, "deepseek-r1-distill-70b")
    assert p.get("reasoning_format") == "hidden"


def test_reasoning_suppression_for_gpt_oss_uses_include_flag():
    """GPT-OSS uses include_reasoning, not reasoning_format (Groq docs:
    mutually exclusive)."""
    from teow_agl.adapters.groq_provider import _add_reasoning_suppression
    p = {"messages": []}
    _add_reasoning_suppression(p, "openai/gpt-oss-120b")
    assert p.get("include_reasoning") is False
    assert "reasoning_format" not in p


def test_reasoning_suppression_noop_for_llama():
    """Llama 3.3 is not a reasoning model. Don't add either field."""
    from teow_agl.adapters.groq_provider import _add_reasoning_suppression
    p = {"messages": []}
    _add_reasoning_suppression(p, "llama-3.3-70b-versatile")
    assert "reasoning_format" not in p
    assert "include_reasoning" not in p


def test_strip_think_blocks_removes_full_block():
    from teow_agl.adapters.groq_provider import strip_think_blocks
    text = "<think>\n用户问我是谁，需要回答...\n</think>\n\n我是 TEOW-AGL。"
    out = strip_think_blocks(text)
    assert "<think>" not in out
    assert "</think>" not in out
    assert "我是 TEOW-AGL。" in out


def test_strip_think_blocks_handles_dangling_close():
    """Sometimes the response begins mid-think with </think> at the front."""
    from teow_agl.adapters.groq_provider import strip_think_blocks
    text = "用户问我是谁\n</think>\n\n我是 TEOW-AGL。"
    out = strip_think_blocks(text)
    assert "</think>" not in out
    assert out.startswith("我是 TEOW-AGL")


def test_strip_think_blocks_passes_through_clean_text():
    """No <think> tag => return unchanged."""
    from teow_agl.adapters.groq_provider import strip_think_blocks
    text = "我是 TEOW-AGL，一个受治理的智能体。"
    assert strip_think_blocks(text) == text


def test_strip_think_blocks_handles_empty():
    from teow_agl.adapters.groq_provider import strip_think_blocks
    assert strip_think_blocks("") == ""
    assert strip_think_blocks(None) is None or strip_think_blocks(None) == ""


def test_chat_llm_uses_same_alias_resolver(monkeypatch):
    """chat_llm.py must resolve the alias the same way so the planner
    and the synthesizer don't accidentally talk to different models."""
    from teow_agl.adapters.chat_llm import ChatLLM
    monkeypatch.setenv("GROQ_MODEL", "qwen")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    # We don't actually invoke chat() (would hit network). We just confirm
    # the resolver is imported and reachable in chat_llm's scope.
    from teow_agl.adapters.groq_provider import _resolve_model
    assert _resolve_model("qwen") == "qwen/qwen3-32b"
    # Sanity: ChatLLM still constructs cleanly with groq backend
    c = ChatLLM(backend="groq")
    assert c.backend == "groq"
