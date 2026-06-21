"""S-patch — fixes from the fourth (main-package Chrome) demo run.

13/14 PASS on main. The one remaining failure was C1 (research):
  - planner 413
  - 102R fired BUT my earlier R3 fix never reached it because runtime
    passed `pre.planning_brief` (the BARE brief) to recovery — without
    web_search_context, the `if web_hits` branch never fired.
  - Result: graceful_fallback body (apology), then Q2 appended Sources
    to it → 89 words, below 100-word threshold.

Three additional defenses added:
  S1 — runtime passes the AUGMENTED brief (with web_search_context) to
       recovery so R3 actually runs
  S2 — DuckDuckGo ad redirect URLs (y.js / aclick / msclkid) filtered
       out before they hit the planning brief
  S3 — research_report scenario now forbids graceful_fallback apology
       phrases as a belt-and-braces defense even if S1 somehow misses
"""
from __future__ import annotations

import json

import pytest

from teow_agl.models import (
    CandidateAction, ExecutionResult, PlannerRefusal, TaskEnvelope,
)
from teow_agl.modules.module_102r_refusal_recovery import (
    RefusalRecoveryModule,
)
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.tools.web_search_tool import _looks_like_ad_url


# ===========================================================================
# S1 — augmented brief reaches recovery
#       (this is the bug-fix that makes R3's web-grounded recovery actually
#       fire; tested by integration via the recovery module's contract)
# ===========================================================================

def test_recovery_uses_augmented_brief_web_hits():
    """When the runtime hands recovery a brief CONTAINING
    web_search_context (the augmented brief, not pre.planning_brief),
    the recovery body is web-grounded — NOT a generic apology."""
    templates = json.load(open(
        "configs/refusal_recovery_templates.json", encoding="utf-8"))
    mod = RefusalRecoveryModule(templates)
    refusal = PlannerRefusal(
        refusal_id="r1", task_id="t1", planner_id="groq",
        refusal_type="model_error",
        message="Client error '413 Payload Too Large'",
        raw_output_hash="", recovery_allowed=True,
    )
    augmented_brief = {
        "task_category": "research_report",
        "user_intent": "搜索并总结最新AI治理趋势",
        "web_search_context": [
            {"title": "EU AI Act 2026", "url": "https://example.com/eu-ai",
             "content": "EU AI Act enforcement phase began with quarterly "
                        "audit requirements for high-risk model deployments "
                        "across healthcare, finance, and government."},
            {"title": "NIST AI RMF 1.1", "url": "https://example.com/nist",
             "content": "NIST released v1.1 of its AI Risk Management "
                        "Framework with sector-specific overlays."},
            {"title": "Singapore Model AI Governance",
             "url": "https://example.com/sg",
             "content": "Singapore updated its model governance framework "
                        "to align with EU and US baselines."},
        ],
    }
    plan = mod.recover(refusal=refusal, planning_brief=augmented_brief)
    chat = next((a for a in plan.actions if a.tool == "chat"), None)
    assert chat is not None
    body = chat.metadata.get("body", "")
    assert chat.metadata.get("recovery_kind") == "web_grounded"
    # Body must NOT be the graceful_fallback apology
    assert "抱歉,我这次没能" not in body
    assert "couldn't complete that request" not in body
    # Body MUST hit the 100-word threshold using the verifier's actual
    # word-counting method (which properly handles mixed CJK + Latin).
    from teow_agl.modules.module_110_verifier import VerifierModule
    wc = VerifierModule()._word_count(body)
    assert wc >= 100, f"recovery body only {wc} words: {body!r}"
    # Body MUST cite + list sources
    assert "[1]" in body and "[2]" in body and "[3]" in body
    assert "Sources:" in body


# ===========================================================================
# S2 — DuckDuckGo ad URL filter
# ===========================================================================

@pytest.mark.parametrize("url", [
    "https://duckduckgo.com/y.js?ad_domain=foo.com",
    "https://www.bing.com/aclick?ld=e8X3...",
    "https://example.com/landing?msclkid=abc",
    "https://shop.example.com/ads/123",
    "https://example.com/sponsored/x",
    "https://example.com/?ad=1&foo=bar",
])
def test_ad_urls_are_filtered(url):
    assert _looks_like_ad_url(url) is True


@pytest.mark.parametrize("url", [
    "https://example.com/article/2026/ai-governance",
    "https://nist.gov/ai-rmf",
    "https://eu.europa.eu/ai-act",
    "https://en.wikipedia.org/wiki/AI_governance",
    "https://news.ycombinator.com/item?id=12345",
])
def test_organic_urls_pass_through(url):
    assert _looks_like_ad_url(url) is False


def test_empty_url_treated_as_ad():
    """Empty / None URLs are dropped to avoid silent garbage hits."""
    assert _looks_like_ad_url("") is True
    assert _looks_like_ad_url(None) is True


# ===========================================================================
# S3 — research_report verifier forbids graceful_fallback apology
# ===========================================================================

_RULES = json.load(open(
    "configs/verifier_rules.json", encoding="utf-8"))


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
    env = TaskEnvelope(
        task_id="t1", session_id="s1", user_id="u1",
        raw_goal="搜索并总结", normalized_goal="搜索并总结",
        attachments=[], workspace_roots=[], metadata={},
    )
    return v.verify(envelope=env, plan_actions=[action], executions=[ex],
                     final_route="BLUE", task_category="research_report")


def test_research_verifier_catches_graceful_fallback_apology():
    """The exact failure case from the demo: short apology + Sources
    block appended → previously passed sources/citation checks because
    Q2 added them. Now research_report explicitly forbids the apology
    text even when Sources are present."""
    body = (
        "抱歉,我这次没能完整生成这份回答的内容。可能是网络或服务暂时繁忙。"
        "请稍等片刻再让我重做一次。\n\n"
        "Sources:\n"
        "[1] AI Act — https://example.com/ai\n"
        "[2] NIST — https://example.com/nist\n"
        "[3] Singapore — https://example.com/sg"
    )
    r = _verify_research(body)
    forbid_check = next(
        (c for c in r["checks"]
         if c["name"] == "scenario.research_report.no_graceful_fallback_apology"),
        None)
    assert forbid_check is not None
    assert forbid_check["pass"] is False


def test_research_verifier_passes_on_real_grounded_answer():
    """A genuine web-grounded recovery body must pass (no false positive)."""
    body = (
        "Here is a summary of recent AI governance developments [1] [2] [3]. "
        "The EU AI Act has entered enforcement, requiring quarterly audits "
        "for high-risk model deployments across healthcare, finance, and "
        "government sectors. NIST released version 1.1 of its AI Risk "
        "Management Framework, adding sector-specific overlays that align "
        "with EU baselines [2]. Several Asian jurisdictions, including "
        "Singapore, updated their model governance frameworks to harmonise "
        "with these international standards [3]. Industry analysts note "
        "that the enforcement timeline pressures vendors to implement "
        "documented governance controls before quarter-end deadlines.\n\n"
        "Sources:\n"
        "[1] EU AI Act 2026 — https://example.com/eu-ai\n"
        "[2] NIST AI RMF 1.1 — https://example.com/nist\n"
        "[3] Singapore Model AI Governance — https://example.com/sg"
    )
    r = _verify_research(body)
    research_checks = [c for c in r["checks"]
                        if c["name"].startswith("scenario.research_report.")]
    failed = [c for c in research_checks if not c["pass"]]
    assert not failed, f"expected all research checks to pass, failed: {failed}"


def test_research_verifier_catches_english_fallback_apology():
    """English version of S3 — covers cases where the English fallback
    leaks past the Chinese-specific forbid list."""
    body = (
        "Sorry — I couldn't complete that request this time, most likely "
        "a temporary network or service hiccup. Please wait a moment and "
        "send it again.\n\n"
        "Sources:\n[1] X — https://example.com\n[2] Y — https://example.com/y"
    )
    r = _verify_research(body)
    forbid_check = next(
        (c for c in r["checks"]
         if c["name"] == "scenario.research_report.no_graceful_fallback_apology"),
        None)
    assert forbid_check is not None
    assert forbid_check["pass"] is False
