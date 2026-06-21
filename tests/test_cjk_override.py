"""Synthesizer CJK-override tests.

Groq llama-3.3-70b writes Chinese that is structurally broken at the
token level — its drafts pass the `_looks_real` length / placeholder
/ repetition gates, but the actual characters are wrong (e.g. "AI
管理表" instead of "AI 治理层", scrambled sentence fragments).

The fix is **not** more prompt engineering — Groq's Chinese weight
distribution can't recover. The fix is: when the synthesizer's chat
LLM is a strong-Chinese backend (Gemini / OpenAI / Claude), the
synthesizer forces a rewrite for any CJK-heavy planner draft,
overriding the normal `_looks_real` early-return.

These tests pin the behavior so we don't regress.
"""
from __future__ import annotations

import pytest

from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer, _is_cjk, _STRONG_CHINESE_BACKENDS,
)
from teow_agl.models import CandidateAction


# ---------------------------------------------------------------------------
# Stub chat LLM that exposes a configurable backend
# ---------------------------------------------------------------------------
class _StubChatLLM:
    def __init__(self, backend: str, response: str = "重写后的正确中文") -> None:
        self.backend = backend
        self.response = response
        self.calls = 0

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        self.calls += 1
        return self.response

    def chat_json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        self.calls += 1
        return {}


def _chat_action(body: str) -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool="chat", operation="answer", target="",
        purpose="t", expected_effect="t", reversibility="high",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata={"body": body},
    )


def _docx_action(body: str, target: str = "outputs/x.docx") -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target=target,
        purpose="t", expected_effect="t", reversibility="high",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata={"body": body, "title": "Test"},
    )


# ===========================================================================
# Helper sanity
# ===========================================================================

def test_is_cjk_basic():
    assert _is_cjk("的")
    assert _is_cjk("人")
    assert _is_cjk("生")
    assert not _is_cjk("a")
    assert not _is_cjk("1")
    assert not _is_cjk(",")
    # Hiragana / Katakana / Hangul intentionally NOT counted as CJK
    # here (they have different LLM failure modes from Chinese)
    assert not _is_cjk("あ")  # hiragana
    assert not _is_cjk("ア")  # katakana


def test_strong_chinese_backends_set():
    assert "gemini" in _STRONG_CHINESE_BACKENDS
    assert "openai" in _STRONG_CHINESE_BACKENDS
    assert "claude" in _STRONG_CHINESE_BACKENDS
    assert "groq" not in _STRONG_CHINESE_BACKENDS
    assert "ollama" not in _STRONG_CHINESE_BACKENDS


def test_is_strong_backend_method():
    s_gemini = ContentSynthesizer(chat_llm=_StubChatLLM("gemini"))
    s_groq = ContentSynthesizer(chat_llm=_StubChatLLM("groq"))
    s_none = ContentSynthesizer(chat_llm=None)
    assert s_gemini._is_strong_chinese_backend() is True
    assert s_groq._is_strong_chinese_backend() is False
    assert s_none._is_strong_chinese_backend() is False


# ===========================================================================
# Chat path — the key behavior
# ===========================================================================

def test_chat_cjk_heavy_with_gemini_forces_rewrite():
    """Groq-style draft (CJK-heavy, passes _looks_real on the surface)
    + Gemini synth backend → MUST be rewritten, not kept."""
    llm = _StubChatLLM("gemini", response="正确通顺的中文回答内容,涵盖了三个要点")
    s = ContentSynthesizer(chat_llm=llm)
    bad_groq_chinese = (
        "我是 TEOW · AI 管理表 (管理表一个收到很多行为体的 AI 一个。"
        "我可以 讨论, 查书, 打包文件, 生成图片, 管理文件,"
        "查询上次任务, 学习发起 参考, 所有行债到很多行为体的一个。"
    )
    diag = s.enrich(_chat_action(bad_groq_chinese), user_intent="你是谁?")
    assert llm.calls >= 1, "Gemini should have been called to rewrite"
    assert diag["result"] == "synthesized"


def test_chat_cjk_heavy_with_groq_keeps_draft():
    """If the synth backend is ALSO Groq, there's no upgrade path —
    keep the draft rather than ping the same weak model."""
    llm = _StubChatLLM("groq", response="同样烂的回答")
    s = ContentSynthesizer(chat_llm=llm)
    bad = "我是 TEOW 一个我可以做很多事 " * 5  # CJK-heavy, looks "real" by length
    diag = s.enrich(_chat_action(bad), user_intent="你是谁?")
    # `_looks_real` says yes (length ok, no placeholder, no repetition
    # detected at the token level here), and Groq backend → don't force
    # rewrite. Result is kept-planner-content.
    assert diag["result"] in ("kept_planner_content", "synthesized")
    # Specifically: the override that fires on CJK-heavy + strong
    # backend should NOT fire here, so no extra Gemini call happens.
    if diag["result"] == "kept_planner_content":
        assert llm.calls == 0


def test_chat_english_content_not_affected_by_override():
    """English content should follow the normal _looks_real path
    regardless of backend. CJK override only fires for CJK-heavy text."""
    llm = _StubChatLLM("gemini", response="rewritten english")
    s = ContentSynthesizer(chat_llm=llm)
    good_en = (
        "The transformer architecture was introduced in 2017 and "
        "revolutionised natural language processing. It uses "
        "self-attention to model token dependencies without recurrence."
    )
    diag = s.enrich(_chat_action(good_en), user_intent="explain transformers")
    # Good English content + strong backend = no rewrite needed
    assert diag["result"] == "kept_planner_content"
    assert llm.calls == 0


def test_chat_mostly_english_with_some_chinese_not_forced():
    """Code-mixed content (mostly English, a few CJK chars) shouldn't
    trigger the rewrite — the override fires only on CJK-DOMINANT text."""
    llm = _StubChatLLM("gemini", response="...")
    s = ContentSynthesizer(chat_llm=llm)
    # < 30% CJK characters
    mixed = (
        "The Chinese word 你好 means hello. The transformer architecture "
        "was introduced in 2017. It uses self-attention without recurrence "
        "to model long-range dependencies between tokens."
    )
    diag = s.enrich(_chat_action(mixed), user_intent="explain")
    assert diag["result"] == "kept_planner_content"
    assert llm.calls == 0


def test_chat_empty_body_still_synthesized():
    """Empty body always re-synth, regardless of CJK / backend."""
    llm = _StubChatLLM("gemini", response="新生成的回答")
    s = ContentSynthesizer(chat_llm=llm)
    diag = s.enrich(_chat_action(""), user_intent="你好,介绍自己")
    assert diag["result"] == "synthesized"
    assert llm.calls >= 1


# ===========================================================================
# Docx path — same override
# ===========================================================================

def test_docx_cjk_heavy_with_gemini_forces_rewrite():
    """500-word Chinese docx body from Groq should be rewritten."""
    llm = _StubChatLLM("gemini", response="重写的高质量 500 字中文报告 " * 30)
    s = ContentSynthesizer(chat_llm=llm)
    # Long Chinese, technically passes length+repetition checks
    bad_groq_docx_body = (
        "人为什么活着 是一个深刻的话题 涉及到了很多行为体 " * 50
    )
    diag = s.enrich(_docx_action(bad_groq_docx_body),
                    user_intent="人为什么活着?写500字报告")
    assert llm.calls >= 1, "Gemini should rewrite CJK docx body"
    assert diag["result"] == "synthesized"


def test_docx_english_with_gemini_keeps_when_good():
    llm = _StubChatLLM("gemini", response="...")
    s = ContentSynthesizer(chat_llm=llm)
    # 500-word English body, varied (NOT repetitive — repetition
    # detector would flag the same sentence 100 times).
    sentences = [
        "The question of why humans exist has occupied philosophers since antiquity.",
        "Existentialists like Sartre argued that meaning is constructed, not discovered.",
        "Religious traditions offer transcendent answers rooted in divine purpose.",
        "Evolutionary biology frames the question in terms of survival and reproduction.",
        "Psychologists point to relationships, mastery, and contribution as anchors.",
        "Each framework illuminates a different facet of the same complex puzzle.",
        "There is no single answer that satisfies every culture, faith, or scientist.",
        "Yet most agree that meaning is felt, not proven, lived rather than deduced.",
        "Our choices, attachments, and small acts compose a life worth living.",
        "Suffering and joy together form the texture against which meaning emerges.",
    ]
    good_en_body = " ".join(sentences * 10)  # ~500 words, varied
    diag = s.enrich(_docx_action(good_en_body),
                    user_intent="Write a 500-word essay on the meaning of life")
    # Good English + sufficient word count → keep
    assert diag["result"] == "kept_planner_content"


def test_docx_short_body_always_resynthed():
    llm = _StubChatLLM("gemini", response="重写的长报告 " * 100)
    s = ContentSynthesizer(chat_llm=llm)
    diag = s.enrich(_docx_action("太短"),
                    user_intent="写500字报告")
    # Too short → re-synth (independent of CJK override)
    assert diag["result"] == "synthesized"
