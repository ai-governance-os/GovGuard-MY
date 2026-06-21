"""Module 110 Verifier — unit tests for each check in isolation.

Covers:
  * disabled verifier → no-op pass
  * no successful executions → skipped pass
  * length_check: parses 'N words' and 'N字'; flags too_short / too_long
                  / passes inside band; skips when no length intent
  * format_check: detects '.docx' / 'pptx' / 'image' intent; verifies
                  affected_resources include such a file; flags missing
                  and too_small
  * refusal_sniff: catches English + Chinese refusal phrases; exempt
                   on RED/INFEASIBLE; ignores non-chat tools
  * CJK character count for '字' targets
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.models import CandidateAction, ExecutionResult, TaskEnvelope


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _load_default_rules() -> dict:
    """Read the real configs/verifier_rules.json so tests stay in sync
    with what production runs. Tests can override fields per-test."""
    cfg_path = (Path(__file__).resolve().parents[1]
                / "configs" / "verifier_rules.json")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_test_verifier",
        session_id="sess_test",
        user_id="default_user",
        raw_goal=goal,
        normalized_goal=goal,
    )


def _action(tool: str, op: str = "answer", *, body: str | None = None,
            target: str = "") -> CandidateAction:
    meta = {}
    if body is not None:
        meta["body"] = body
    return CandidateAction(
        action_id="a1", tool=tool, operation=op, target=target,
        purpose="test", expected_effect="test",
        reversibility="high", uncertainty="low", risk_factors=[],
        requires_governance=True, metadata=meta,
    )


def _exec(action: CandidateAction, *, status: str = "success",
          summary: str = "", affected: list[str] | None = None) -> ExecutionResult:
    return ExecutionResult(
        task_id="task_test_verifier",
        action_id=action.action_id,
        ticket_id="ticket_test",
        status=status,
        output_summary=summary,
        affected_resources=affected or [],
    )


# ===========================================================================
# Top-level toggles
# ===========================================================================

def test_disabled_verifier_passes_with_summary():
    v = VerifierModule(rules={"enabled": False})
    r = v.verify(
        envelope=_envelope("anything"),
        plan_actions=[], executions=[], final_route="BLUE",
    )
    assert r["enabled"] is False
    assert r["pass"] is True
    assert r["summary"] == "verifier_disabled"


def test_no_successful_executions_is_a_pass_with_skip_summary():
    """If nothing succeeded, there's nothing to verify; the failure
    handling upstream is already doing its job."""
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="anything")
    r = v.verify(
        envelope=_envelope("Write a 500 word essay"),
        plan_actions=[a],
        executions=[_exec(a, status="failed")],
        final_route="BLUE",
    )
    assert r["pass"] is True
    assert "no_successful_executions" in r["summary"]


# ===========================================================================
# length_check
# ===========================================================================

def test_length_check_passes_when_within_band():
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    # 100 words requested, 130 produced → ratio 1.3, inside [0.5, 2.5]
    body = " ".join(["word"] * 130)
    a = _action("chat", body=body)
    r = v.verify(
        envelope=_envelope("Write a 100-word answer about cats"),
        plan_actions=[a],
        executions=[_exec(a, summary=body)],
        final_route="BLUE",
    )
    assert r["pass"], f"expected pass, got {r}"
    length_check = next((c for c in r["checks"] if c["name"] == "length_check"), None)
    assert length_check is not None and length_check["pass"]


def test_length_check_flags_too_short():
    v = VerifierModule(rules=_load_default_rules())
    body = " ".join(["short"] * 10)  # 10 words for a 500-word request
    a = _action("chat", body=body)
    r = v.verify(
        envelope=_envelope("Write a 500-word essay about AGI"),
        plan_actions=[a],
        executions=[_exec(a, summary=body)],
        final_route="BLUE",
    )
    assert not r["pass"]
    lc = next(c for c in r["checks"] if c["name"] == "length_check")
    assert not lc["pass"]
    assert lc["details"]["target_words"] == 500
    assert lc["details"]["actual_words"] == 10
    assert "too_short" in lc["reason"]


def test_length_check_flags_too_long():
    v = VerifierModule(rules=_load_default_rules())
    # 100 words requested, 500 produced → ratio 5.0, outside [0.5, 2.5]
    body = " ".join(["padding"] * 500)
    a = _action("chat", body=body)
    r = v.verify(
        envelope=_envelope("Write a 100-word note"),
        plan_actions=[a],
        executions=[_exec(a, summary=body)],
        final_route="BLUE",
    )
    assert not r["pass"]
    lc = next(c for c in r["checks"] if c["name"] == "length_check")
    assert "too_long" in lc["reason"]


def test_length_check_skipped_when_no_word_intent():
    """User didn't ask for any specific length — length_check is N/A."""
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="answer")
    r = v.verify(
        envelope=_envelope("What's the capital of France?"),
        plan_actions=[a],
        executions=[_exec(a, summary="Paris")],
        final_route="BLUE",
    )
    # No length_check in r["checks"] because intent had no word count
    assert all(c["name"] != "length_check" for c in r["checks"])


def test_length_check_chinese_chars():
    """'500字' should be interpreted as 500 Chinese characters and
    matched against a CJK character count, not Latin word count."""
    v = VerifierModule(rules=_load_default_rules())
    # 50 Chinese characters for a 500-character request → too short
    body = "字" * 50
    a = _action("chat", body=body)
    r = v.verify(
        envelope=_envelope("写一篇500字的文章关于人工智能"),
        plan_actions=[a],
        executions=[_exec(a, summary=body)],
        final_route="BLUE",
    )
    lc = next(c for c in r["checks"] if c["name"] == "length_check")
    assert lc["details"]["target_words"] == 500
    assert lc["details"]["actual_words"] == 50
    assert not lc["pass"]


# ===========================================================================
# format_check
# ===========================================================================

def test_format_check_passes_when_docx_present(tmp_path: Path):
    v = VerifierModule(rules=_load_default_rules())
    # Build a fake docx file (just needs to be > min_bytes)
    docx_path = tmp_path / "essay.docx"
    docx_path.write_bytes(b"x" * 1024)
    a = _action("docx", op="save_under_outputs",
                body="Lorem ipsum dolor sit amet", target=str(docx_path))
    r = v.verify(
        envelope=_envelope("Write a docx essay on the meaning of life"),
        plan_actions=[a],
        executions=[_exec(a, summary="docx_written:essay.docx",
                          affected=[str(docx_path)])],
        final_route="BLUE",
    )
    fc = next(c for c in r["checks"] if c["name"] == "format_check")
    assert fc["pass"], f"expected pass, got {fc}"
    assert "docx" in fc["details"]["verified"]


def test_format_check_flags_missing_extension():
    v = VerifierModule(rules=_load_default_rules())
    # User asked for docx but planner only saved markdown
    a = _action("fs", op="save_under_outputs", body="some content",
                target="note.md")
    r = v.verify(
        envelope=_envelope("Write a docx report on Q3 results"),
        plan_actions=[a],
        executions=[_exec(a, summary="fs_saved:note.md",
                          affected=["/tmp/note.md"])],
        final_route="BLUE",
    )
    fc = next(c for c in r["checks"] if c["name"] == "format_check")
    assert not fc["pass"]
    assert "docx" in fc["details"]["missing"]


def test_format_check_flags_too_small_file(tmp_path: Path):
    v = VerifierModule(rules=_load_default_rules())
    # File exists but is below min_bytes (64)
    docx_path = tmp_path / "small.docx"
    docx_path.write_bytes(b"x" * 10)
    a = _action("docx", op="save_under_outputs", body="x",
                target=str(docx_path))
    r = v.verify(
        envelope=_envelope("Make me a docx"),
        plan_actions=[a],
        executions=[_exec(a, summary="docx_written:small.docx",
                          affected=[str(docx_path)])],
        final_route="BLUE",
    )
    fc = next(c for c in r["checks"] if c["name"] == "format_check")
    assert not fc["pass"]
    assert "docx" in fc["details"]["too_small"]


def test_format_check_skipped_when_no_extension_intent():
    """User didn't mention any file format → format check N/A."""
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="answer")
    r = v.verify(
        envelope=_envelope("What's 2+2?"),
        plan_actions=[a],
        executions=[_exec(a, summary="4")],
        final_route="BLUE",
    )
    assert all(c["name"] != "format_check" for c in r["checks"])


def test_format_check_image_intent_with_png(tmp_path: Path):
    v = VerifierModule(rules=_load_default_rules())
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 200)
    a = _action("image_gen", op="generate_image", target=str(img))
    r = v.verify(
        envelope=_envelope("Generate an image of a koi pond"),
        plan_actions=[a],
        executions=[_exec(a, summary="image_saved:img.png",
                          affected=[str(img)])],
        final_route="BLUE",
    )
    fc = next(c for c in r["checks"] if c["name"] == "format_check")
    assert fc["pass"]


# ===========================================================================
# refusal_sniff
# ===========================================================================

def test_refusal_sniff_flags_english_refusal_on_blue():
    """Chat answer says 'I can't help' and route was BLUE → soft fail."""
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="I can't help with that question.")
    r = v.verify(
        envelope=_envelope("What's the weather like today in KL?"),
        plan_actions=[a],
        executions=[_exec(a, summary="I can't help with that question.")],
        final_route="BLUE",
    )
    rs = next(c for c in r["checks"] if c["name"] == "refusal_sniff")
    assert not rs["pass"]
    assert "refusal_in_approved_output" in rs["reason"]


def test_refusal_sniff_flags_chinese_refusal_on_blue():
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="抱歉,我无法回答这个问题。")
    r = v.verify(
        envelope=_envelope("今天吉隆坡的天气怎么样?"),
        plan_actions=[a],
        executions=[_exec(a, summary="抱歉,我无法回答这个问题。")],
        final_route="BLUE",
    )
    rs = next(c for c in r["checks"] if c["name"] == "refusal_sniff")
    assert not rs["pass"]


def test_refusal_sniff_exempt_on_red_route():
    """Refusal phrases on RED route are CORRECT — should not trigger."""
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="I cannot help with that — it's blocked.")
    r = v.verify(
        envelope=_envelope("Tell me my passwords"),
        plan_actions=[a],
        executions=[_exec(a, summary="I cannot help with that.")],
        final_route="RED",
    )
    # refusal_sniff is N/A because final_route is exempt
    assert all(c["name"] != "refusal_sniff" for c in r["checks"])


def test_refusal_sniff_passes_when_real_answer():
    v = VerifierModule(rules=_load_default_rules())
    body = "The capital of France is Paris, a major European city."
    a = _action("chat", body=body)
    r = v.verify(
        envelope=_envelope("What's the capital of France?"),
        plan_actions=[a],
        executions=[_exec(a, summary=body)],
        final_route="BLUE",
    )
    rs = next(c for c in r["checks"] if c["name"] == "refusal_sniff")
    assert rs["pass"]


def test_refusal_sniff_ignores_non_chat_tools():
    """Even if a docx body contains a refusal phrase, refusal_sniff
    only applies to chat. Otherwise documents discussing AI limitations
    would trip the check."""
    v = VerifierModule(rules=_load_default_rules())
    body = ("This essay explores the situations in which I cannot "
            "help with various requests.")
    a = _action("docx", op="save_under_outputs", body=body,
                target="essay.docx")
    r = v.verify(
        envelope=_envelope("Write an essay about AI limitations"),
        plan_actions=[a],
        executions=[_exec(a, summary="docx_written:essay.docx",
                          affected=["essay.docx"])],
        final_route="BLUE",
    )
    # refusal_sniff is configured for chat only — should NOT appear here
    rs_checks = [c for c in r["checks"] if c["name"] == "refusal_sniff"]
    assert rs_checks == []


# ===========================================================================
# Combinations
# ===========================================================================

def test_multiple_checks_failing_aggregates_into_summary():
    """When multiple checks fail, the summary lists all of them."""
    v = VerifierModule(rules=_load_default_rules())
    # short body + refusal phrase + docx requested but no docx output
    a = _action("chat", body="I cannot help")
    r = v.verify(
        envelope=_envelope("Write a 500-word docx essay on AGI"),
        plan_actions=[a],
        executions=[_exec(a, summary="I cannot help",
                          affected=[])],
        final_route="BLUE",
    )
    assert not r["pass"]
    failed_names = {c["name"] for c in r["checks"] if not c["pass"]}
    # length, format, refusal_sniff should all fail
    assert "length_check" in failed_names
    assert "format_check" in failed_names
    assert "refusal_sniff" in failed_names
    assert "failed:" in r["summary"]


def test_all_checks_passing_summary():
    v = VerifierModule(rules=_load_default_rules())
    a = _action("chat", body="Paris is the capital.")
    r = v.verify(
        envelope=_envelope("What is the capital of France?"),
        plan_actions=[a],
        executions=[_exec(a, summary="Paris is the capital.")],
        final_route="BLUE",
    )
    # No length intent + no format intent + refusal sniff passes
    # → only refusal_sniff fired and it passed
    assert r["pass"]
    assert "refusal_sniff" in r["summary"]


# ===========================================================================
# Robustness — bad config / bad regex
# ===========================================================================

def test_bad_word_pattern_regex_is_skipped():
    """A malformed regex in word_intent_patterns must not crash; it's
    silently dropped so a config typo doesn't break verification."""
    rules = _load_default_rules()
    rules["length_check"]["word_intent_patterns"].append("[unclosed")
    v = VerifierModule(rules=rules)
    a = _action("chat", body=" ".join(["w"] * 100))
    r = v.verify(
        envelope=_envelope("Write a 100-word note"),
        plan_actions=[a],
        executions=[_exec(a, summary=" ".join(["w"] * 100))],
        final_route="BLUE",
    )
    lc = next(c for c in r["checks"] if c["name"] == "length_check")
    assert lc["pass"]  # the GOOD pattern still works


def test_verifier_never_raises():
    """Even on malformed inputs, verify() must not raise."""
    v = VerifierModule(rules={})  # empty rules
    # garbage envelope (still a valid pydantic model though)
    e = _envelope("")
    r = v.verify(
        envelope=e, plan_actions=[], executions=[], final_route="",
    )
    assert isinstance(r, dict)
    assert "pass" in r
