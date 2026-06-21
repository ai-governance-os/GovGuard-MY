"""Phase 2 L4.9 — end-to-end integration tests for the Level-3 skill
transfer-learning chain.

This file ties together every Phase-2 sub-slice (L4.1 → L4.8) and exercises
them through the REAL objects (SkillManager + SubjectConfidence in tmp, a
real ContentSynthesizer, a real Runtime), as one connected flow:

    create_skill (principle + parameters + embedding)        # L4.4, L4.3
        → find_relevant cosine retrieval                     # L4.3
        → Runtime._maybe_adapt_skill cross-context adaptation # L4.5, L4.7
        → brief injection + used_adapted_skill flag           # L4.7
        → _record_task_outcome success / failure isolation    # L4.7
        → lifecycle_sweep + status-weighted retrieval         # L4.8

The OpenAI network is never touched: embeddings go through a mocked
`_embed_via_provider`, and the synthesizer's `_adapt_skill_to_task` is
monkeypatched. The matching LIVE proof (5 real cross-context cases against
gpt-4o-mini) lives in scripts/verify_phase2_e2e.py and feeds the L4.10 doc.

Five distinct cross-context transfer cases are parametrised below:
docx→pptx, docx→xlsx, pptx→docx, xlsx→pptx, and a Chinese-goal docx→pptx.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from teow_agl.modules.module_skill_manager import SkillManager
from teow_agl.policies.subject_confidence import SubjectConfidence
from teow_agl.runtime import Runtime, TaskRunResult
from teow_agl.models import (
    CandidateAction, CandidatePlan, ExecutionResult,
    PreGovernanceAssessment, TaskEnvelope,
)
from teow_agl.util import embeddings as emb

from tests.conftest import make_runtime


# ---------------------------------------------------------------------------
# Embedding lane: mocked so cosine retrieval is deterministic and free.
# A constant vector makes every cosine similarity 1.0, so a skill is always
# retrieved by the cosine lane — exactly what we want for "does the chain
# wire up" integration coverage (the *ranking* maths is unit-tested in
# test_skill_manager_embeddings.py).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _embedding_env(monkeypatch):
    emb._reset_dim_cache_for_tests()
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda texts: [[0.1] * 1536 for _ in texts])
    yield
    emb._reset_dim_cache_for_tests()


def _constraints() -> dict:
    return {
        "creation_limits": {
            "max_skills_per_task": 1, "max_chars_per_skill": 4000,
            "min_chars_per_skill": 10, "max_total_skills": 200,
        },
        "retrieval": {"top_k_injected": 5, "min_score_for_injection": 0.0},
        "lifecycle": {
            "active_to_stale_days_unused": 90,
            "stale_to_archived_days_unused": 180,
            "supersede_on_new_skill_same_shape": True,
            "stale_retrieval_weight": 0.5,
            "superseded_retrieval_weight": 0.0,
            "auto_archive_enabled": False,
        },
        "forbidden_patterns": {"patterns": []},
    }


_TOOL_PROC = {
    "docx": "1. Outline the sections.\n2. Write prose paragraphs.\n"
            "3. Save as a Word document and verify it is non-empty.",
    "pptx": "1. Outline the slides.\n2. Write bullet points per slide.\n"
            "3. Save as a slide deck and verify it opens.",
    "xlsx": "1. Lay out the columns.\n2. Fill rows with the data.\n"
            "3. Save as a spreadsheet and verify the cells.",
}


def _create_skill(sm: SkillManager, *, name: str, tool: str) -> str:
    out = sm.create_skill(
        name=name,
        description=f"Produce a quarterly business deliverable as a {tool}.",
        procedure=_TOOL_PROC[tool],
        parameters={"tool": tool, "output_format": tool,
                    "output_language": "en"},
        principle="Organise findings into a structured narrative and verify "
                  "the artifact before delivery.",
        source_category="office_doc_generation",
        source_shape=f"{tool}_v1",
        tags=["office", tool],
    )
    assert out["ok"], out
    return out["skill_id"]


def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_l49", session_id="sess", user_id="default_user",
        raw_goal=goal, normalized_goal=goal,
    )


def _pre(env: TaskEnvelope) -> PreGovernanceAssessment:
    return PreGovernanceAssessment(
        task_id=env.task_id, task_category="office_doc_generation",
        planning_mode="direct",
    )


def _plan(env: TaskEnvelope, tool: str = "pptx") -> CandidatePlan:
    return CandidatePlan(
        task_id=env.task_id, planner_id="mock", planning_mode="direct",
        actions=[CandidateAction(
            action_id="a1", tool=tool, operation="save", target="",
            purpose="t", expected_effect="t", reversibility="high",
            uncertainty="low", risk_factors=[], requires_governance=True,
        )],
    )


def _wire_runtime(workspace: Path, tmp_path: Path):
    """A real Runtime with a real SkillManager + SubjectConfidence attached,
    an adaptation mock that produces target-tool-aware procedures, and an
    _emit capture. Returns (rt, sm, events)."""
    rt = make_runtime(workspace)
    sm = SkillManager(tmp_path / "skills", constraints=_constraints())
    rt.skill_manager = sm
    rt.subject_confidence = SubjectConfidence(path=tmp_path / "subj.jsonl")

    def _adapt(skill, *, target_tool, target_intent, **kw):
        return (f"1. Build the {target_tool} structure.\n"
                f"2. Populate it from the source principle.\n"
                f"3. Save as a real {target_tool} artifact and verify it.",
                "ok")

    # rt.synthesizer is a real ContentSynthesizer; we only swap the LLM call.
    assert hasattr(rt.synthesizer, "_adapt_skill_to_task")
    rt.synthesizer._adapt_skill_to_task = _adapt  # type: ignore[assignment]

    events: list[dict] = []
    orig_emit = rt._emit

    def _capture(module, event_type, task_id, session_id,
                 input_text="", *, summary="", details=None):
        events.append({"event": event_type, "details": details or {}})

    rt._emit = _capture  # type: ignore[assignment]
    return rt, sm, events


# ===========================================================================
# Group 1 — cross-context transfer, full chain (5 parametrised cases)
# ===========================================================================

_CROSS_CASES = [
    ("docx", "pptx", "Turn the Q3 report into a board slide deck"),
    ("docx", "xlsx", "Put the Q3 figures into an Excel spreadsheet"),
    ("pptx", "docx", "Write the deck up as a Word document"),
    ("xlsx", "pptx", "Make a PowerPoint presentation from the spreadsheet"),
    ("docx", "pptx", "把这份报告做成给董事会的演示文稿"),
]


@pytest.mark.parametrize("source_tool,target_tool,goal", _CROSS_CASES)
def test_cross_context_full_chain(
    isolated_workspace, tmp_path, source_tool, target_tool, goal,
):
    """create → cosine-retrieve → adapt → inject → flag, end to end."""
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    sid = _create_skill(sm, name=f"q3-{source_tool}", tool=source_tool)

    # L4.3 — cosine retrieval pulls the skill back for a different-tool goal.
    hits = sm.find_relevant(goal, top_k=5)
    assert hits and hits[0]["skill_id"] == sid
    assert hits[0]["rank_method"] == "cosine"

    # L4.5 / L4.7 — adapt the retrieved skill to the goal's target tool.
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    result.used_skill_ids = [h["skill_id"] for h in hits]
    brief: dict = {"relevant_skills": hits}
    rt._maybe_adapt_skill(env, hits, brief, result)

    assert result.used_adapted_skill is True
    assert result.adapted_target_tool == target_tool
    assert target_tool in brief["adapted_skill_procedure"]
    assert "adapted_skill_note" in brief
    adapted_ev = [e for e in events if e["event"] == "skill_adapted"]
    assert adapted_ev
    assert adapted_ev[0]["details"]["from_tool"] == source_tool
    assert adapted_ev[0]["details"]["to_tool"] == target_tool


@pytest.mark.parametrize("source_tool,target_tool,goal", _CROSS_CASES)
def test_cross_context_success_credits_skill(
    isolated_workspace, tmp_path, source_tool, target_tool, goal,
):
    """A verified-good adapted task credits success_count exactly once."""
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    sid = _create_skill(sm, name=f"q3-{source_tool}", tool=source_tool)
    hits = sm.find_relevant(goal, top_k=5)
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    result.used_skill_ids = [sid]
    rt._maybe_adapt_skill(env, hits, {"relevant_skills": hits}, result)
    assert result.used_adapted_skill is True

    # Successful execution, verifier passes → outcome success.
    result.executions = [ExecutionResult(
        task_id=env.task_id, action_id="a1", ticket_id="tk",
        status="success", output_summary="done")]
    rt._record_task_outcome(env, _pre(env), _plan(env, target_tool),
                            result, None)

    rec = next(s for s in sm.list_skills(include_archived=True)
               if s["skill_id"] == sid)
    assert rec["success_count"] == 1
    assert any(e["event"] == "skill_usage_success" for e in events)


# ===========================================================================
# Group 2 — no adaptation when tool already matches / no target tool
# ===========================================================================

def test_same_tool_skips_adaptation_but_still_injects(isolated_workspace,
                                                      tmp_path):
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    sid = _create_skill(sm, name="q3-docx", tool="docx")
    goal = "Write a Word document summarising Q3"
    hits = sm.find_relevant(goal, top_k=5)
    assert hits and hits[0]["skill_id"] == sid   # raw skill still retrieved
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    rt._maybe_adapt_skill(env, hits, {"relevant_skills": hits}, result)
    assert result.used_adapted_skill is False
    assert not any(e["event"].startswith("skill_adapt") for e in events)


def test_no_target_tool_skips_adaptation(isolated_workspace, tmp_path):
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    _create_skill(sm, name="q3-docx", tool="docx")
    goal = "Summarise the quarterly findings for me"
    hits = sm.find_relevant(goal, top_k=5)
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    rt._maybe_adapt_skill(env, hits, {"relevant_skills": hits}, result)
    assert result.used_adapted_skill is False


# ===========================================================================
# Group 3 — abstraction (L4.4): principle is the embedded retrieval surface
# ===========================================================================

def test_principle_is_embedded_not_description(isolated_workspace, tmp_path,
                                               monkeypatch):
    sm = SkillManager(tmp_path / "skills", constraints=_constraints())
    seen: list[str] = []

    def _capture(texts):
        seen.extend(texts)
        return [[0.2] * 1536 for _ in texts]

    monkeypatch.setattr(emb, "_embed_via_provider", _capture)
    out = sm.create_skill(
        name="make-doc",
        description="DESC_TOKEN_SHOULD_NOT_EMBED",
        procedure=_TOOL_PROC["docx"],
        parameters={"tool": "docx"},
        principle="PRINCIPLE_TOKEN organise and verify the artifact.",
    )
    assert out["ok"] and out["embedding_persisted"]
    joined = " ".join(seen)
    assert "PRINCIPLE_TOKEN" in joined
    assert "DESC_TOKEN_SHOULD_NOT_EMBED" not in joined


def test_adaptation_carries_principle_into_new_tool(isolated_workspace,
                                                    tmp_path):
    """The body handed to the adapter carries the source principle — the
    'why' that survives the tool switch (Level-3 transfer)."""
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    captured: dict = {}

    def _adapt(skill, *, target_tool, target_intent, **kw):
        captured["body"] = skill["body"]
        return (f"adapted for {target_tool}", "ok")

    rt.synthesizer._adapt_skill_to_task = _adapt  # type: ignore
    sid = _create_skill(sm, name="q3-docx", tool="docx")
    goal = "Make a board slide deck from the report"
    hits = sm.find_relevant(goal, top_k=5)
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    rt._maybe_adapt_skill(env, hits, {"relevant_skills": hits}, result)
    assert "## Principle" in captured["body"]
    assert "Organise findings into a structured narrative" in captured["body"]


# ===========================================================================
# Group 4 — lifecycle (L4.8) integrated with retrieval
# ===========================================================================

def test_stale_skill_still_retrievable_then_archive_excludes(
    isolated_workspace, tmp_path,
):
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    sid = _create_skill(sm, name="q3-docx", tool="docx")
    goal = "Make a board slide deck from the report"

    # Active: retrieved at full weight.
    active_hits = sm.find_relevant(goal, top_k=5)
    active_score = next(h["score"] for h in active_hits if h["skill_id"] == sid)

    # Stale (auto via sweep at a future date): still retrievable, half weight.
    sm.lifecycle_sweep(now=datetime.now(timezone.utc) + timedelta(days=120))
    stale_hits = sm.find_relevant(goal, top_k=5)
    stale_score = next(h["score"] for h in stale_hits if h["skill_id"] == sid)
    assert stale_score == pytest.approx(active_score * 0.5, rel=1e-6)

    # Archived: gone from retrieval entirely.
    sm.archive_skill(sid)
    assert sm.find_relevant(goal, top_k=5) == []


def test_supersede_via_sweep_approval_removes_from_retrieval(
    isolated_workspace, tmp_path,
):
    """Two same-shape docx skills → sweep proposes supersede → human
    approves → the older one drops out of retrieval (weight 0.0)."""
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    old = _create_skill(sm, name="q3-docx-old", tool="docx")
    new = _create_skill(sm, name="q3-docx-new", tool="docx")
    goal = "Write a Word document summarising Q3"

    before = {h["skill_id"] for h in sm.find_relevant(goal, top_k=5)}
    assert {old, new} <= before

    rt.run_skill_lifecycle()
    prop = next(p for p in rt.list_curator_proposals(status="pending")
                if p["type"] == "supersede_skill" and p["skill_id"] == old)
    ok, reason, _ = rt.decide_curator_proposal(prop["proposal_id"], "approved")
    assert ok, reason

    after = {h["skill_id"] for h in sm.find_relevant(goal, top_k=5)}
    assert new in after
    assert old not in after


def test_superseded_skill_not_adapted(isolated_workspace, tmp_path):
    """A superseded skill must never be the adaptation source — it isn't
    even in the retrieval pool, so _maybe_adapt_skill gets no hit for it."""
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    sid = _create_skill(sm, name="q3-docx", tool="docx")
    sm.mark_superseded(sid)
    goal = "Make a board slide deck from the report"
    hits = sm.find_relevant(goal, top_k=5)
    assert hits == []
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    # No hits → adaptation is a no-op, never flags.
    rt._maybe_adapt_skill(env, hits, {"relevant_skills": hits}, result) \
        if hits else None
    assert result.used_adapted_skill is False


# ===========================================================================
# Group 5 — failure isolation, end to end (L4.7 contract through the chain)
# ===========================================================================

def test_failed_adapted_task_withholds_credit_and_audits(isolated_workspace,
                                                         tmp_path):
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    sid = _create_skill(sm, name="q3-docx", tool="docx")
    goal = "Make a board slide deck from the report"
    hits = sm.find_relevant(goal, top_k=5)
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    result.used_skill_ids = [sid]
    rt._maybe_adapt_skill(env, hits, {"relevant_skills": hits}, result)
    assert result.used_adapted_skill is True

    # Verifier fails the adapted output → outcome coerced to failure.
    result.verification = {"enabled": True, "pass": False}
    result.executions = [ExecutionResult(
        task_id=env.task_id, action_id="a1", ticket_id="tk",
        status="success", output_summary="done")]
    rt._record_task_outcome(env, _pre(env), _plan(env, "pptx"), result, None)

    rec = next(s for s in sm.list_skills(include_archived=True)
               if s["skill_id"] == sid)
    assert rec["success_count"] == 0  # withheld — no learning-loop poisoning
    fail = [e for e in events if e["event"] == "skill_adaptation_failed"]
    assert fail
    assert fail[-1]["details"]["reason"] == "task_failed_after_adaptation"
    assert fail[-1]["details"]["phase"] == "outcome"
    assert fail[-1]["details"]["adapted_target_tool"] == "pptx"


def test_synthesis_failure_falls_back_without_strict_mode(isolated_workspace,
                                                          tmp_path):
    """If the adapter returns a non-ok status, the run keeps the raw skill,
    does NOT flag strict mode, and emits a synthesis-phase failure audit."""
    rt, sm, events = _wire_runtime(isolated_workspace, tmp_path)
    rt.synthesizer._adapt_skill_to_task = (  # type: ignore
        lambda skill, **kw: (None, "adaptation_invalid_format"))
    sid = _create_skill(sm, name="q3-docx", tool="docx")
    goal = "Make a board slide deck from the report"
    hits = sm.find_relevant(goal, top_k=5)
    env = _envelope(goal)
    result = TaskRunResult(envelope=env, pre_assessment=_pre(env))
    brief: dict = {"relevant_skills": hits}
    rt._maybe_adapt_skill(env, hits, brief, result)

    assert result.used_adapted_skill is False
    assert "adapted_skill_procedure" not in brief
    fail = [e for e in events if e["event"] == "skill_adaptation_failed"]
    assert fail and fail[0]["details"]["reason"] == "adaptation_invalid_format"
    assert fail[0]["details"]["phase"] == "synthesis"
