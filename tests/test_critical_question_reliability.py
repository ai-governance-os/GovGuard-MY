"""Regression lock for the 2026-08-21 fix: `_critical_question` (the
"is there still immediate danger?" pause) used to require the compiler's
OWN unknowns list to carry a model-assigned "life_safety" (or
"danger_still_present") tag before it would ask, on top of the already-
independent `source_safety` signal (deterministic hazard/safeguarding
regex OR semantic danger signals).

That tag is not reliable: the exact same active_danger/critical-severity
case ("2 days ago... part of ceiling in year 4 classroom fall") got tagged
"life_safety" on one live run and "content_only" on the next — silently
skipping a genuine safety question depending on how the model happened to
label an unrelated fact, not on whether the situation was actually
dangerous. Found live during Darren demo prep (owner noticed the question
step had stopped appearing) — see handoff doc for the trace evidence.
"""
from __future__ import annotations

from teow_agl.modules.module_school_situation import SchoolSituationCompiler


def _situation(*, unknowns, signals=None, severity="critical",
               compiler_source="school_semantic_llm", case_summary=""):
    return {
        "severity": severity,
        "case_summary": case_summary,
        "signals": signals or [],
        "compiler_source": compiler_source,
        "unknowns": unknowns,
    }


def test_question_fires_even_when_the_model_tags_unknowns_content_only():
    """The exact failing case: real danger signals present, severity
    critical, but every unknown the model produced is tagged content_only
    (not life_safety) — the question must still fire on source_safety
    alone."""
    situation = _situation(
        unknowns=[
            {"fact_id": "fact_5", "impact": "content_only"},
            {"fact_id": "fact_6", "impact": "content_only"},
            {"fact_id": "fact_7", "impact": "content_only"},
            {"fact_id": "fact_8", "impact": "governance_boundary"},
        ],
        signals=[
            "active_danger", "evidence_preservation_needed",
            "external_help_may_be_required", "guardian_notification_relevant",
            "possible_regulatory_trigger", "service_disruption",
        ],
        case_summary=(
            "2 days ago, time around 10:00 a.m. there is heavy rain, part of "
            "ceiling in year 4 classroom fall."
        ),
    )
    question = SchoolSituationCompiler._critical_question(situation, {})
    assert question is not None
    assert question["question_id"] == "immediate_danger"


def test_question_still_fires_the_old_way_life_safety_tagged():
    """No regression on the case that used to work: an unknown explicitly
    tagged life_safety must still trigger the question."""
    situation = _situation(
        unknowns=[{"fact_id": "fact_1", "impact": "life_safety"}],
        signals=["active_danger"],
        case_summary="A pupil was bitten by a snake near the canteen.",
    )
    question = SchoolSituationCompiler._critical_question(situation, {})
    assert question is not None
    assert question["question_id"] == "immediate_danger"


def test_question_does_not_fire_without_any_real_danger_signal():
    """Not a blanket trigger — severity/critical alone, with no hazard
    signal at all, must not ask. (Guards against over-firing.)"""
    situation = _situation(
        unknowns=[{"fact_id": "fact_1", "impact": "content_only"}],
        signals=[],
        severity="critical",
        case_summary="Please update the confirmed schedule in the parent notice.",
    )
    question = SchoolSituationCompiler._critical_question(situation, {})
    assert question is None


def test_question_does_not_fire_when_already_answered():
    situation = _situation(
        unknowns=[{"fact_id": "fact_1", "impact": "content_only"}],
        signals=["active_danger"],
        case_summary="A pupil fainted at assembly.",
    )
    question = SchoolSituationCompiler._critical_question(
        situation, {"immediate_danger": "No"},
    )
    assert question is None


def test_question_does_not_fire_below_high_severity():
    situation = _situation(
        unknowns=[{"fact_id": "fact_1", "impact": "content_only"}],
        signals=["active_danger"],
        severity="low",
        case_summary="A pupil fainted at assembly.",
    )
    question = SchoolSituationCompiler._critical_question(situation, {})
    assert question is None
