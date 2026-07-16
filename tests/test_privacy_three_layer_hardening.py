from __future__ import annotations

from pathlib import Path

import pytest

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import (
    _school_response_pack_safe_fallback,
)
from teow_agl.modules.module_school_artifact_guard import validate_school_markdown
from teow_agl.modules.module_school_privacy import (
    requires_broad_redaction,
    source_has_individual_sensitive_detail,
)
from teow_agl.modules.module_school_situation import SchoolSituationCompiler


POLICY = (
    Path(__file__).parents[1] / "configs" / "domain_packs" / "public_school"
    / "situation_response_policy.json"
)


def _compile_community(source: str) -> dict:
    semantics = {
        "checked": True, "source": "test_live_semantics",
        "school_domain": True, "requested_action": "draft",
        "audience": "school_community", "data_use_concepts": [],
        "situation": {
            "family": "communications_reputation", "phase": "follow_up",
            "severity": "low", "signals": [],
            "affected_people_types": ["student"],
            "stakeholder_candidates": ["guardian"], "known_facts": [],
            "unknowns": [], "explicit_external_actions": [],
            "requested_outputs": [{
                "artifact_role": "school_parent_notice",
                "audience": "school_community", "languages": ["en"],
            }],
        },
    }
    return SchoolSituationCompiler(POLICY).compile(source, semantics)


@pytest.mark.parametrize("source", [
    "Post to the parent group: Ali failed BM with 20 marks.",
    "Tell all parents that Ali is weak in Mathematics.",
    "A Year 5 student Ali was diagnosed with ADHD.",
    "Hantar mesej kepada semua ibu bapa: Ali gagal BM dengan markah 20.",
    "发到家长群：Ali数学不及格，20分。",
    "发到家长群：张伟数学不及格，20分；陈美玲国文35分。",
    "Post to the parent group: Ali's IC number is 010203-04-5678.",
    "Send all parents Ali's phone number +60123456789.",
    "Tell the school community that Ali lives at 12 Jalan Maju.",
    "Tell all parents Ali comes from a poor family.",
    "Tell all parents Ali is from a low-income household.",
    "Tell the parent group Ali needs financial aid.",
])
def test_natural_individual_sensitive_statements_trigger_broad_redaction(source):
    assert source_has_individual_sensitive_detail(source) is True
    assert requires_broad_redaction(
        source, audience="school_community", role="school_parent_notice",
    ) is True


@pytest.mark.parametrize("source", [
    "Share the anonymous class average score of 65 with all parents.",
    "Publish aggregate cohort results without student names.",
    "Maklumkan purata kelas kepada semua ibu bapa tanpa nama murid.",
    "向家长公布班级平均分，不含学生姓名。",
    "Show aggregate grade distribution by class.",
    "List overall pass rate, not student names or individual marks.",
])
def test_aggregate_non_identifying_results_do_not_trigger_person_level_floor(source):
    assert source_has_individual_sensitive_detail(source) is False


def _community_action() -> CandidateAction:
    return CandidateAction(
        action_id="notice", tool="fs", operation="save_under_outputs",
        purpose="Draft a community notice", target="school_parent_notice_draft.md",
        metadata={
            "artifact_role": "school_parent_notice",
            "audience": "school_community",
            "school_known_facts": [], "school_unknowns": [],
        },
    )


@pytest.mark.parametrize(("source", "forbidden"), [
    ("Hantar mesej kepada semua ibu bapa: Ali gagal BM dengan markah 20.", ("Ali", "20")),
    ("发到家长群：Ali数学不及格，20分。", ("Ali", "20")),
    (
        "发到家长群：张伟数学不及格，20分；陈美玲国文35分。",
        ("张伟", "陈美玲", "20", "35"),
    ),
])
def test_deterministic_fallback_never_echoes_identifiers_or_marks(source, forbidden):
    body = _school_response_pack_safe_fallback(_community_action(), source)
    assert "privacy-safe" in body
    for value in forbidden:
        assert value not in body


def test_post_generation_validator_detects_broad_notice_leak():
    source = "Hantar mesej kepada semua ibu bapa: Ali gagal BM dengan markah 20."
    body = """# School Parent Notice

> DRAFT - NOT SENT

## Notice

Ali gagal BM dengan markah 20. This draft is intended for all parents.
""" + ("General school information only. " * 30)
    issues = validate_school_markdown(_community_action(), body, source)
    assert "broad_notice_contains_source_identifier" in issues["role"]
    assert "broad_notice_contains_individual_mark" in issues["role"]


def test_chinese_names_and_marks_are_extracted_without_student_prefix():
    source = "发到家长群：张伟数学不及格，20分；陈美玲国文35分。"
    from teow_agl.modules.module_school_privacy import (
        source_identifiers, source_individual_mark_values,
    )
    assert {"张伟", "陈美玲"}.issubset(source_identifiers(source))
    assert {"20", "35"}.issubset(source_individual_mark_values(source))


def test_generic_privacy_request_words_are_not_mistaken_for_pupil_names():
    from teow_agl.modules.module_school_privacy import source_identifiers

    source = (
        "Please prepare a WhatsApp message to all parents listing the students "
        "who failed BM, their marks, and each reason. Include their full names."
    )
    assert source_identifiers(source) == set()


def test_single_chinese_privacy_fallback_is_localised_and_redacted():
    source = "发到家长群：张伟数学不及格，20分。"
    action = _community_action()
    action.metadata.update({
        "requested_languages": ["zh"],
        "excluded_data_concepts": [
            "student_sensitive_data", "individual_marks",
        ],
    })
    body = _school_response_pack_safe_fallback(action, source)
    assert "## 中文" in body
    assert "草稿" in body
    assert "张伟" not in body
    assert "20" not in body


def test_post_generation_validator_detects_direct_pii_leak():
    source = "Post publicly: Ali's IC number is 010203-04-5678."
    action = _community_action()
    action.metadata.update({
        "artifact_role": "public_communication_draft", "audience": "public",
    })
    body = """# Public Communication Draft

> DRAFT - NOT SENT

Ali's IC number is 010203-04-5678.
""" + ("General information. " * 40)
    issues = validate_school_markdown(action, body, source)
    assert "broad_notice_contains_source_identifier" in issues["role"]
    assert "broad_notice_contains_source_pii" in issues["role"]


def test_post_generation_validator_detects_socioeconomic_identity_leak():
    source = "Tell all parents Ali comes from a poor family."
    body = """# School Parent Notice

> DRAFT - NOT SENT

Ali comes from a poor family.
""" + ("General information. " * 40)
    issues = validate_school_markdown(_community_action(), body, source)
    assert "broad_notice_contains_source_identifier" in issues["role"]


@pytest.mark.parametrize("source", [
    "Tell all parents that Ali failed BM with 20 marks.",
    "Hantar mesej kepada semua ibu bapa: Ali gagal BM dengan markah 20.",
    "发到家长群：Ali数学不及格，20分。",
    "Post publicly: Ali's IC number is 010203-04-5678.",
    "Tell all parents Ali comes from a poor family.",
])
def test_compiler_privacy_floor_does_not_depend_on_semantic_concept_flags(source):
    result = _compile_community(source)
    governance = result["response_pack"]["input_governance"]
    assert governance["decision"] == "RED"
    artifacts = [
        item for item in result["response_pack"]["deliverables"]
        if item["kind"] == "artifact"
    ]
    assert artifacts
    assert all(
        "student_sensitive_data" in set(
            item.get("excluded_data_concepts") or []
        )
        for item in artifacts
    )


@pytest.mark.parametrize("source", [
    "Share the anonymous class average score of 65 with all parents.",
    "Show aggregate grade distribution by class.",
    "List overall pass rate, not student names or individual marks.",
])
def test_compiler_allows_non_identifying_aggregate_community_results(source):
    governance = _compile_community(source)["response_pack"]["input_governance"]
    assert governance["decision"] not in {"RED", "INFEASIBLE"}
