"""Phase A3 — lock in the pre-planner web-search heuristic.

Per the pipeline diagnosis (§7): philosophy / general-knowledge questions
("什么是人生的意义?", "what is photosynthesis?") must NOT trigger a web
search — they are answerable from the planner's training data, and
searching wastes a call + bloats the brief. Only genuine freshness cues
(latest, price, news, year tokens, 最新, 现在 …) should search.
"""
from __future__ import annotations

import pytest

from teow_agl.runtime import _query_needs_web


# --- philosophy / general knowledge / history → NO search -----------------

@pytest.mark.parametrize("q", [
    "什么是人生的意义?",
    "什么是光合作用?",
    "什么是量子力学",
    "what is photosynthesis?",
    "what is the meaning of life",
    "who is shakespeare",
    "who was napoleon",
    "explain how a car engine works",
    "解释一下相对论",
])
def test_general_knowledge_does_not_search(q):
    assert _query_needs_web(q, "unknown") is False


# --- current events / prices / freshness → search -------------------------

@pytest.mark.parametrize("q", [
    "比特币最新价格",
    "latest AI news",
    "what is the price of gold today",
    "今天天气怎么样",
    "2026 年的科技趋势",
    "OpenAI 现任 CEO 是谁",
    "current ceo of microsoft",
    "美元汇率是多少",
    "this week's headlines",
])
def test_freshness_queries_search(q):
    assert _query_needs_web(q, "unknown") is True


# --- identity questions never search --------------------------------------

@pytest.mark.parametrize("q", [
    "你是谁?",
    "你能做什么",
    "who are you",
    "introduce yourself",
])
def test_identity_never_searches(q):
    assert _query_needs_web(q, "identity_capability") is False


# --- env override still works ---------------------------------------------

def test_web_search_always_override(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_ALWAYS", "1")
    # Even a philosophy question searches when the operator forces it.
    assert _query_needs_web("什么是人生的意义?", "unknown") is True


def test_web_search_disabled_override(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "disabled")
    # Even a freshness query does not search when disabled.
    assert _query_needs_web("latest AI news", "unknown") is False
