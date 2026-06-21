"""Module 110 Verifier — Phase 2 L4.6 adapted-skill strict mode tests.

When the runtime flags `used_adapted_skill=True`, verify() runs two extra
sub-checks driven by scenario_checks._skill_adapted_strict_mode:

  * scenario.skill_adapted.strict_length — a higher word-count floor
    (target * min_ratio * extra_min_word_count_pct). Catches thin
    adapted output that scrapes past the baseline length_check.
  * scenario.skill_adapted.target_tool_format_match — REQUIRES that the
    task actually produced an artifact in the tool the skill was adapted
    TO (pptx slides / xlsx data / docx body / matching file extension).

These tests build envelopes/actions/executions directly (no Runtime) and
read the real configs/verifier_rules.json so they stay in sync with prod.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.models import CandidateAction, ExecutionResult, TaskEnvelope


def _load_default_rules() -> dict:
    cfg_path = (Path(__file__).resolve().parents[1]
                / "configs" / "verifier_rules.json")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_strict", session_id="sess", user_id="default_user",
        raw_goal=goal, normalized_goal=goal,
    )


def _action(action_id: str, tool: str, op: str = "save",
            *, metadata: dict | None = None) -> CandidateAction:
    return CandidateAction(
        action_id=action_id, tool=tool, operation=op, target="",
        purpose="test", expected_effect="test",
        reversibility="high", uncertainty="low", risk_factors=[],
        requires_governance=True, metadata=metadata or {},
    )


def _exec(action: CandidateAction, *, status: str = "success",
          summary: str = "", affected: list[str] | None = None
          ) -> ExecutionResult:
    return ExecutionResult(
        task_id="task_strict", action_id=action.action_id,
        ticket_id="ticket", status=status, output_summary=summary,
        affected_resources=affected or [],
    )


def _pptx_action(n_slides: int = 4) -> CandidateAction:
    slides = [{"title": f"Slide {i}", "bullets": ["a", "b", "c"]}
              for i in range(n_slides)]
    return _action("p1", "pptx", "save", metadata={"title": "Deck",
                                                   "slides": slides})


# ===========================================================================
# Gating: strict checks only appear when used_adapted_skill=True
# ===========================================================================

def test_strict_checks_absent_when_not_adapted():
    v = VerifierModule(rules=_load_default_rules())
    a = _action("a1", "chat", "answer")
    r = v.verify(
        envelope=_envelope("make a deck"),
        plan_actions=[a], executions=[_exec(a, summary="hello there")],
        final_route="BLUE", used_adapted_skill=False,
        adapted_target_tool="pptx",
    )
    names = [c["name"] for c in r["checks"]]
    assert not any(n.startswith("scenario.skill_adapted") for n in names)
    assert "adapted_skill_strict_mode" not in r


def test_strict_mode_flag_set_when_adapted():
    v = VerifierModule(rules=_load_default_rules())
    a = _pptx_action()
    r = v.verify(
        envelope=_envelope("make a board deck"),
        plan_actions=[a], executions=[_exec(a)],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="pptx",
    )
    assert r.get("adapted_skill_strict_mode") is True


def test_strict_checks_absent_when_config_disabled():
    rules = _load_default_rules()
    rules["scenario_checks"]["_skill_adapted_strict_mode"]["enabled"] = False
    v = VerifierModule(rules=rules)
    a = _action("a1", "chat", "answer")
    r = v.verify(
        envelope=_envelope("write 100 words"),
        plan_actions=[a], executions=[_exec(a, summary="x")],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="pptx",
    )
    names = [c["name"] for c in r["checks"]]
    assert not any(n.startswith("scenario.skill_adapted") for n in names)


# ===========================================================================
# strict_length
# ===========================================================================

def test_strict_length_fails_what_baseline_passes():
    """Body of 55 words for a 100-word request: baseline floor is
    100*0.5=50 (pass), but the strict floor 100*0.5*1.2=60 fails."""
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    body = " ".join(["word"] * 55)
    a = _action("a1", "docx", "save", metadata={"body": body})
    r = v.verify(
        envelope=_envelope("Write a 100-word summary"),
        plan_actions=[a], executions=[_exec(a, summary=body,
                                            affected=["out.docx"])],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="docx",
    )
    sl = next(c for c in r["checks"]
              if c["name"] == "scenario.skill_adapted.strict_length")
    assert sl["pass"] is False
    assert sl["details"]["strict_floor"] == 60
    # Whole verification fails because of the strict floor
    assert r["pass"] is False


def test_strict_length_passes_with_enough_words():
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    body = " ".join(["word"] * 70)
    a = _action("a1", "docx", "save", metadata={"body": body})
    r = v.verify(
        envelope=_envelope("Write a 100-word summary"),
        plan_actions=[a], executions=[_exec(a, summary=body,
                                            affected=["out.docx"])],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="docx",
    )
    sl = next(c for c in r["checks"]
              if c["name"] == "scenario.skill_adapted.strict_length")
    assert sl["pass"] is True


def test_strict_length_skipped_without_word_intent():
    """No word count in the goal → nothing to scale → no strict_length."""
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    a = _pptx_action()
    r = v.verify(
        envelope=_envelope("Make a board presentation"),
        plan_actions=[a], executions=[_exec(a)],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="pptx",
    )
    names = [c["name"] for c in r["checks"]]
    assert "scenario.skill_adapted.strict_length" not in names


# ===========================================================================
# target_tool_format_match
# ===========================================================================

def test_format_match_fails_when_target_artifact_missing():
    """Adapted to pptx but only a chat reply was produced → fail."""
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    a = _action("a1", "chat", "answer")
    r = v.verify(
        envelope=_envelope("Make a board deck"),
        plan_actions=[a],
        executions=[_exec(a, summary="Here is your deck summary.")],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="pptx",
    )
    fm = next(c for c in r["checks"]
              if c["name"] == "scenario.skill_adapted.target_tool_format_match")
    assert fm["pass"] is False
    assert r["pass"] is False


def test_format_match_passes_with_pptx_slides():
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    a = _pptx_action(5)
    r = v.verify(
        envelope=_envelope("Make a board deck"),
        plan_actions=[a], executions=[_exec(a)],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="pptx",
    )
    fm = next(c for c in r["checks"]
              if c["name"] == "scenario.skill_adapted.target_tool_format_match")
    assert fm["pass"] is True
    assert "5_slides" in fm["details"]["evidence"]


def test_format_match_passes_with_matching_file_extension():
    """A docx target satisfied by an affected .docx file even if the
    producing action's tool name differs (e.g. fs.save_under_outputs)."""
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    a = _action("a1", "fs", "save", metadata={"body": "x" * 50})
    r = v.verify(
        envelope=_envelope("Produce the report"),
        plan_actions=[a],
        executions=[_exec(a, affected=["outputs/report.docx"])],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="docx",
    )
    fm = next(c for c in r["checks"]
              if c["name"] == "scenario.skill_adapted.target_tool_format_match")
    assert fm["pass"] is True
    assert "report.docx" in fm["details"]["evidence"]


def test_format_match_skipped_when_no_target_tool():
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    a = _action("a1", "chat", "answer")
    r = v.verify(
        envelope=_envelope("do the thing"),
        plan_actions=[a], executions=[_exec(a, summary="done")],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="",
    )
    names = [c["name"] for c in r["checks"]]
    assert "scenario.skill_adapted.target_tool_format_match" not in names


def test_format_match_passes_with_xlsx_rows():
    rules = _load_default_rules()
    v = VerifierModule(rules=rules)
    a = _action("a1", "xlsx", "save", metadata={
        "sheets": {"Data": [["h1", "h2"], ["r1", "r2"], ["r3", "r4"]]}})
    r = v.verify(
        envelope=_envelope("Build the spreadsheet"),
        plan_actions=[a], executions=[_exec(a)],
        final_route="BLUE", used_adapted_skill=True,
        adapted_target_tool="xlsx",
    )
    fm = next(c for c in r["checks"]
              if c["name"] == "scenario.skill_adapted.target_tool_format_match")
    assert fm["pass"] is True
