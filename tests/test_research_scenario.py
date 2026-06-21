"""Phase B2 — §7 research_report scenario.

Verifies:
 1. research_report keywords classify correctly (Chinese + English)
    AND similar-shape non-research prompts don't mis-fire.
 2. The research_report category force-triggers _query_needs_web=True
    even when no individual freshness cue is present — the classifier
    already proved the user wants live sources.
 3. Scenario verifier sub-rules fire correctly:
    - has_sources_section: requires "Sources:" / 来源 / 参考资料 …
    - has_citation_marker: requires at least one [1] / [2] / …
    - min_word_count: catches stub answers
    - forbid_placeholders: catches "(source)" / "[citation needed]"
"""
from __future__ import annotations

import json

import pytest

from teow_agl.models import (
    CandidateAction, ExecutionResult, TaskEnvelope,
)
from teow_agl.modules.module_101a_pre_governance import PreGovernanceModule
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.runtime import _query_needs_web


# ---------------------------------------------------------------------------
# 1. Classification
# ---------------------------------------------------------------------------

def _classifier() -> PreGovernanceModule:
    cls = json.load(open(
        "configs/intake_classifier.json", encoding="utf-8"))
    return PreGovernanceModule(
        intake_classifier=cls, hard_safety_cfg={}, learned_policy={})


@pytest.mark.parametrize("goal", [
    "搜索并总结最新AI新闻",
    "搜索后总结一下行业动态",
    "查资料后写一份关于Tesla的报告",
    "帮我调研一下电动车市场",
    "找几个来源，总结成500字",
    "附引用的报告",
    "research and write a 500-word report on AI safety",
    "search and summarize the latest news on quantum computing",
    "find sources for AI governance",
    "look up and summarize Tesla's latest earnings",
])
def test_classifies_as_research_report(goal):
    m = _classifier()
    assert m._classify(goal) == "research_report"


@pytest.mark.parametrize("goal,expected", [
    # Philosophy / general knowledge — must NOT misfire
    ("什么是人生的意义", "unknown"),
    ("explain quantum tunneling", "unknown"),
    # Bare "research" without a research-task phrase
    ("research is interesting", "unknown"),
    # Office doc requests with no search verb
    ("写一份关于AI的报告", "office_doc_generation"),
    ("make a slide deck about Q3 sales", "office_doc_generation"),
    # Identity
    ("你是谁", "identity_capability"),
    # Freshness query without research phrase
    ("比特币最新价格", "unknown"),
])
def test_does_not_misclassify_as_research(goal, expected):
    m = _classifier()
    assert m._classify(goal) == expected


# ---------------------------------------------------------------------------
# 2. Web-search forcing
# ---------------------------------------------------------------------------

def test_research_report_forces_web_search_even_without_freshness_cue():
    """The classifier already matched a research keyword; the runtime
    must force web search regardless of whether the text also contains
    explicit freshness words like 'latest' / '最新'."""
    # Plain English research request, no freshness cue:
    assert _query_needs_web("research AI safety and write a brief",
                             "research_report") is True
    # Chinese research request, no freshness cue:
    assert _query_needs_web("查资料后写一份相关报告",
                             "research_report") is True


def test_non_research_category_still_obeys_freshness_heuristic():
    """A research_report-shaped goal that landed in `unknown` (e.g.
    because the keyword set is incomplete) still goes through the
    normal freshness gate — we don't blanket-search every 'unknown'."""
    assert _query_needs_web("explain how transformers work",
                             "unknown") is False


# ---------------------------------------------------------------------------
# 3. Scenario verifier — sub-rules
# ---------------------------------------------------------------------------

_RULES = json.load(open(
    "configs/verifier_rules.json", encoding="utf-8"))


def _env(goal: str = "research and write") -> TaskEnvelope:
    return TaskEnvelope(
        task_id="t1", session_id="s1", user_id="u1",
        raw_goal=goal, normalized_goal=goal,
        attachments=[], workspace_roots=[], metadata={},
    )


def _verify_research(body: str) -> dict:
    v = VerifierModule(rules=_RULES)
    action = CandidateAction(
        action_id="a1", tool="chat", operation="answer", target="",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"body": body},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary=body, affected_resources=[],
    )
    return v.verify(
        envelope=_env(),
        plan_actions=[action], executions=[ex], final_route="BLUE",
        task_category="research_report",
    )


def _check(report: dict, name: str) -> dict | None:
    for c in report.get("checks", []):
        if c["name"] == name:
            return c
    return None


def test_research_verifier_passes_on_well_cited_answer():
    body = (
        "AI governance regulations have evolved rapidly in 2026 [1]. "
        "Several major jurisdictions now require independent model audits "
        "for systems deployed in regulated sectors such as healthcare and "
        "finance [2]. The EU AI Act has been the most influential framework "
        "internationally, defining tiered risk categories that map onto "
        "concrete pre-deployment obligations. The US has followed with a "
        "patchwork of state-level rules, particularly in California and "
        "Colorado [3]. Industry adoption has accelerated noticeably over "
        "the past twelve months: most large model providers now publish "
        "annual governance reports that document training data sources, "
        "evaluation methodologies, and incident response procedures. "
        "Independent researchers continue to debate whether voluntary "
        "frameworks alone are sufficient, or whether binding statutory "
        "obligations across all deployment contexts are necessary to "
        "manage systemic risks effectively as model capability scales.\n\n"
        "Sources:\n[1] https://example.com/ai-2026\n"
        "[2] https://example.com/audits\n[3] https://example.com/eu-ai-act"
    )
    r = _verify_research(body)
    sources = _check(r, "scenario.research_report.has_sources_section")
    citations = _check(r, "scenario.research_report.has_citation_marker")
    wc = _check(r, "scenario.research_report.min_word_count")
    assert sources and sources["pass"] is True
    assert citations and citations["pass"] is True
    assert wc and wc["pass"] is True


def test_research_verifier_fails_on_missing_sources_section():
    body = (
        "AI governance regulations have evolved rapidly in 2026 [1]. "
        "Several jurisdictions now require model audits [2]. The EU AI "
        "Act has been influential, with the US following with state rules [3]. "
        "Adoption has accelerated; many providers publish governance reports."
    )  # has citation markers but NO "Sources:" block
    r = _verify_research(body)
    sources = _check(r, "scenario.research_report.has_sources_section")
    assert sources and sources["pass"] is False


def test_research_verifier_fails_on_missing_citation_markers():
    body = (
        "AI governance regulations have evolved rapidly in 2026. "
        "Many jurisdictions now require model audits. The EU AI Act has "
        "been the most influential framework worldwide.\n\n"
        "Sources:\nhttps://example.com/ai\nhttps://example.com/audits"
    )  # has Sources block but no [1]/[2] markers
    r = _verify_research(body)
    citations = _check(r, "scenario.research_report.has_citation_marker")
    assert citations and citations["pass"] is False


def test_research_verifier_fails_on_too_short_answer():
    body = "Yes, AI is regulated. [1]\nSources:\n[1] https://x"
    r = _verify_research(body)
    wc = _check(r, "scenario.research_report.min_word_count")
    assert wc and wc["pass"] is False


def test_research_verifier_catches_citation_placeholder():
    body = (
        "AI governance covers multiple areas (citation). Many countries "
        "have rules (source). The EU AI Act is influential.\n\n"
        "Sources:\n[1] https://example.com"
    )
    r = _verify_research(body)
    ph = _check(r, "scenario.research_report.no_placeholder")
    assert ph and ph["pass"] is False
    hits = [h.lower() for h in ph["details"]["hits"]]
    assert "(source)" in hits or "(citation)" in hits


def test_research_verifier_accepts_chinese_sources_label():
    body = (
        "人工智能治理在 2026 年迅速演进 [1]。多个司法管辖区已要求模型审计 [2]。"
        "欧盟 AI 法案是最具影响力的框架。许多大型模型提供方现在会发布治理报告。\n\n"
        "参考资料:\n[1] https://example.com/ai\n[2] https://example.com/audits"
    )
    r = _verify_research(body)
    sources = _check(r, "scenario.research_report.has_sources_section")
    assert sources and sources["pass"] is True


def test_research_verifier_does_not_run_on_other_categories():
    """The research rules must not pollute unrelated categories."""
    v = VerifierModule(rules=_RULES)
    action = CandidateAction(
        action_id="a1", tool="chat", operation="answer", target="",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"body": "short chat reply"},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary="short chat reply",
        affected_resources=[],
    )
    r = v.verify(
        envelope=_env(),
        plan_actions=[action], executions=[ex], final_route="BLUE",
        task_category="identity_capability",  # NOT research
    )
    research_checks = [c for c in r["checks"]
                        if c["name"].startswith("scenario.research_report.")]
    assert research_checks == []
