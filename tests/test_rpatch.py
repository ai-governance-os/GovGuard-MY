"""R-patch — lock in the fixes from the third demo run.

Demo round 3 (12/14 PASS) surfaced 3 remaining issues:
  - C1 still 413'd because relevant_skills / relevant_context /
    prior_sessions weren't trimmed
  - B1.2 Chinese pptx fell through to a 1-slide synthesis_failed card
  - C1 recovery body was too short (89 words vs 100 required)

R1 — _trim_skill_hits / _trim_rag_hits / _trim_session_hits
R2 — _parse_plain_text_slides + _structured_fallback_slides
R3 — _web_grounded_recovery_body when planner refused with hits in hand
R4 — agent demo brief F1 spec aligned with Q4 capability_boundary
"""
from __future__ import annotations

import json

import pytest

from teow_agl.models import (
    CandidateAction, ExecutionResult, TaskEnvelope,
)
from teow_agl.modules.module_102b_synthesizer import (
    _parse_plain_text_slides, _structured_fallback_slides,
    ContentSynthesizer,
)
from teow_agl.modules.module_102r_refusal_recovery import (
    _web_grounded_recovery_body, RefusalRecoveryModule,
)
from teow_agl.models import PlannerRefusal
from teow_agl.runtime import (
    _trim_rag_hits, _trim_session_hits, _trim_skill_hits,
)


# ===========================================================================
# R1 — brief growth caps
# ===========================================================================

def test_trim_skill_hits_caps_count_and_body_length():
    fat_skills = [{"skill_id": f"s{i}", "name": f"N{i}",
                    "description": "D" * 500,
                    "body": "B" * 5000, "score": 0.9}
                   for i in range(10)]
    out = _trim_skill_hits(fat_skills)
    assert len(out) == 2  # default max_hits
    for s in out:
        assert len(s["body"]) <= 360  # 350 + ellipsis
        assert len(s["description"]) <= 200


def test_trim_rag_hits_preserves_original_schema():
    """The RAG retriever returns chunk_id/path/text/score keys; tests
    and the planner prompt use these names. Trimming must NOT rename."""
    fat = [{"chunk_id": "c1", "path": "/p/x.md",
            "text": "T" * 5000, "score": 0.8}]
    out = _trim_rag_hits(fat)
    assert out[0]["chunk_id"] == "c1"
    assert out[0]["path"] == "/p/x.md"
    assert "text" in out[0]
    assert len(out[0]["text"]) <= 410


def test_trim_session_hits_keeps_only_useful_fields():
    fat = [{"task_id": "t1", "raw_goal": "g" * 500,
            "summary": "s" * 3000, "outputs": ["x"] * 100,
            "score": 0.7}]
    out = _trim_session_hits(fat)
    assert len(out[0]["raw_goal"]) <= 200
    assert len(out[0]["summary"]) <= 260


def test_total_savings_when_all_three_fire():
    """Worst case: skills, rag, sessions all populated. Trims must
    keep the combined payload well under 5 KB (vs ~35 KB raw)."""
    fat_skills = [{"skill_id": f"s{i}", "name": "N",
                    "description": "D" * 500,
                    "body": "B" * 2000, "score": 0.9}
                   for i in range(5)]
    fat_rag = [{"chunk_id": f"c{i}", "path": "/p",
                "text": "T" * 3000, "score": 0.8} for i in range(5)]
    fat_sessions = [{"task_id": f"t{i}", "raw_goal": "g" * 300,
                      "summary": "s" * 2000, "score": 0.7}
                     for i in range(5)]
    total = (len(json.dumps(_trim_skill_hits(fat_skills)))
             + len(json.dumps(_trim_rag_hits(fat_rag)))
             + len(json.dumps(_trim_session_hits(fat_sessions))))
    assert total < 5000, f"trimmed payload {total} too large"


# ===========================================================================
# R2 — pptx plain-text retry parser + structured fallback
# ===========================================================================

def test_plain_text_parser_handles_qwen_chinese_format():
    text = (
        "TITLE: Q3 销售演示\n"
        "SLIDE 1: 关键指标\n"
        "- 收入同比增长 18%\n"
        "- 毛利率提升至 42%\n"
        "SLIDE 2: 重点产品\n"
        "- A 产品线 +25%\n"
        "- B 产品线持平\n"
    )
    r = _parse_plain_text_slides(text)
    assert r["title"] == "Q3 销售演示"
    assert len(r["slides"]) == 2
    assert r["slides"][0]["title"] == "关键指标"
    assert "收入" in r["slides"][0]["bullets"][0]


def test_plain_text_parser_handles_english_format():
    text = (
        "TITLE: AI Safety Brief\n"
        "SLIDE 1: Background\n"
        "- Recent incidents in healthcare AI\n"
        "- Regulatory pressure increasing\n"
        "SLIDE 2: Recommendations\n"
        "- Adopt audit framework\n"
    )
    r = _parse_plain_text_slides(text)
    assert r["title"] == "AI Safety Brief"
    assert len(r["slides"]) == 2


def test_plain_text_parser_strips_markdown_decoration():
    """Qwen sometimes wraps in **bold**/code fences. Parser must clean."""
    text = (
        "```\n"
        "**TITLE: My Deck**\n"
        "**SLIDE 1: Intro**\n"
        "* point one\n"
        "* point two\n"
        "```\n"
    )
    r = _parse_plain_text_slides(text)
    assert r is not None
    assert "My Deck" in r["title"]
    assert len(r["slides"]) == 1


def test_plain_text_parser_returns_none_on_garbage():
    assert _parse_plain_text_slides("") is None
    assert _parse_plain_text_slides("just some random text") is None
    assert _parse_plain_text_slides("TITLE: nothing else") is None  # no slides


def test_structured_fallback_chinese():
    slides = _structured_fallback_slides("做一份关于 Q3 销售的演示")
    assert len(slides) >= 5
    assert any("Q3" in s["title"] for s in slides)
    # First slide honest about limitation
    first_bullets = " ".join(slides[0]["bullets"])
    assert "未能" in first_bullets or "骨架" in first_bullets


def test_structured_fallback_english():
    slides = _structured_fallback_slides("Q3 sales deck")
    assert len(slides) >= 5
    assert any("Q3 sales" in s["title"] for s in slides)


# ===========================================================================
# R2 wiring: synthesizer uses plain-text retry when chat_json fails
# ===========================================================================

class _JsonFailsPlainOK:
    """Simulates Qwen3 Chinese behavior: JSON mode returns garbage,
    plain text mode produces real slides."""
    backend = "groq"

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return (
            "TITLE: AI 治理报告\n"
            "SLIDE 1: 背景\n"
            "- EU AI Act 进入执行阶段\n"
            "- NIST 框架升级\n"
            "SLIDE 2: 核心问题\n"
            "- 模型审计成为强制要求\n"
            "- 跨境合规复杂化\n"
            "SLIDE 3: 建议\n"
            "- 建立内部治理委员会\n"
            "- 季度审计\n"
        )

    def chat_json(self, system: str, user: str,
                  max_tokens: int = 1500) -> dict:
        return {}  # JSON path always fails


def test_synthesizer_falls_back_to_plain_text_pptx():
    synth = ContentSynthesizer(chat_llm=_JsonFailsPlainOK())
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx", purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T"},
    )
    synth.enrich(action, user_intent="做一份关于 AI 治理的演示")
    slides = action.metadata.get("slides", [])
    assert len(slides) == 3
    assert slides[0]["title"] == "背景"
    assert "EU AI Act" in slides[0]["bullets"][0]


class _AllFails:
    backend = "groq"
    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return ""
    def chat_json(self, system: str, user: str,
                  max_tokens: int = 1500) -> dict:
        return {}


def test_synthesizer_uses_structured_fallback_when_both_fail():
    """When BOTH chat_json and chat plain-text fail, we get the
    structured 5+ slide skeleton, not a 1-slide error card."""
    synth = ContentSynthesizer(chat_llm=_AllFails())
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx", purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T"},
    )
    synth.enrich(action, user_intent="做一份关于 Q3 的演示")
    slides = action.metadata.get("slides", [])
    assert len(slides) >= 5


# ===========================================================================
# R3 — web-grounded recovery (C1 fix)
# ===========================================================================

def test_web_grounded_recovery_body_includes_citations_and_sources():
    hits = [
        {"title": "AI Act enforcement begins",
         "url": "https://example.com/act",
         "content": "The EU AI Act enforcement phase started in 2026 "
                    "with audits required for high-risk deployments."},
        {"title": "NIST AI RMF update",
         "url": "https://example.com/nist",
         "content": "NIST released v1.1 of its AI Risk Management "
                    "Framework with sector-specific overlays."},
    ]
    body = _web_grounded_recovery_body("search AI governance trends", hits)
    # Citations
    assert "[1]" in body
    assert "[2]" in body
    # Sources block
    assert "Sources:" in body
    assert "https://example.com/act" in body
    assert "https://example.com/nist" in body


def test_web_grounded_recovery_body_meets_min_word_count():
    """The research_report verifier requires min_word_count=100. The
    recovery body must clear that bar to not fail verification."""
    hits = [
        {"title": f"Article {i}",
         "url": f"https://example.com/{i}",
         "content": ("This is a substantive snippet about an aspect "
                     "of AI governance that fills a meaningful amount "
                     "of text for a citation. ") * 2}
        for i in range(3)
    ]
    body = _web_grounded_recovery_body("research and write a brief", hits)
    word_count = len(body.split())
    assert word_count >= 100, f"only {word_count} words"


def test_web_grounded_recovery_body_chinese_for_chinese_intent():
    hits = [{"title": "中国 AI 监管", "url": "https://x/1",
             "content": "国务院新发布人工智能管理办法,要求模型备案与定期"
                        "安全评估。该政策影响国内外厂商。"}]
    body = _web_grounded_recovery_body("搜索并总结最新 AI 治理趋势", hits)
    # Contains CJK
    assert any("一" <= ch <= "鿿" for ch in body)
    # Still has citation + sources
    assert "[1]" in body
    assert "Sources:" in body


def test_refusal_recovery_uses_web_grounded_when_hits_present():
    """Full 102R integration: when planning_brief carries
    web_search_context AND planner refused with model_error, the
    recovery body must be the web-grounded answer, not graceful_fallback."""
    templates = json.load(open(
        "configs/refusal_recovery_templates.json", encoding="utf-8"))
    mod = RefusalRecoveryModule(templates)
    refusal = PlannerRefusal(
        refusal_id="r1", task_id="t1", planner_id="groq",
        refusal_type="model_error",
        message="413 Payload Too Large",
        raw_output_hash="", recovery_allowed=True,
    )
    plan = mod.recover(
        refusal=refusal,
        planning_brief={
            "task_category": "unknown",
            "user_intent": "search AI news",
            "web_search_context": [
                {"title": "AI news 1", "url": "https://x/1",
                 "content": "Important AI news from 2026 about model audits."},
                {"title": "AI news 2", "url": "https://x/2",
                 "content": "Another AI development from 2026."},
            ],
        },
    )
    chat_action = next((a for a in plan.actions if a.tool == "chat"), None)
    assert chat_action is not None
    body = chat_action.metadata.get("body", "")
    # Web-grounded: has citations and Sources block
    assert "[1]" in body
    assert "Sources:" in body
    # NOT generic apology
    assert "网络或服务暂时繁忙" not in body
    assert "couldn't complete that request" not in body
    assert chat_action.metadata.get("recovery_kind") == "web_grounded"


def test_refusal_recovery_still_falls_back_when_no_hits():
    """If no web_search_context (e.g. planner refused before any search),
    keep the original graceful_fallback behaviour."""
    templates = json.load(open(
        "configs/refusal_recovery_templates.json", encoding="utf-8"))
    mod = RefusalRecoveryModule(templates)
    refusal = PlannerRefusal(
        refusal_id="r1", task_id="t1", planner_id="groq",
        refusal_type="model_error",
        message="429 Too Many Requests",
        raw_output_hash="", recovery_allowed=True,
    )
    plan = mod.recover(
        refusal=refusal,
        planning_brief={"task_category": "unknown",
                        "user_intent": "say hi"},  # no web hits
    )
    chat_action = next((a for a in plan.actions if a.tool == "chat"), None)
    assert chat_action is not None
    body = chat_action.metadata.get("body", "")
    # Graceful apology, not the web-grounded format
    assert "Sources:" not in body
    assert chat_action.metadata.get("recovery_kind") == "generic_apology"
