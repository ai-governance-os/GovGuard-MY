"""T-patch — D2 patent-legal flakiness fix (round 5).

Demo round 5: 13/14 PASS, C1 finally fixed (S-patch worked end-to-end).
Only D2 flake remained: Qwen3 sometimes drops the "Assumptions Made"
section and writes < 200 words. Retry passes — proves it's output
stability, not a routing bug.

T1 — patent docx body goes through structural enforcement before
     reaching the executor. Missing assumptions section → injected.
     Missing disclaimer → injected. Body still < 100 words → swap in
     the full structural fallback. Guarantees the 4 patent verifier
     rules pass even when Qwen is having a bad minute.
"""
from __future__ import annotations

import json

import pytest

from teow_agl.models import (
    CandidateAction, ExecutionResult, TaskEnvelope,
)
from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer,
    _canonical_assumptions_section, _canonical_disclaimer_section,
    _enforce_patent_structure, _full_patent_fallback_body,
    _has_marker, _PATENT_ASSUMPTION_MARKERS, _PATENT_DISCLAIMER_MARKERS,
)
from teow_agl.modules.module_110_verifier import VerifierModule


# ---------------------------------------------------------------------------
# T1 — _enforce_patent_structure
# ---------------------------------------------------------------------------

def test_enforce_injects_missing_assumptions():
    """The D2 demo failure exactly: a short body with no Assumptions
    section. Enforcement must add one."""
    body = ("This Non-Disclosure Agreement is between two startups. "
            "Confidential information will be exchanged.")
    fixed, injected = _enforce_patent_structure(body, "draft an NDA")
    assert "assumptions" in injected
    assert _has_marker(fixed, _PATENT_ASSUMPTION_MARKERS)


def test_enforce_injects_missing_disclaimer():
    body = "Background. The parties enter into an NDA. " * 10  # long enough
    fixed, injected = _enforce_patent_structure(body, "draft an NDA")
    assert "disclaimer" in injected
    assert _has_marker(fixed, _PATENT_DISCLAIMER_MARKERS)


def test_enforce_leaves_complete_body_alone():
    """A well-formed body with both sections already present should
    NOT get duplicate injections."""
    body = (
        "Background. This is a draft NDA.\n\n"
        "Assumptions Made: The parties are competent.\n\n"
        "Disclaimer: This is an AI-generated draft, not legal advice. "
        "Consult a licensed attorney." + " more content " * 30
    )
    fixed, injected = _enforce_patent_structure(body, "draft an NDA")
    # Body already has both → nothing should be injected
    assert "assumptions" not in injected
    assert "disclaimer" not in injected


def test_enforce_falls_back_when_body_too_short_after_injection():
    """Pathological case: LLM returned a tiny stub. Even after injecting
    the assumptions + disclaimer sections, word count is below the 200
    floor. Safety net: swap in the full structural fallback."""
    body = "Hi."  # absurdly short
    fixed, injected = _enforce_patent_structure(body, "draft an NDA")
    assert "full_fallback_below_min_word_count" in injected
    v = VerifierModule()
    assert v._word_count(fixed) >= 200


def test_enforce_handles_chinese_input():
    body = "## 背景\n本草稿是关于一份保密协议。"
    fixed, injected = _enforce_patent_structure(
        body, "起草一份保密协议")
    assert _has_marker(fixed, _PATENT_ASSUMPTION_MARKERS)
    assert _has_marker(fixed, _PATENT_DISCLAIMER_MARKERS)
    # Injected sections should be Chinese (input was Chinese)
    assert "我做的假设" in fixed or "假设" in fixed
    assert "免责声明" in fixed or "免责" in fixed


# ---------------------------------------------------------------------------
# T1 — _full_patent_fallback_body
# ---------------------------------------------------------------------------

def test_full_patent_fallback_passes_all_scenario_rules():
    """The fallback used when chat_llm returns completely empty must
    satisfy all 4 patent_legal_draft scenario verifier rules on its
    own — without any LLM call."""
    rules = json.load(open(
        "configs/verifier_rules.json", encoding="utf-8"))
    v = VerifierModule(rules=rules)

    body = _full_patent_fallback_body("起草一份保密协议")
    action = CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/draft.docx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="medium",
        risk_factors=[], requires_governance=True,
        metadata={"title": "Draft", "body": body},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary="",
        affected_resources=["outputs/draft.docx"],
    )
    env = TaskEnvelope(
        task_id="t1", session_id="s1", user_id="u1",
        raw_goal="起草一份保密协议", normalized_goal="起草一份保密协议",
        attachments=[], workspace_roots=[], metadata={},
    )
    r = v.verify(envelope=env, plan_actions=[action], executions=[ex],
                  final_route="GREEN", task_category="patent_legal_draft")
    failed = [c for c in r["checks"]
              if c["name"].startswith("scenario.patent_legal_draft.")
              and not c["pass"]]
    assert not failed, f"fallback failed: {failed}"


def test_full_patent_fallback_english_branch():
    body = _full_patent_fallback_body("draft an NDA between two startups")
    # English structure markers
    assert "Background" in body
    assert "Key Assumptions" in body or "assumptions" in body.lower()
    assert "Disclaimer" in body
    # No Chinese leakage
    assert not any("一" <= ch <= "鿿" for ch in body)


# ---------------------------------------------------------------------------
# T1 — synthesizer integration: D2 reproduction
# ---------------------------------------------------------------------------

class _D2FlakeChat:
    """Simulates the D2 demo flake: Qwen3 produces a short NDA draft
    missing the Assumptions section. Should be caught + repaired by
    _enforce_patent_structure."""
    backend = "groq"

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        # 109-word draft, no Assumptions section — exactly the D2 demo
        # failure shape.
        return (
            "Background\n"
            "This Non-Disclosure Agreement (the \"Agreement\") is entered "
            "into by and between two startups, each referred to as a "
            "\"Party\" and collectively as the \"Parties\". The Parties "
            "wish to explore a potential business relationship.\n\n"
            "Subject Matter\n"
            "Each Party may disclose confidential information; the "
            "recipient agrees to maintain confidentiality.\n\n"
            "Claims / Key Provisions\n"
            "1. Each Party shall keep confidential information in strict "
            "confidence.\n"
            "2. This Agreement shall remain in effect for two years.\n"
            "3. Upon termination, each Party shall return all "
            "confidential materials."
        )

    def chat_json(self, *args, **kwargs) -> dict:
        return {}


def test_synthesizer_repairs_d2_flake():
    """End-to-end: D2 flake reproduction through the full synthesizer.
    The action's final body must pass all 4 patent verifier rules."""
    synth = ContentSynthesizer(chat_llm=_D2FlakeChat())
    action = CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/draft.docx",
        purpose="generate legal/patent draft",
        expected_effect="docx with sections + disclaimer",
        reversibility="high", uncertainty="medium",
        risk_factors=["legal_content"], requires_governance=True,
        metadata={"title": "NDA", "scenario_hint": "patent_legal_draft"},
    )
    synth.enrich(action, user_intent="draft an NDA between two startups")

    body = action.metadata.get("body", "")
    assert body, "synthesizer produced no body"
    # Must contain both required sections after enforcement
    assert _has_marker(body, _PATENT_ASSUMPTION_MARKERS), \
        f"missing assumptions after enforcement: {body!r}"
    assert _has_marker(body, _PATENT_DISCLAIMER_MARKERS), \
        f"missing disclaimer after enforcement: {body!r}"
    # Word count above the 200 verifier floor
    v = VerifierModule()
    assert v._word_count(body) >= 200, (
        f"body still only {v._word_count(body)} words after enforcement")


def test_synthesizer_uses_full_fallback_when_chat_llm_empty():
    """If chat_llm returns empty for a patent task, we use the full
    structural fallback (NOT the generic docx fallback)."""
    class _EmptyChat:
        backend = "groq"
        def chat(self, *args, **kwargs): return ""
        def chat_json(self, *args, **kwargs): return {}

    synth = ContentSynthesizer(chat_llm=_EmptyChat())
    action = CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/draft.docx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="medium",
        risk_factors=["legal_content"], requires_governance=True,
        metadata={"title": "NDA", "scenario_hint": "patent_legal_draft"},
    )
    synth.enrich(action, user_intent="起草一份保密协议")

    body = action.metadata.get("body", "")
    assert _has_marker(body, _PATENT_ASSUMPTION_MARKERS)
    assert _has_marker(body, _PATENT_DISCLAIMER_MARKERS)
    # Honest about being a skeleton (vs. trying to pretend it's a draft)
    assert "结构骨架" in body or "skeleton" in body.lower() \
        or "未能" in body or "unable to" in body.lower()


def test_non_patent_docx_unaffected_by_enforcement():
    """The enforcement path must ONLY fire for patent_legal_draft —
    a regular docx task should be enriched normally."""
    class _OKChat:
        backend = "groq"
        def chat(self, system, user, max_tokens=1500):
            return "A perfectly normal report body about AI governance. " * 30
        def chat_json(self, *args, **kwargs):
            return {}

    synth = ContentSynthesizer(chat_llm=_OKChat())
    action = CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/report.docx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "Report"},  # NO scenario_hint
    )
    synth.enrich(action, user_intent="write a report on AI")

    body = action.metadata.get("body", "")
    # Should NOT have legal disclaimer / assumptions auto-injected
    assert "Key Assumptions" not in body
    assert "AI-generated draft, not legal advice" not in body
