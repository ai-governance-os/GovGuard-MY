"""Focused release-intent regression tests.

These cases protect the boundary between producing communication content and
authorising an actual external release.  They intentionally test the shared
deterministic parser rather than relying on LLM output.
"""
from __future__ import annotations

import pytest

from teow_agl.modules.module_school_release_intent import (
    EXTERNAL_RECIPIENTS,
    infer_explicit_external_recipients,
    negated_external_recipients,
    recipients_in_release_text,
    release_clauses,
    release_is_globally_negated,
    requests_external_release,
)


@pytest.mark.parametrize(
    "text",
    [
        "Draft a message to parents.",
        "Prepare an SMS template for parents.",
        "Write a reply draft for Ali's parent.",
        "Draft a circular to distribute to all parents.",
        "Create a WhatsApp script to inform all parents.",
        "Prepare contact scripts for the hospital and parents.",
        "Write a Facebook post draft.",
        "Compose an email template for the District Education Office.",
        "Generate a notice that the school may later share with the council.",
        "A student cannot deliver the speech at assembly.",
        "The student will deliver a speech during assembly.",
        "Students share ideas in class.",
        "Write text about safety for the lesson.",
        "Teachers circulate around the classroom during supervision.",
    ],
)
def test_content_creation_and_speech_delivery_do_not_request_release(
    text: str,
) -> None:
    assert release_clauses(text) == ([], [])
    assert infer_explicit_external_recipients(text) == set()
    assert requests_external_release({}, text) is False


def test_draft_plus_explicit_execution_is_a_release() -> None:
    text = "Draft the notice and send it to all parents only after approval."
    positive, negative = release_clauses(text)
    assert positive == [
        "draft the notice and send it to all parents only after approval"
    ]
    assert negative == []
    assert infer_explicit_external_recipients(text) == {"school_community"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Send this message to the District Education Office after review.",
            {"education_authority"},
        ),
        ("Publish the notice on Facebook.", {"public_media"}),
        ("Post the announcement on the school website.", {"public_media"}),
        ("Share this report with the local authority.", {"local_authority"}),
        ("Upload the file to the Ministry portal.", {"education_authority"}),
        ("Forward the message to police.", {"police"}),
        ("Notify the parents now.", {"guardian"}),
        ("Contact Ali's parent after review.", {"guardian"}),
        ("Contact the hospital.", {"medical_services"}),
        ("Submit the report to PPD.", {"education_authority"}),
        (
            "Request official support from the District Education Office.",
            {"education_authority"},
        ),
        ("Inform all parents now.", {"school_community"}),
        ("Tell the guardian about the confirmed facts.", {"guardian"}),
        ("Announce the notice to all staff.", {"school_community"}),
        ("Issue a notice to the vendor.", {"vendor"}),
        ("Reply to the parent after approval.", {"guardian"}),
        ("Text the guardian after approval.", {"guardian"}),
        ("Distribute the notice to all parents.", {"school_community"}),
        ("Deliver a message to the parent.", {"guardian"}),
        ("Deliver the report to PPD.", {"education_authority"}),
    ],
)
def test_explicit_release_actions_identify_the_exact_recipient(
    text: str,
    expected: set[str],
) -> None:
    positive, negative = release_clauses(text)
    assert positive
    assert negative == []
    assert infer_explicit_external_recipients(text) == expected
    assert requests_external_release({}, text) is True


def test_global_negation_has_no_positive_release_and_covers_every_route() -> None:
    text = "Prepare contact scripts, but do not call, send, or publish anything."
    positive, negative = release_clauses(text)
    assert positive == []
    assert negative == ["do not call, send, or publish anything"]
    assert release_is_globally_negated(text) is True
    assert negated_external_recipients(text) == EXTERNAL_RECIPIENTS
    assert requests_external_release(
        {"situation": {"explicit_external_actions": ["guardian"]}},
        text,
    ) is False


@pytest.mark.parametrize(
    "text",
    [
        "Draft the notice and do not send it.",
        "Prepare the report without sending it.",
    ],
)
def test_same_clause_draft_negation_is_not_lost(text: str) -> None:
    positive, negative = release_clauses(text)
    assert positive == []
    assert negative
    assert release_is_globally_negated(text) is True


def test_recipient_negation_does_not_erase_a_different_positive_route() -> None:
    text = "Send the report to PPD, but do not notify parents."
    positive, negative = release_clauses(text)
    assert positive == ["send the report to ppd"]
    assert negative == ["do not notify parents"]
    assert release_is_globally_negated(text) is False
    assert infer_explicit_external_recipients(text) == {"education_authority"}
    assert negated_external_recipients(text) == {"guardian"}


def test_anything_to_named_recipient_is_scoped_not_global() -> None:
    text = "Do not send anything to parents; submit the report to PPD."
    assert infer_explicit_external_recipients(text) == {"education_authority"}
    assert negated_external_recipients(text) == {"guardian"}
    assert release_is_globally_negated(text) is False


def test_public_negation_and_private_release_remain_separate() -> None:
    text = "Do not publish it publicly, but send the private message to the parent."
    assert infer_explicit_external_recipients(text) == {"guardian"}
    assert negated_external_recipients(text) == {"public_media"}


def test_conditional_approval_phrase_retains_the_release_gate() -> None:
    text = "Do not send the notice to parents without leadership approval."
    positive, negative = release_clauses(text)
    assert positive == [
        "do not send the notice to parents without leadership approval"
    ]
    assert negative == []
    assert infer_explicit_external_recipients(text) == {"guardian"}


def test_all_parents_is_one_school_community_route() -> None:
    assert recipients_in_release_text("send this to all parents") == {
        "school_community"
    }
    assert infer_explicit_external_recipients(
        "Send this notice to the parent group."
    ) == {"school_community"}


def test_requested_output_fallback_is_order_independent() -> None:
    text = "Send the completed documents after approval."
    outputs_a = [
        {"role": "parent_notice", "recipient_type": "guardian"},
        {"role": "authority_report", "recipient_type": "education_authority"},
    ]
    outputs_b = list(reversed(outputs_a))
    first = infer_explicit_external_recipients(
        text,
        requested_outputs=outputs_a,
    )
    second = infer_explicit_external_recipients(
        text,
        requested_outputs=outputs_b,
    )
    assert first == second == {"guardian", "education_authority"}


def test_positive_clause_order_is_stable() -> None:
    text = (
        "Send the report to PPD. Notify the parent. "
        "Forward the evidence to police."
    )
    expected = [
        "send the report to ppd",
        "notify the parent",
        "forward the evidence to police",
    ]
    for _ in range(10):
        assert release_clauses(text)[0] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hantar notis ini kepada semua ibu bapa.", {"school_community"}),
        ("Emel laporan kepada PPD.", {"education_authority"}),
        ("Hubungi hospital.", {"medical_services"}),
        ("Maklumkan semua ibu bapa.", {"school_community"}),
        ("Siarkan notis ini di Facebook.", {"public_media"}),
    ],
)
def test_malay_release_verbs_are_recognised(
    text: str,
    expected: set[str],
) -> None:
    assert release_clauses(text)[0]
    assert infer_explicit_external_recipients(text) == expected


def test_malay_recipient_negation_is_scoped() -> None:
    text = "Jangan hantar apa-apa kepada ibu bapa; emel laporan kepada PPD."
    assert infer_explicit_external_recipients(text) == {"education_authority"}
    assert negated_external_recipients(text) == {"guardian"}


def test_chinese_mixed_negation_preserves_only_the_positive_route() -> None:
    text = "不要发给家长，但提交给教育局。"
    assert infer_explicit_external_recipients(text) == {"education_authority"}
    assert negated_external_recipients(text) == {"guardian"}
    assert release_is_globally_negated(text) is False


def test_strong_release_without_named_recipient_still_gets_a_gate() -> None:
    text = "Send now."
    assert infer_explicit_external_recipients(text) == {"external_stakeholder"}
    assert requests_external_release({}, text) is True
