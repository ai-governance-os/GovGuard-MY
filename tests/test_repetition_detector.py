"""Tests for the repetition / template-filler detector in 102B
synthesizer. The detector exists to catch Groq llama-3.3-70b's
'template + variable [N]' failure mode, especially in Chinese where
the model reliably produces:

    最新AI新闻与每日更新: ...等。 [1]
    最新AI新闻与每日更新: ...等。 [2]
    最新AI新闻与每日更新: ...等。 [3]

The detector is heuristic (string ops, no LLM call); false positives
trigger a synthesizer re-write, which is acceptable. False negatives
(missing the repetition) are the real cost, so the tests bias toward
catching even subtle variants.
"""
from __future__ import annotations

import pytest

from teow_agl.modules.module_102b_synthesizer import (
    _looks_like_repetition_filler,
    _normalise_for_repetition_check,
)


# ===========================================================================
# Negative cases — real content should NOT be flagged
# ===========================================================================

def test_empty_string_not_flagged():
    assert _looks_like_repetition_filler("") is False


def test_short_text_not_flagged():
    """A 30-char chat reply can't be 'repetition' even if simple."""
    assert _looks_like_repetition_filler("你是谁?我是 governed AI agent。") is False
    assert _looks_like_repetition_filler("hi") is False


def test_normal_english_essay_not_flagged():
    text = (
        "The transformer architecture revolutionised natural language "
        "processing in 2017. It introduced self-attention as a way to "
        "model dependencies across tokens without recurrence. Earlier "
        "approaches relied on RNNs, which struggled with long sequences. "
        "Today's frontier models all derive from this design, scaled "
        "up by orders of magnitude in parameter count and training data."
    )
    assert _looks_like_repetition_filler(text) is False


def test_normal_chinese_essay_not_flagged():
    text = (
        "Transformer 架构在 2017 年由 Google 提出。它引入 self-attention "
        "机制,让模型可以并行处理整个序列。这种设计取代了 RNN 的循环依赖。"
        "今天主流的大模型都基于这个架构,只是参数规模指数级增长。"
        "训练数据也从早期的几亿 token 扩展到数万亿 token。"
    )
    assert _looks_like_repetition_filler(text) is False


def test_chinese_with_genuine_citations_not_flagged():
    """Real cited summary — each [N] backs a different fact."""
    text = (
        "2026年5月最新AI动态:Anthropic发布了Claude 4.7,新增了反思机制。[1] "
        "OpenAI此前在4月推出GPT-5 nano版本。[2] "
        "Google更新了Gemini的多模态能力。[3] "
        "中国厂商方面,字节跳动开源了豆包系列的小参数版本。[4] "
        "据观察这些更新都强调了agent能力。[5]"
    )
    assert _looks_like_repetition_filler(text) is False


def test_english_with_genuine_citations_not_flagged():
    text = (
        "Anthropic released Claude 4.7 with a reflection mechanism [1]. "
        "OpenAI earlier shipped GPT-5 nano in April [2]. "
        "Google updated Gemini's multimodal capabilities [3]. "
        "On the Chinese side, ByteDance open-sourced the small "
        "Doubao variant [4]. All these moves emphasise agent capability [5]."
    )
    assert _looks_like_repetition_filler(text) is False


# ===========================================================================
# Positive cases — the exact failure pattern we're catching
# ===========================================================================

def test_screenshot_chinese_reproduction():
    """Verbatim repro of the bad output the user reported."""
    bad = (
        "最新AI新闻与每日更新: 人工智能行业资讯和市场趋势、技术发展和政策动向等。"
        "不过一些最新的AI新闻为: [1]。\n"
        "最新AI新闻与每日更新: 人工智能行业资讯和市场趋势、技术发展和政策动向等。"
        "不过一些最新的AI新闻为: [2]。\n"
        "最新AI新闻与每日更新: 人工智能行业资讯和市场趋势、技术发展和政策动向等。"
        "不过一些最新的AI新闻为: [3]。\n"
        "最新AI新闻与每日更新: 人工智能行业资讯和市场趋势、技术发展和政策动向等。"
        "不过一些最新的AI新闻为: [4]。\n"
        "最新AI新闻与每日更新: 人工智能行业资讯和市场趋势、技术发展和政策动向等。"
        "不过一些最新的AI新闻为: [5]。"
    )
    assert _looks_like_repetition_filler(bad) is True


def test_english_template_repetition_caught():
    bad = (
        "The latest AI news includes industry trends. [1]\n"
        "The latest AI news includes industry trends. [2]\n"
        "The latest AI news includes industry trends. [3]\n"
        "The latest AI news includes industry trends. [4]\n"
        "The latest AI news includes industry trends. [5]"
    )
    assert _looks_like_repetition_filler(bad) is True


def test_minor_variation_still_caught():
    """LLM sometimes varies wording slightly between repetitions —
    should still be flagged."""
    bad = (
        "The market is growing rapidly. See source 1. [1]\n"
        "The market is growing rapidly. See source 2. [2]\n"
        "The market is growing rapidly. See source 3. [3]\n"
        "The market is growing rapidly. See source 4. [4]"
    )
    assert _looks_like_repetition_filler(bad) is True


def test_chinese_template_with_parentheses_citations_caught():
    """Chinese sometimes uses 【N】 or (N) instead of [N]."""
    bad = (
        "AI行业蓬勃发展正在改变各个领域。【1】\n"
        "AI行业蓬勃发展正在改变各个领域。【2】\n"
        "AI行业蓬勃发展正在改变各个领域。【3】\n"
        "AI行业蓬勃发展正在改变各个领域。【4】"
    )
    assert _looks_like_repetition_filler(bad) is True


def test_three_repetitions_minimum_to_flag():
    """Only 2 repetitions could be intentional emphasis — should NOT
    flag to avoid false positives on legitimate stylistic choices."""
    only_two = (
        "AI is fascinating. There's much to learn here. New developments "
        "happen weekly across multiple companies and research labs. "
        "The pace is unprecedented historically. "
        "AI is fascinating. We see this in every domain now."
    )
    # 2 repetitions is borderline; let it through to avoid false-flagging
    # legitimate rhetorical doubles.
    assert _looks_like_repetition_filler(only_two) is False


# ===========================================================================
# Normalisation helper unit tests
# ===========================================================================

def test_normalise_strips_citations():
    out = _normalise_for_repetition_check("AI is good. [1]")
    assert "[1]" not in out
    assert "ai is good" in out


def test_normalise_strips_bullets_and_numbering():
    assert _normalise_for_repetition_check("- The point") == "the point"
    assert _normalise_for_repetition_check("* item") == "item"
    assert _normalise_for_repetition_check("1. step one") == "step one"


def test_normalise_collapses_whitespace():
    assert _normalise_for_repetition_check("a   b\tc") == "a b c"


def test_normalise_handles_chinese_punctuation():
    assert "[1]" not in _normalise_for_repetition_check("内容【1】")
    assert "(1)" not in _normalise_for_repetition_check("内容(1)")
