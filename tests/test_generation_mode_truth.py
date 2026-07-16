from __future__ import annotations

from types import SimpleNamespace

from server.app import _school_generation_mode


def _action(mode: str = "", *, failed: bool = False):
    validation = {"pass": not failed, "mode": mode} if mode else {}
    return SimpleNamespace(metadata={
        "school_output_contract": True,
        "artifact_role": "internal_incident_report",
        "school_generation_failed": failed,
        "school_generation_validation": validation,
    })


def _plan(*actions):
    return SimpleNamespace(actions=list(actions))


def test_generation_mode_requires_actual_validated_live_output():
    assert _school_generation_mode(
        _plan(_action("plan_level_action_id_mapping")), "live",
    ) == "live_api_verified"
    assert _school_generation_mode(
        _plan(_action("plan_level_action_id_mapping")), "deterministic",
    ) == "deterministic"


def test_generation_mode_reports_provider_fallback_and_hybrid_honestly():
    assert _school_generation_mode(
        _plan(_action("deterministic_response_pack_fallback")), "live",
    ) == "deterministic_fallback"
    assert _school_generation_mode(
        _plan(
            _action("plan_level_action_id_mapping"),
            _action("deterministic_response_pack_similarity_repair"),
        ),
        "live",
    ) == "hybrid_live_with_deterministic_repair"


def test_generation_failure_is_never_badged_as_live():
    assert _school_generation_mode(
        _plan(_action("plan_level_action_id_mapping", failed=True)), "live",
    ) == "failed_closed"


def test_non_school_or_external_gate_only_has_no_generation_claim():
    external = SimpleNamespace(metadata={
        "school_output_contract": True,
        "artifact_role": "external_release_gate",
    })
    assert _school_generation_mode(_plan(external), "live") == "not_applicable"
