"""Phase A+B patch — post-demo bug-fix regression tests.

Locks in the four fixes that came out of the live agent demo run:
  P0.1 — greeting "嗨" no longer triggers the full identity intro
  P0.2 — synthesizer writes an honest fallback when chat_llm is empty
  P1   — Groq 429 retry-with-backoff
  P2   — image extension picked from magic bytes, not hardcoded .png
"""
from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer, _fallback_body, _text_has_cjk,
)
from teow_agl.runtime import (
    _greeting_answer, _identity_answer,
    _is_greeting_only, _is_identity_question, _is_identity_or_chitchat,
)
from teow_agl.tools.image_tool import _ext_for_bytes


# ===========================================================================
# P0.1 — greeting vs identity
# ===========================================================================

@pytest.mark.parametrize("text", ["嗨", "你好", "hi", "hello", "hi there",
                                    "good morning", "早安"])
def test_greeting_only_recognised(text):
    assert _is_greeting_only(text) is True
    assert _is_identity_question(text) is False


@pytest.mark.parametrize("text", ["你是谁?", "who are you", "你能做什么",
                                    "introduce yourself"])
def test_identity_question_recognised(text):
    assert _is_identity_question(text) is True
    assert _is_greeting_only(text) is False


def test_greeting_answer_is_short_chinese_for_chinese_input():
    body = _greeting_answer("嗨")
    assert "TEOW-AGL" not in body, \
        "greeting should NOT contain full self-introduction"
    # Should be short — under 50 chars
    assert len(body) < 50
    assert "你好" in body


def test_greeting_answer_is_short_english_for_english_input():
    body = _greeting_answer("hi")
    assert "TEOW-AGL" not in body
    assert len(body) < 60
    assert "Hi" in body or "Hello" in body


def test_identity_answer_still_includes_teow_agl():
    """Regression guard: the identity-direct path's full intro stays."""
    body = _identity_answer("你是谁?")
    assert "TEOW-AGL" in body
    assert "治理" in body or "governed" in body.lower()


def test_compat_wrapper_still_returns_true_for_both():
    """The combined helper is still used by the web-search heuristic."""
    assert _is_identity_or_chitchat("嗨") is True
    assert _is_identity_or_chitchat("你是谁?") is True
    assert _is_identity_or_chitchat("什么是人生的意义") is False


# ===========================================================================
# P0.2 — synthesizer honest fallback body
# ===========================================================================

def test_fallback_body_is_chinese_for_chinese_intent():
    body = _fallback_body("写一份关于 AI 的报告", "docx")
    assert _text_has_cjk(body)
    assert "抱歉" in body or "没能" in body
    # Must NOT leak internals
    for word in ["chat_llm", "429", "exception", "traceback", "groq"]:
        assert word not in body.lower()


def test_fallback_body_is_english_for_english_intent():
    body = _fallback_body("write a report on AI", "docx")
    assert not _text_has_cjk(body)
    assert "Sorry" in body or "couldn't" in body.lower()


def test_fallback_body_mentions_the_artifact_kind():
    """The body should make it clear what kind of file failed."""
    assert "Word" in _fallback_body("write a report", "docx")
    assert "slide" in _fallback_body("make a deck", "pptx").lower()
    assert "spreadsheet" in _fallback_body("make a sheet", "xlsx").lower()


def test_fallback_body_includes_original_request_for_context():
    intent = "write a 500-word report on AI safety"
    body = _fallback_body(intent, "docx")
    assert intent in body or intent[:50] in body


# ===========================================================================
# P0.2 — synthesizer wiring: empty LLM reply triggers fallback body
# ===========================================================================

class _AlwaysEmptyChat:
    """Stand-in chat LLM that always returns empty — simulates Qwen3
    using all token budget on reasoning and leaving no answer."""
    backend = "groq"

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return ""

    def chat_json(self, system: str, user: str,
                  max_tokens: int = 1500) -> dict:
        return {}


def _docx_action(body: str = "") -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/x.docx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"body": body, "title": "T"},
    )


def test_synthesizer_writes_fallback_docx_body_on_empty_llm():
    synth = ContentSynthesizer(chat_llm=_AlwaysEmptyChat())
    action = _docx_action(body="")  # empty body → synthesizer must fill
    synth.enrich(action, user_intent="写一份关于 AI 的报告")
    body = action.metadata.get("body", "")
    assert body, "expected fallback body, got empty"
    # Should be the honest fallback, not silently empty
    assert "抱歉" in body or "没能" in body


def test_synthesizer_writes_fallback_pptx_slide_on_empty_llm():
    """R2 — when chat_llm returns empty even after the plain-text
    retry, synthesizer now produces a STRUCTURED multi-slide fallback
    (≥5 slides) instead of a single 'synthesis failed' card. The first
    slide is honest about the limitation; subsequent slides give the
    user a usable skeleton to fill in."""
    synth = ContentSynthesizer(chat_llm=_AlwaysEmptyChat())
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx", purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T"},  # no slides → synth must fill
    )
    synth.enrich(action, user_intent="做一份关于 AI 的演示")
    slides = action.metadata.get("slides", [])
    # R2: now ≥5 slides not 1
    assert len(slides) >= 5
    # First slide is honest about the limitation
    first = slides[0]
    bullets_text = " ".join(first.get("bullets", []))
    assert "未能" in bullets_text or "骨架" in bullets_text \
        or "scaffold" in bullets_text.lower() \
        or "didn't complete" in bullets_text.lower()


def test_synthesizer_writes_fallback_xlsx_sheet_on_empty_llm():
    synth = ContentSynthesizer(chat_llm=_AlwaysEmptyChat())
    action = CandidateAction(
        action_id="a1", tool="xlsx", operation="save_under_outputs",
        target="outputs/x.xlsx", purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T"},  # no sheets → synth must fill
    )
    synth.enrich(action, user_intent="做一份预算电子表格")
    sheets = action.metadata.get("sheets", {})
    assert sheets, "expected at least one fallback sheet"
    # Sheet name should hint at the failure
    assert any("说明" in k or "Notice" in k for k in sheets.keys())


# ===========================================================================
# P1 — 429 retry helper (parse Retry-After)
# ===========================================================================

def test_retry_after_seconds_parsed():
    from teow_agl.adapters.chat_llm import _parse_retry_after
    assert _parse_retry_after("10", 999) == 10.0
    assert _parse_retry_after("0.5", 999) == 1.0  # clamped to min 1
    assert _parse_retry_after("9999", 999) == 60.0  # clamped to max 60


def test_retry_after_invalid_falls_back_to_default():
    from teow_agl.adapters.chat_llm import _parse_retry_after
    assert _parse_retry_after(None, 25) == 25
    assert _parse_retry_after("", 25) == 25
    assert _parse_retry_after("not a number", 25) == 25
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", 25) == 25


# ===========================================================================
# P2 — image extension from magic bytes
# ===========================================================================

@pytest.mark.parametrize("magic,expected", [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", ".jpg"),
    (b"GIF89a", ".gif"),
    (b"RIFF\x00\x00\x00\x00WEBP" + b"x" * 8, ".webp"),
    (b"unknown bytes here", ".png"),  # safe default
    (b"", ".png"),
])
def test_magic_byte_extension_detection(magic, expected):
    assert _ext_for_bytes(magic + b"x" * 100) == expected


def test_save_image_renames_extension_to_match_bytes(tmp_path):
    from teow_agl.tools.image_tool import _save_image_with_correct_extension
    # Caller asked for .png but content is JPEG
    target = tmp_path / "image.png"
    content = b"\xff\xd8\xff\xe0" + b"x" * 200
    actual = _save_image_with_correct_extension(target, content)
    assert actual.suffix == ".jpg"
    assert actual.exists()
    assert actual.read_bytes() == content
    # Original .png filename should NOT have been created
    assert not target.exists()
