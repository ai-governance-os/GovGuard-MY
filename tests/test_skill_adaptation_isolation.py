"""Phase 2 L4.7 — cross-context adaptation wiring + failure-state isolation.

Three things land in this slice and are tested here:

  1. SkillManager.bump_usage_success — a SUCCESS counter distinct from the
     retrieval-hit `usage_count`. Only the runtime's success branch bumps it.
  2. Runtime._detect_target_tool / _maybe_adapt_skill — the runtime decides,
     from the goal text, whether a retrieved skill should be ADAPTED to a new
     output tool, calls the synthesizer, injects the adapted procedure into the
     brief, and flags the run so the verifier applies strict mode. Any non-"ok"
     adaptation status falls back to the raw skill body and emits
     `skill_adaptation_failed`.
  3. Runtime._record_task_outcome — failure isolation (roadmap §3.7): a task
     that used skills credits success_count ONLY when it verified good; a
     failed adapted-skill task withholds the credit AND emits an audit event.

These build objects directly (real SkillManager / SubjectConfidence in tmp,
a real ContentSynthesizer whose _adapt_skill_to_task is monkeypatched) so the
tests never spend a token.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.modules.module_skill_manager import SkillManager
from teow_agl.policies.subject_confidence import SubjectConfidence
from teow_agl.runtime import Runtime, TaskRunResult
from teow_agl.models import (
    CandidateAction, CandidatePlan, ExecutionResult,
    PreGovernanceAssessment, TaskEnvelope,
)

from tests.conftest import make_runtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DOCX_BODY = """# save-q3-docx-report

_Write a quarterly report as a Word document._

<!-- skill_id: skill_abc123def456 | task_id: t1 | created: 2026-05-29 -->

## Principle

Organise findings into a structured narrative and verify the artifact.

## Parameters

```json
{
  "tool": "docx",
  "output_format": "docx",
  "output_language": "en"
}
```

## Procedure

1. Draft an outline of the report's sections.
2. Write each section as full prose paragraphs.
3. Save as a Word document under outputs/.
4. Verify the file exists and is non-empty.
"""


def _skill_manager(tmp_path: Path) -> SkillManager:
    sm = SkillManager(directory=tmp_path / "skills",
                      constraints={"creation_limits":
                                   {"min_chars_per_skill": 10}})
    return sm


def _make_skill(sm: SkillManager, name: str = "save-q3-docx-report") -> str:
    out = sm.create_skill(
        name=name,
        description="Write a quarterly report as a Word document.",
        procedure=("1. Draft an outline.\n2. Write prose paragraphs.\n"
                   "3. Save under outputs/ and verify it is non-empty."),
        parameters={"tool": "docx", "output_format": "docx"},
        principle="Organise findings into a structured narrative.",
    )
    assert out["ok"], out
    return out["skill_id"]


def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_l47", session_id="sess", user_id="default_user",
        raw_goal=goal, normalized_goal=goal,
    )


def _relevant_hit(skill_id: str, body: str = _DOCX_BODY) -> dict:
    return {"skill_id": skill_id, "name": "save-q3-docx-report",
            "description": "Write a quarterly report as a Word document.",
            "tags": [], "score": 0.72, "body": body,
            "char_length": len(body), "usage_count": 1,
            "success_count": 0, "rank_method": "cosine"}


# ===========================================================================
# (1) SkillManager.bump_usage_success
# ===========================================================================

def test_create_skill_starts_with_zero_success_count(tmp_path):
    sm = _skill_manager(tmp_path)
    sid = _make_skill(sm)
    rec = next(s for s in sm.list_skills() if s["skill_id"] == sid)
    assert rec["success_count"] == 0


def test_bump_usage_success_increments_and_persists(tmp_path):
    sm = _skill_manager(tmp_path)
    sid = _make_skill(sm)
    out = sm.bump_usage_success(sid)
    assert out == {"ok": True, "skill_id": sid, "success_count": 1}
    # Persisted: a fresh manager replays the index and sees the bump.
    sm2 = SkillManager(directory=sm.dir)
    rec = next(s for s in sm2.list_skills() if s["skill_id"] == sid)
    assert rec["success_count"] == 1


def test_bump_usage_success_twice(tmp_path):
    sm = _skill_manager(tmp_path)
    sid = _make_skill(sm)
    sm.bump_usage_success(sid)
    out = sm.bump_usage_success(sid)
    assert out["success_count"] == 2


def test_bump_usage_success_invalid_and_missing(tmp_path):
    sm = _skill_manager(tmp_path)
    assert sm.bump_usage_success("not-a-valid-id")["error"] == "invalid_skill_id"
    assert sm.bump_usage_success(
        "skill_000000000000")["error"] == "skill_not_found"


def test_find_relevant_exposes_success_count(tmp_path):
    sm = _skill_manager(tmp_path)
    sid = _make_skill(sm)
    sm.bump_usage_success(sid)
    hits = sm.find_relevant("quarterly report word document", top_k=3)
    assert hits and hits[0]["skill_id"] == sid
    assert hits[0]["success_count"] == 1
    # usage_count (retrieval hits) is independent of success_count.
    assert hits[0]["usage_count"] >= 1


# ===========================================================================
# (2) Runtime._detect_target_tool
# ===========================================================================

@pytest.mark.parametrize("goal,expected", [
    ("Make a Q3 board deck in PowerPoint", "pptx"),
    ("Turn the report into a slide deck", "pptx"),
    ("Build an Excel spreadsheet of the data", "xlsx"),
    ("Write a Word document summarising Q3", "docx"),
    ("做一个 Q3 演示文稿给董事会", "pptx"),
    ("做一个电子表格", "xlsx"),
    ("Summarise this article for me", ""),
    ("", ""),
])
def test_detect_target_tool(goal, expected):
    assert Runtime._detect_target_tool(goal) == expected


def test_detect_target_tool_pptx_wins_over_docx_when_both_present():
    # "slide deck" (pptx cue) should win over "document" wording.
    assert Runtime._detect_target_tool(
        "make a slide deck from the word document") == "pptx"


# ===========================================================================
# (3) Runtime._maybe_adapt_skill
# ===========================================================================

def _runtime(make_runtime_factory, monkeypatch):
    rt = make_runtime_factory()
    events: list[dict] = []

    def _capture(module, event_type, task_id, session_id,
                 input_text="", *, summary="", details=None):
        events.append({"module": module, "event": event_type,
                       "summary": summary, "details": details or {}})

    monkeypatch.setattr(rt, "_emit", _capture)
    return rt, events


def test_maybe_adapt_ok_injects_and_flags(make_runtime_factory, monkeypatch):
    rt, events = _runtime(make_runtime_factory, monkeypatch)
    monkeypatch.setattr(
        rt.synthesizer, "_adapt_skill_to_task",
        lambda skill, **kw: ("1. Break into slides.\n2. Add bullets.", "ok"),
    )
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env,
                           pre_assessment=_pre(env))
    brief: dict = {}
    rt._maybe_adapt_skill(env, [_relevant_hit("skill_abc123def456")],
                          brief, result)
    assert result.used_adapted_skill is True
    assert result.adapted_target_tool == "pptx"
    assert brief["adapted_skill_procedure"].startswith("1. Break into slides")
    assert any(e["event"] == "skill_adapted" for e in events)


def test_maybe_adapt_failure_falls_back_to_raw(make_runtime_factory,
                                                monkeypatch):
    rt, events = _runtime(make_runtime_factory, monkeypatch)
    monkeypatch.setattr(
        rt.synthesizer, "_adapt_skill_to_task",
        lambda skill, **kw: (None, "adaptation_invalid_format"),
    )
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    brief: dict = {}
    rt._maybe_adapt_skill(env, [_relevant_hit("skill_abc123def456")],
                          brief, result)
    assert result.used_adapted_skill is False
    assert "adapted_skill_procedure" not in brief
    fail = [e for e in events if e["event"] == "skill_adaptation_failed"]
    assert fail and fail[0]["details"]["reason"] == "adaptation_invalid_format"
    assert fail[0]["details"]["phase"] == "synthesis"


def test_maybe_adapt_skipped_when_same_tool(make_runtime_factory, monkeypatch):
    rt, events = _runtime(make_runtime_factory, monkeypatch)
    called = {"n": 0}

    def _adapt(skill, **kw):
        called["n"] += 1
        return ("x", "ok")

    monkeypatch.setattr(rt.synthesizer, "_adapt_skill_to_task", _adapt)
    # Goal asks for docx; the skill is already a docx skill → no adaptation.
    env = _envelope("Write a Word document summarising Q3")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    rt._maybe_adapt_skill(env, [_relevant_hit("skill_abc123def456")],
                          {}, result)
    assert called["n"] == 0
    assert result.used_adapted_skill is False
    assert not any(e["event"].startswith("skill_adapt") for e in events)


def test_maybe_adapt_skipped_when_no_target_tool(make_runtime_factory,
                                                 monkeypatch):
    rt, events = _runtime(make_runtime_factory, monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(rt.synthesizer, "_adapt_skill_to_task",
                        lambda skill, **kw: called.__setitem__("n", 1) or ("x", "ok"))
    env = _envelope("Summarise this article for me")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    rt._maybe_adapt_skill(env, [_relevant_hit("skill_abc123def456")],
                          {}, result)
    assert called["n"] == 0
    assert result.used_adapted_skill is False


def test_maybe_adapt_isolates_synthesizer_crash(make_runtime_factory,
                                                 monkeypatch):
    rt, events = _runtime(make_runtime_factory, monkeypatch)

    def _boom(skill, **kw):
        raise RuntimeError("synthesizer exploded")

    monkeypatch.setattr(rt.synthesizer, "_adapt_skill_to_task", _boom)
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    # Must not raise.
    rt._maybe_adapt_skill(env, [_relevant_hit("skill_abc123def456")],
                          {}, result)
    assert result.used_adapted_skill is False
    fail = [e for e in events if e["event"] == "skill_adaptation_failed"]
    assert fail and fail[0]["details"]["reason"].startswith("exception:")


# ===========================================================================
# (4) Runtime._record_task_outcome — failure isolation
# ===========================================================================

def _pre(env: TaskEnvelope, category: str = "office_doc_generation"
         ) -> PreGovernanceAssessment:
    return PreGovernanceAssessment(
        task_id=env.task_id, task_category=category, planning_mode="direct",
    )


def _plan(env: TaskEnvelope) -> CandidatePlan:
    return CandidatePlan(
        task_id=env.task_id, planner_id="mock", planning_mode="direct",
        actions=[CandidateAction(
            action_id="a1", tool="pptx", operation="save", target="",
            purpose="t", expected_effect="t", reversibility="high",
            uncertainty="low", risk_factors=[], requires_governance=True,
        )],
    )


def _wire_outcome_runtime(make_runtime_factory, tmp_path, monkeypatch):
    rt = make_runtime_factory()
    rt.skill_manager = _skill_manager(tmp_path)
    rt.subject_confidence = SubjectConfidence(path=tmp_path / "subj.jsonl")
    events: list[dict] = []
    monkeypatch.setattr(
        rt, "_emit",
        lambda m, e, t, s, i="", *, summary="", details=None:
            events.append({"event": e, "details": details or {}}),
    )
    return rt, events


def test_outcome_success_bumps_skill_success(make_runtime_factory, tmp_path,
                                             monkeypatch):
    rt, events = _wire_outcome_runtime(make_runtime_factory, tmp_path,
                                       monkeypatch)
    sid = _make_skill(rt.skill_manager)
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    result.executions = [ExecutionResult(
        task_id=env.task_id, action_id="a1", ticket_id="tk",
        status="success", output_summary="done")]
    result.used_skill_ids = [sid]
    rt._record_task_outcome(env, _pre(env), _plan(env), result, None)

    rec = next(s for s in rt.skill_manager.list_skills()
               if s["skill_id"] == sid)
    assert rec["success_count"] == 1
    assert any(e["event"] == "skill_usage_success" for e in events)


def test_outcome_failure_withholds_success(make_runtime_factory, tmp_path,
                                           monkeypatch):
    rt, events = _wire_outcome_runtime(make_runtime_factory, tmp_path,
                                       monkeypatch)
    sid = _make_skill(rt.skill_manager)
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    # Verifier failed → outcome coerced to failure.
    result.verification = {"enabled": True, "pass": False}
    result.executions = [ExecutionResult(
        task_id=env.task_id, action_id="a1", ticket_id="tk",
        status="success", output_summary="done")]
    result.used_skill_ids = [sid]
    rt._record_task_outcome(env, _pre(env), _plan(env), result, None)

    rec = next(s for s in rt.skill_manager.list_skills()
               if s["skill_id"] == sid)
    assert rec["success_count"] == 0  # withheld
    assert not any(e["event"] == "skill_usage_success" for e in events)


def test_outcome_failure_on_adapted_skill_emits_audit(make_runtime_factory,
                                                      tmp_path, monkeypatch):
    rt, events = _wire_outcome_runtime(make_runtime_factory, tmp_path,
                                       monkeypatch)
    sid = _make_skill(rt.skill_manager)
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    result.verification = {"enabled": True, "pass": False}
    result.executions = [ExecutionResult(
        task_id=env.task_id, action_id="a1", ticket_id="tk",
        status="success", output_summary="done")]
    result.used_skill_ids = [sid]
    result.used_adapted_skill = True
    result.adapted_target_tool = "pptx"
    rt._record_task_outcome(env, _pre(env), _plan(env), result, None)

    fail = [e for e in events if e["event"] == "skill_adaptation_failed"]
    assert fail
    assert fail[0]["details"]["reason"] == "task_failed_after_adaptation"
    assert fail[0]["details"]["phase"] == "outcome"
    assert fail[0]["details"]["adapted_target_tool"] == "pptx"
    # Still no success credit.
    rec = next(s for s in rt.skill_manager.list_skills()
               if s["skill_id"] == sid)
    assert rec["success_count"] == 0


def test_outcome_failure_non_adapted_emits_no_adaptation_audit(
        make_runtime_factory, tmp_path, monkeypatch):
    rt, events = _wire_outcome_runtime(make_runtime_factory, tmp_path,
                                       monkeypatch)
    sid = _make_skill(rt.skill_manager)
    env = _envelope("Make a Q3 board deck in PowerPoint")
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    result.verification = {"enabled": True, "pass": False}
    result.executions = [ExecutionResult(
        task_id=env.task_id, action_id="a1", ticket_id="tk",
        status="success", output_summary="done")]
    result.used_skill_ids = [sid]
    result.used_adapted_skill = False  # plain retrieval, no adaptation
    rt._record_task_outcome(env, _pre(env), _plan(env), result, None)

    assert not any(e["event"] == "skill_adaptation_failed" for e in events)
