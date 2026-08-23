"""Regression lock for the 2026-08-21 UI transparency feature: when a
specific school artifact used a safe/deterministic template instead of the
live model's own draft (e.g. inside a LIVE + SAFE FALLBACK pack), the UI
must be able to show a plain-language reason for THAT file, not just the
task-level chip. Purely additive/read-only: derived entirely from the
already-computed `school_generation_validation` metadata; must never change
which content/mode was actually chosen.
"""
from __future__ import annotations

from teow_agl.modules.module_102b_synthesizer import (
    explain_school_generation_reason,
)


def test_returns_empty_for_fully_live_verified_modes():
    for mode in (
        "plan_level_action_id_mapping",
        "plan_level_batched_action_id_mapping",
        "plan_level_action_id_mapping_pending_bundle_audit",
    ):
        assert explain_school_generation_reason({"pass": True, "mode": mode}) == ""


def test_returns_empty_for_no_metadata():
    assert explain_school_generation_reason({}) == ""
    assert explain_school_generation_reason(None) == ""


def test_known_issue_labels_get_plain_language_explanation():
    validation = {
        "pass": True,
        "mode": "deterministic_response_pack_fallback",
        "live_bundle_issues": [
            "a1:grounding:unsupported_date_of_incident",
            "a1:process:emergency_services_contacted_or_arrived",
        ],
    }
    reason = explain_school_generation_reason(validation)
    assert "did not pass an automatic accuracy check" in reason
    assert "incident date" in reason
    assert "emergency services" in reason


def test_unknown_issue_labels_still_produce_a_reason_not_silence():
    validation = {
        "pass": True,
        "mode": "deterministic_response_pack_fallback",
        "live_bundle_issues": ["a1:some_new_future_check_label"],
    }
    reason = explain_school_generation_reason(validation)
    assert reason
    assert "some new future check label" in reason


def test_similarity_repair_mode_explained_even_without_issues_list():
    validation = {
        "pass": True,
        "mode": "deterministic_response_pack_similarity_repair",
    }
    reason = explain_school_generation_reason(validation)
    assert "too similar to another file" in reason


def test_provider_circuit_fallback_explained():
    validation = {
        "pass": True,
        "mode": "deterministic_response_pack_fallback",
        "live_bundle_issues": ["provider_circuit_open"],
    }
    reason = explain_school_generation_reason(validation)
    assert reason  # some explanation, either mapped or raw-label fallback


def test_clean_partial_bundle_retained_has_nothing_to_explain():
    """A live draft that was RETAINED because it had no issues of its own
    (only a sibling file in the pack had a problem) must not be badged —
    it is not itself a template."""
    validation = {
        "pass": True,
        "mode": "partial_bundle_retained",
        "semantic_audit_passed": True,
        "goal_alignment_passed": True,
        "bundle_goal_alignment_passed": True,
        "bundle_audit_issues": [],
    }
    assert explain_school_generation_reason(validation) == ""


def test_partial_bundle_retained_is_never_badged_even_with_bundle_issues():
    """2026-08-22 fix: a live-caught screenshot showed EVERY file in a
    7-file LIVE + SAFE FALLBACK pack badged "why a template?" — because
    `bundle_audit_issues` is a shared list attached to every retained
    action, not just the one actually at fault. `partial_bundle_retained`
    means THIS file kept its own live draft; it is never itself a
    template, no matter what a sibling file's issue was. Badging it
    "why a template?" is simply false and drowns out the one file that
    actually mattered — must stay silent."""
    validation = {
        "pass": True,
        "mode": "partial_bundle_retained",
        "bundle_audit_issues": ["missing_required_deliverable:xyz"],
    }
    assert explain_school_generation_reason(validation) == ""


def test_attach_school_generation_reasons_is_additive_and_matches_by_action_id():
    from server.app import _attach_school_generation_reasons

    class _Action:
        def __init__(self, action_id, validation):
            self.action_id = action_id
            self.metadata = {"school_generation_validation": validation}

    class _Plan:
        def __init__(self, actions):
            self.actions = actions

    plan = _Plan([
        _Action("a1", {
            "pass": True, "mode": "deterministic_response_pack_fallback",
            "live_bundle_issues": ["a1:grounding:unsupported_location"],
        }),
        _Action("a2", {"pass": True, "mode": "plan_level_action_id_mapping"}),
    ])
    executions = [
        {"action_id": "a1", "status": "success", "affected_resources": ["x.md"]},
        {"action_id": "a2", "status": "success", "affected_resources": ["y.md"]},
    ]
    before_a2 = dict(executions[1])
    _attach_school_generation_reasons(executions, plan)

    assert "school_generation_reason" in executions[0]
    assert "location" in executions[0]["school_generation_reason"]
    assert executions[0]["school_generation_submode"] == (
        "deterministic_response_pack_fallback"
    )
    # Fully live-verified file: no new keys added, dict otherwise untouched.
    assert executions[1] == before_a2
    assert "school_generation_reason" not in executions[1]


def test_mixed_bundle_only_badges_the_actual_template_not_the_whole_pack():
    """Reproduces the live screenshot: a 7-file LIVE + SAFE FALLBACK pack
    where only ONE file's own draft actually failed and became a
    template, but `bundle_audit_issues` was attached to every retained
    sibling. Only the genuinely-templated file should be badged."""
    from server.app import _attach_school_generation_reasons

    class _Action:
        def __init__(self, action_id, validation):
            self.action_id = action_id
            self.metadata = {"school_generation_validation": validation}

    class _Plan:
        def __init__(self, actions):
            self.actions = actions

    shared_bundle_issues = ["missing_required_deliverable:regulatory_notice"]
    actions = [
        _Action(f"a{i}", {
            "pass": True, "mode": "partial_bundle_retained",
            "semantic_audit_passed": True,
            "bundle_audit_issues": shared_bundle_issues,
        })
        for i in range(1, 7)
    ] + [
        _Action("a7", {
            "pass": True, "mode": "deterministic_response_pack_fallback",
            "live_bundle_issues": ["a7:grounding:unsupported_location"],
            "bundle_audit_issues": shared_bundle_issues,
        }),
    ]
    plan = _Plan(actions)
    executions = [
        {"action_id": a.action_id, "status": "success",
         "affected_resources": [f"{a.action_id}.md"]}
        for a in actions
    ]
    _attach_school_generation_reasons(executions, plan)

    badged = [e["action_id"] for e in executions if "school_generation_reason" in e]
    assert badged == ["a7"], badged


def test_attach_school_generation_reasons_noops_without_plan():
    from server.app import _attach_school_generation_reasons

    executions = [{"action_id": "a1", "status": "success"}]
    before = dict(executions[0])
    _attach_school_generation_reasons(executions, None)
    assert executions[0] == before
