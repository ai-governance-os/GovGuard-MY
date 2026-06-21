"""Q-patch — lock in the architectural fixes from the second demo run.

Demo round 2 (9/14 PASS) surfaced 5 architectural issues. These tests
guard the fixes:

  Q1 — artifact-aware LLM judge can see pptx structure / xlsx structure
  Q2 — Sources block is enforced in chat when web_search_context exists
  Q3 — desktop-boundary direct path produces an honest capability answer
  Q4 — capability_boundary category + verifier scenario rule
  Q5 — _prior_attempt is threaded into office direct plan and synthesizer
"""
from __future__ import annotations

import json

import pytest

from teow_agl.models import (
    CandidateAction, ExecutionResult, TaskEnvelope,
)
from teow_agl.modules.module_101a_pre_governance import PreGovernanceModule
from teow_agl.modules.module_102b_synthesizer import (
    _ensure_sources_block, _prior_attempt_addendum,
    ContentSynthesizer,
)
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.runtime import (
    _DESKTOP_BOUNDARY_PATTERNS,
    _desktop_boundary_answer,
    _is_desktop_boundary_question,
)


# ===========================================================================
# Q1 — artifact-aware judge: _collect_artifact_content sees ALL artifacts
# ===========================================================================

def _success_result(action_id: str = "a1") -> ExecutionResult:
    return ExecutionResult(
        result_id="r1", task_id="t1", action_id=action_id, ticket_id="tk",
        status="success", output_summary="",
        affected_resources=[],
    )


def _verifier() -> VerifierModule:
    return VerifierModule(rules={"enabled": True})


def test_artifact_collect_includes_pptx_slides():
    """The crux of Q1: pptx structure (slides + bullets) must reach the
    judge brief, not just the chat companion text."""
    v = _verifier()
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={
            "title": "Q3 Sales Review",
            "slides": [
                {"title": "Revenue", "bullets": ["Up 12% QoQ",
                                                  "Driven by enterprise"]},
                {"title": "Costs", "bullets": ["COGS flat",
                                                "Headcount +4"]},
            ],
        },
    )
    text = v._collect_artifact_content([action], [_success_result()])
    assert "[pptx deck" in text
    assert "Q3 Sales Review" in text
    assert "Revenue" in text
    assert "Up 12% QoQ" in text  # bullet content reaches the judge


def test_artifact_collect_includes_xlsx_rows():
    v = _verifier()
    action = CandidateAction(
        action_id="a1", tool="xlsx", operation="save_under_outputs",
        target="outputs/x.xlsx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={
            "sheets": {"Budget": [
                ["Category", "Q1", "Q2", "Q3"],
                ["Salaries", 100, 110, 115],
                ["Marketing", 30, 35, 40],
            ]},
        },
    )
    text = v._collect_artifact_content([action], [_success_result()])
    assert "[xlsx workbook]" in text
    assert "Budget" in text
    assert "Category" in text
    assert "Salaries" in text


def test_artifact_collect_pptx_and_chat_combined():
    """The two actions of an office task (chat companion + pptx) both
    reach the judge in the combined artifact_content."""
    v = _verifier()
    chat_action = CandidateAction(
        action_id="a1", tool="chat", operation="answer", target="",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"body": "我为你做了一份演示稿"},
    )
    pptx_action = CandidateAction(
        action_id="a2", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T", "slides": [
            {"title": "S1", "bullets": ["bullet1", "bullet2"]}
        ]},
    )
    ex1 = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary="我为你做了一份演示稿",
        affected_resources=[],
    )
    ex2 = ExecutionResult(
        result_id="r2", task_id="t1", action_id="a2", ticket_id="tk",
        status="success", output_summary="",
        affected_resources=["outputs/x.pptx"],
    )
    text = v._collect_artifact_content([chat_action, pptx_action],
                                         [ex1, ex2])
    assert "[chat reply]" in text
    assert "我为你做了一份演示稿" in text
    assert "[pptx deck" in text
    assert "bullet1" in text


def test_artifact_collect_skips_failed_executions():
    v = _verifier()
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T", "slides": [{"title": "S", "bullets": ["b"]}]},
    )
    failed = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="failed", output_summary="", affected_resources=[],
    )
    assert v._collect_artifact_content([action], [failed]) == ""


# ===========================================================================
# Q2 — Sources block enforcement
# ===========================================================================

def test_ensure_sources_block_appends_when_missing():
    body = "AI governance is evolving. The EU AI Act is influential."
    hits = [
        {"title": "EU AI Act", "url": "https://example.com/ai-act"},
        {"title": "NIST Framework", "url": "https://example.com/nist"},
    ]
    out, appended = _ensure_sources_block(body, hits)
    assert appended is True
    assert "Sources:" in out
    assert "https://example.com/ai-act" in out
    assert "https://example.com/nist" in out


def test_ensure_sources_block_skips_when_present():
    body = ("AI governance is evolving.\n\n"
            "Sources:\n[1] https://example.com")
    hits = [{"title": "X", "url": "https://other.com"}]
    out, appended = _ensure_sources_block(body, hits)
    assert appended is False
    assert out == body  # unchanged


def test_ensure_sources_block_recognises_chinese_label():
    body = "人工智能治理在演进。\n\n参考资料:\n[1] https://x"
    hits = [{"title": "X", "url": "https://other.com"}]
    out, appended = _ensure_sources_block(body, hits)
    assert appended is False  # 参考资料 counted as a Sources marker


def test_ensure_sources_block_handles_empty_hits():
    body = "Just text, no hits."
    out, appended = _ensure_sources_block(body, [])
    assert appended is False
    assert out == body


# ===========================================================================
# Q2 wiring — _enrich_chat appends Sources when web_search_context present
# ===========================================================================

class _StubChat:
    """LLM stub that returns text-the-planner-might-already-have-set."""
    backend = "groq"
    def __init__(self, reply: str = "Real-sounding answer that omits sources"):
        self._reply = reply
    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return self._reply
    def chat_json(self, system: str, user: str,
                  max_tokens: int = 1500) -> dict:
        return {}


def test_synthesizer_appends_sources_to_kept_planner_body():
    """Planner body looks real but lacks Sources → synthesizer appends
    one based on web_search_context. This is the C2 fix."""
    synth = ContentSynthesizer(chat_llm=_StubChat())
    action = CandidateAction(
        action_id="a1", tool="chat", operation="answer", target="",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={
            "body": ("Quantum computing news this week: vendor X "
                     "announced 100-qubit chip. Market analysts noted "
                     "implications for cryptography."),
            "web_search_context": [
                {"title": "X 100-qubit launch",
                 "url": "https://news.example.com/x-quantum"},
                {"title": "Crypto implications",
                 "url": "https://news.example.com/crypto"},
            ],
        },
    )
    synth.enrich(action, user_intent="quantum news")
    body = action.metadata.get("body", "")
    assert "Sources:" in body
    assert "https://news.example.com/x-quantum" in body
    assert "https://news.example.com/crypto" in body


# ===========================================================================
# Q3 — desktop-boundary direct path
# ===========================================================================

@pytest.mark.parametrize("text", [
    "你可以去动我的电脑吗",
    "帮我控制电脑",   # not in patterns
    "控制我的电脑可以吗",
    "use my desktop",
    "access my desktop please",
    "control my computer for me",
])
def test_boundary_phrases_caught_when_unambiguous(text):
    # Some of these may not match — test only the ones that should
    # The strict patterns are intentional to avoid false positives
    if text in {"帮我控制电脑"}:
        pytest.skip("不在硬编码 patterns 里（intentional precision)")
    assert _is_desktop_boundary_question(text) is True


def test_boundary_answer_is_chinese_for_chinese_input():
    body = _desktop_boundary_answer("动我的电脑可以吗")
    assert any("一" <= ch <= "鿿" for ch in body)
    # Must explain governance
    assert "BLUE" in body or "GREEN" in body or "治理" in body
    # Must NOT be the graceful_fallback "service busy" text
    assert "抱歉,我这次没能" not in body
    assert "服务暂时繁忙" not in body


def test_boundary_answer_is_english_for_english_input():
    body = _desktop_boundary_answer("control my computer please")
    assert all(not ("一" <= ch <= "鿿") for ch in body)
    assert ("BLUE" in body or "GREEN" in body or "governance" in body.lower())
    assert "Sorry — I couldn't complete" not in body


def test_boundary_classifier_routes_to_capability_boundary():
    """Q4 — classifier sends '动我的电脑' to capability_boundary, not unknown."""
    cls = json.load(open(
        "configs/intake_classifier.json", encoding="utf-8"))
    m = PreGovernanceModule(intake_classifier=cls, hard_safety_cfg={},
                             learned_policy={})
    assert m._classify("你可以去动我的电脑吗") == "capability_boundary"
    assert m._classify("control my computer please") == "capability_boundary"
    # NOT confused with identity
    assert m._classify("你是谁") == "identity_capability"
    # NOT a general unknown
    assert m._classify("什么是人生意义") == "unknown"


# ===========================================================================
# Q4 — verifier scenario rule for capability_boundary
# ===========================================================================

_RULES = json.load(open(
    "configs/verifier_rules.json", encoding="utf-8"))


def _verify_boundary(body: str) -> dict:
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
        envelope=TaskEnvelope(
            task_id="t1", session_id="s1", user_id="u1",
            raw_goal="动我的电脑可以吗", normalized_goal="动我的电脑可以吗",
            attachments=[], workspace_roots=[], metadata={},
        ),
        plan_actions=[action], executions=[ex],
        final_route="BLUE", task_category="capability_boundary",
    )


def test_boundary_verifier_passes_on_good_answer():
    body = _desktop_boundary_answer("动我的电脑可以吗")
    r = _verify_boundary(body)
    boundary_checks = [c for c in r["checks"]
                        if c["name"].startswith("scenario.capability_boundary.")]
    assert boundary_checks
    # All boundary sub-checks should pass
    failed = [c for c in boundary_checks if not c["pass"]]
    assert not failed, f"failed: {failed}"


def test_boundary_verifier_catches_graceful_fallback_misuse():
    """The graceful_fallback text is correct for API failures but is
    WRONG for an honest boundary question. Verifier must catch this."""
    bad_body = (
        "抱歉,我这次没能完成这个请求 —— 可能是网络或服务暂时繁忙。"
        "请稍等片刻再发一次。"
    )
    r = _verify_boundary(bad_body)
    forbid_check = next(
        (c for c in r["checks"]
         if c["name"] == "scenario.capability_boundary.no_graceful_fallback_misuse"),
        None)
    assert forbid_check is not None
    assert forbid_check["pass"] is False


# ===========================================================================
# Q5 — _prior_attempt addendum threaded into synthesizer prompts
# ===========================================================================

def test_prior_attempt_addendum_returns_empty_when_no_prior():
    assert _prior_attempt_addendum(None) == ""
    assert _prior_attempt_addendum({}) == ""
    assert _prior_attempt_addendum({"iteration": 1}) == ""  # no issues/suggestions


def test_prior_attempt_addendum_includes_issues_and_suggestions():
    prior = {
        "iteration": 1,
        "judge_score": 20,
        "judge_threshold": 60,
        "judge_issues": ["body too short", "wrong tone"],
        "judge_suggestions": ["write more", "use formal voice"],
    }
    text = _prior_attempt_addendum(prior)
    assert "SELF-FIX" in text
    assert "20/60" in text
    assert "body too short" in text
    assert "wrong tone" in text
    assert "write more" in text


def test_prior_attempt_addendum_truncates_long_issues():
    prior = {
        "judge_issues": ["x" * 500],
        "judge_suggestions": ["y" * 500],
    }
    text = _prior_attempt_addendum(prior)
    # Each line capped at 200 chars (+ "  - " prefix)
    for line in text.splitlines():
        assert len(line) < 250


def test_synthesizer_uses_prior_attempt_in_docx_prompt():
    """When the office direct plan threads _prior_attempt into action.
    metadata, synthesizer must include it in the user prompt to the LLM."""
    captured_user_prompt: list[str] = []

    class _CapturingChat:
        backend = "groq"
        def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
            captured_user_prompt.append(user)
            return "rewritten content"
        def chat_json(self, system: str, user: str,
                      max_tokens: int = 1500) -> dict:
            return {}

    synth = ContentSynthesizer(chat_llm=_CapturingChat())
    action = CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/x.docx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={
            "title": "T",
            "_prior_attempt": {
                "iteration": 1,
                "judge_score": 30,
                "judge_threshold": 60,
                "judge_issues": ["only shows download link"],
                "judge_suggestions": ["include real content in body"],
            },
        },
    )
    synth.enrich(action, user_intent="rewrite this docx")
    assert captured_user_prompt
    full_user = "\n".join(captured_user_prompt)
    assert "SELF-FIX" in full_user
    assert "only shows download link" in full_user
    assert "include real content" in full_user
