"""Regression lock for the 2026-08-21 fix: the deterministic safe-fallback
template (module_102b_synthesizer._school_response_pack_safe_fallback) used
to hardcode "TBC" for date/time regardless of whether the source actually
gave one. A user typing "TIMES : 7:45 am" and getting "Time of incident:
TBC" back was a real gap, not intentional conservatism — the function
already had a `grounded()` mechanism for other fields, these two just never
used it.

Location is deliberately NOT covered here — free-text location has no
reliable extraction pattern and was scoped out on purpose (see handoff doc).
"""
from __future__ import annotations

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import (
    _extract_date_field,
    _extract_time_field,
    _school_response_pack_safe_fallback,
)
from teow_agl.modules.module_school_artifact_guard import validate_school_markdown


def _action(role: str) -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool="fs", operation="save_under_outputs",
        target="x.md", purpose="p", expected_effect="e",
        reversibility="high", uncertainty="low", risk_factors=[],
        requires_governance=True,
        metadata={"artifact_role": role, "audience": "internal"},
    )


def test_extract_time_field_echoes_a_reported_time_verbatim():
    assert _extract_time_field("TIMES : 7:45 am. Follow-up.") == "7:45 am (reported)"
    assert _extract_time_field("at approximately 11:50am on 15-6-2026") == \
        "11:50am (reported)"
    assert _extract_time_field("no time mentioned here") == ""


def test_extract_date_field_echoes_an_explicit_date_verbatim():
    assert _extract_date_field("held from 20-22 June 2026") == \
        "20-22 June 2026 (reported)"
    assert _extract_date_field("on 15-6-2026 the incident") == \
        "15-6-2026 (reported)"
    assert _extract_date_field("no date mentioned at all") == ""


def test_extract_date_field_resolves_today_yesterday_and_days_ago():
    # These use the real clock — assert the RESOLUTION LABEL and format,
    # not a hardcoded date, so the test doesn't rot with the calendar.
    today = _extract_date_field("this happened today")
    assert today.endswith("(resolved from 'today')")
    yesterday = _extract_date_field("this happened yesterday")
    assert yesterday.endswith("(resolved from 'yesterday')")
    days_ago = _extract_date_field("this happened 2 days ago")
    assert days_ago.endswith("(resolved from '2 days ago')")
    # An explicit date always wins over a relative phrase in the same text.
    both = _extract_date_field("today, which is 15 June 2026, the incident happened")
    assert "(reported)" in both and "resolved" not in both


def test_fallback_template_uses_reported_time_not_tbc():
    goal = ("A STUDENT NAME AHMAD FALL'S DOWN FROM SECOND LEVEL OF MAIN "
            "BUIDING . HEAD INJURED. TIMES : 7:45 am.")
    body = _school_response_pack_safe_fallback(_action("internal_incident_report"), goal)
    assert "Time of incident: 7:45 am (reported)" in body
    # No date was given in this exact source — must stay TBC, not invent one.
    assert "Date of incident: TBC" in body
    # Location was never in scope for this fix — must still be TBC.
    assert "- Location: TBC" in body


def test_fallback_template_resolves_today_into_a_real_date():
    goal = "A student fell from the stairs today. Time: 7:45 am."
    body = _school_response_pack_safe_fallback(_action("internal_incident_report"), goal)
    assert "resolved from 'today'" in body
    assert "Date of incident: TBC" not in body


def test_fallback_template_combined_bullet_partial_fill():
    """private_parent_notice / medical_handover_script render date+time+
    location as one bullet — must partially fill it, not fall back to the
    all-TBC wording, once at least one of date/time is known."""
    goal = "A student fell today. Time: 7:45 am."
    for role in ("private_parent_notice", "medical_handover_script"):
        body = _school_response_pack_safe_fallback(_action(role), goal)
        assert "Time: 7:45 am (reported)" in body
        assert "Location: TBC" in body
        # The original all-in-one TBC wording must be gone once something
        # was actually found.
        assert "date, time and location: TBC" not in body.lower()


def test_resolved_today_date_passes_grounding_not_flagged_as_invented():
    """2026-08-21 same-day regression: the fix above made
    _school_response_pack_safe_fallback WRITE a resolved date, but
    validate_school_markdown's generic per-field grounding check does a
    verbatim-token-subset match and doesn't know about relative-date
    resolution — it flagged the fallback template's OWN output as
    unsupported_date_of_incident, turning a safe TBC into a GENERATION HELD
    failure. Locks the fix: a correctly-resolved date must pass grounding,
    a wrong one must still fail (no generic bypass)."""
    goal = ("TODAY, A STUDENT NAME AHMAD INJURED IN LEG DURING PJ LESSON. "
            "TIME 8:30 . am. PREPARE EVERYTHING")
    action = _action("internal_incident_report")
    body = _school_response_pack_safe_fallback(action, goal)
    issues = validate_school_markdown(action, body, goal)
    assert not issues["grounding"], issues["grounding"]

    # A fabricated/wrong date in the same field must still be rejected —
    # this is not a blanket bypass for anything that looks like a date.
    # (Don't hardcode "today"'s resolved value — compute it so the test
    # doesn't rot with the calendar.)
    real_resolved = _extract_date_field(goal).split(" (")[0]
    wrong_date = "31 December 1999"
    assert wrong_date != real_resolved
    forged = body.replace(real_resolved, wrong_date)
    forged_issues = validate_school_markdown(action, forged, goal)
    assert any("date_of_incident" in i for i in forged_issues["grounding"]), (
        "a wrong date slipped past grounding — the fix is too permissive"
    )


def test_fallback_template_stays_all_tbc_when_nothing_is_reported():
    """No regression on the ordinary "nothing given" case — must render
    EXACTLY the original wording, not a partially-broken bullet."""
    goal = "A student was involved in an incident. Please prepare a report."
    for role, needle in (
        ("private_parent_notice", "- Exact date, time and location: TBC"),
        ("medical_handover_script", "- Date, time and exact location: TBC"),
    ):
        body = _school_response_pack_safe_fallback(_action(role), goal)
        assert needle in body, body


def test_location_is_echoed_when_the_source_names_a_school_place():
    """2026-08-21: "Location: TBC" printed directly under an Incident
    snapshot quoting "part of ceiling in year 4 classroom" read as a
    contradiction. The extractor echoes only a closed vocabulary of school
    place nouns, so whatever it emits is literally in the source."""
    from teow_agl.modules.module_102b_synthesizer import _extract_location_field

    assert _extract_location_field(
        "part of ceiling in year 4 classroom fall") == "year 4 classroom (reported)"
    assert _extract_location_field(
        "A student fainted in the school hall") == "school hall (reported)"
    # No recognised place named → stays empty so the caller keeps TBC.
    assert _extract_location_field("A student was involved in an incident.") == ""
    assert _extract_location_field("Ahmad fell from the second level.") == ""

    goal = ("yesterday, around 10:30 A.M., there is heavy rain, part of "
            "ceiling in year 4 classroom fall. no student are injuried.")
    action = _action("internal_incident_report")
    body = _school_response_pack_safe_fallback(action, goal)
    assert "- Location: year 4 classroom (reported)" in body
    # And it must survive the deterministic grounding check.
    assert not validate_school_markdown(action, body, goal)["grounding"]


def test_opaque_unknown_fact_ids_become_meaningful_labels():
    """2026-08-21: the compiler emits ids like "fact_4"; the old guard only
    caught the unseparated "fact4", so readers saw "Fact 4: TBC". The
    verifier itself flags those as non-meaningful placeholders. Each
    unknown's `impact` carries the real meaning — use it."""
    goal = "A student was involved in an incident."
    action = _action("internal_incident_report")
    action.metadata["school_unknowns"] = [
        {"fact_id": "fact_4", "impact": "life_safety"},
        {"fact_id": "fact_5", "impact": "governance_boundary"},
        {"fact_id": "fact_6", "impact": "required_deliverables"},
    ]
    body = _school_response_pack_safe_fallback(action, goal)
    assert "Fact 4" not in body and "Fact 5" not in body and "Fact 6" not in body
    assert "Whether anyone still needs urgent help or medical care" in body
    assert "Who authorised this and what may be shared" in body
    assert "Which documents the school actually needs" in body


def test_live_relative_date_is_resolved_after_the_audit_passes():
    """2026-08-21: the live model reliably declines to do the arithmetic —
    it writes "Date: 2 days ago (reported; exact date TBC)". Resolving it in
    the prompt failed (the model ignored the rule) and resolving it BEFORE
    the audit failed worse (the LLM auditor flagged our own resolved date as
    unsupported). So it is filled in deterministically after validation and
    audit have already passed. Bounded: only a date FIELD, only when the
    value literally contains the source's own relative phrase."""
    from teow_agl.modules.module_102b_synthesizer import (
        _resolve_relative_dates_in_body,
    )
    src = "2 days ago, around 10:00 a.m., part of ceiling in year 4 classroom fall."

    for line in (
        "- **Date:** 2 days ago (reported; exact date TBC)",
        "- **Date**: 2 days ago (reported)",
        "- Date: 2 days ago",
        "- Date of incident: 2 days ago",
    ):
        out = _resolve_relative_dates_in_body(line, src)
        assert "resolved from '2 days ago'" in out, out
        assert "2 days ago (reported" not in out
        # Markdown emphasis must survive intact.
        assert out.count("**") == line.count("**"), out

    # An explicit date is never overwritten.
    explicit = "**Date:** 15 June 2026 (reported)"
    assert _resolve_relative_dates_in_body(explicit, src) == explicit
    # Prose that merely mentions the phrase is not a field — leave it alone.
    prose = "The roof was checked 2 days ago in passing prose."
    assert _resolve_relative_dates_in_body(prose, src) == prose
    # No relative phrase in the source → nothing to resolve.
    assert _resolve_relative_dates_in_body(
        "- Date: sometime", "incident on 15 June 2026") == "- Date: sometime"


def test_operator_name_env_override_for_representative_signoff(monkeypatch):
    """2026-08-21: TEOW_AGL_OPERATOR_NAME lets a single-operator deployment
    name the 'authorised school representative' instead of showing TBC.
    Unset (the submission/production default) must be byte-for-byte the
    original wording — this is deployment config, opt-in only."""
    goal = "A student was involved in an incident."

    monkeypatch.delenv("TEOW_AGL_OPERATOR_NAME", raising=False)
    action = _action("private_parent_notice")
    action.metadata["channel"] = "email"
    default_body = _school_response_pack_safe_fallback(action, goal)
    assert "TBC - authorised school representative" in default_body
    assert "- School contact person and return number: TBC" in default_body

    monkeypatch.setenv("TEOW_AGL_OPERATOR_NAME", "Keane Teow")
    named_body = _school_response_pack_safe_fallback(action, goal)
    assert "Keane Teow\nAuthorised school representative" in named_body
    assert "- School contact person: Keane Teow" in named_body
    assert "Return number: TBC" in named_body
    # No claim is made that a phone number was ever supplied.
    assert "TBC - authorised school representative" not in named_body
