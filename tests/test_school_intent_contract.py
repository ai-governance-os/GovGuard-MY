from teow_agl.modules.module_school_intent_contract import (
    align_deliverables_to_contract,
    build_user_intent_contract,
)


def test_duplicate_school_document_roles_remain_three_ordered_obligations():
    semantics = {
        "audience": "internal",
        "situation": {
            "requested_outputs": [
                {
                    "artifact_role": "school_document",
                    "label": "Asset intake log",
                    "purpose": "Record devices received",
                },
                {
                    "artifact_role": "school_document",
                    "label": "Handover checklist",
                    "purpose": "Control device handover",
                },
                {
                    "artifact_role": "school_document",
                    "label": "Reconciliation sheet",
                    "purpose": "Reconcile returned devices",
                },
            ]
        },
    }

    contract = build_user_intent_contract("Prepare all three records.", semantics)

    assert [item["label"] for item in contract["obligations"]] == [
        "Asset intake log",
        "Handover checklist",
        "Reconciliation sheet",
    ]
    assert len({item["obligation_id"] for item in contract["obligations"]}) == 3
    assert {item["artifact_role"] for item in contract["obligations"]} == {
        "school_document"
    }


def test_secondary_exact_copy_is_deduplicated_but_custom_file_is_retained():
    semantic_output = {
        "artifact_role": "internal_incident_report",
        "label": "Internal incident report",
        "purpose": "Record the reported incident",
        "audience": "internal",
        "recipient_type": "school_leadership",
    }
    contract = build_user_intent_contract(
        "Prepare the report and a chronology.",
        {"situation": {"requested_outputs": [semantic_output]}},
        source_outputs=[dict(semantic_output)],
        custom_deliverables=[{
            "artifact_role": "school_document",
            "label": "Minute-by-minute chronology",
            "purpose": "Preserve the event sequence",
            "audience": "internal",
            "recipient_type": "investigation_team",
        }],
    )

    assert len(contract["obligations"]) == 2
    assert contract["obligations"][0]["source"] == "semantic_requested_output"
    assert contract["obligations"][1]["source"] == "custom_deliverable"
    assert contract["obligations"][1]["label"] == "Minute-by-minute chronology"


def test_bilingual_request_is_preserved_for_every_output_without_language_field():
    contract = build_user_intent_contract(
        "Draft a bilingual notice in English and Malay for all parents.",
        {
            "audience": "school_community",
            "situation": {
                "requested_outputs": [{
                    "artifact_role": "school_parent_notice",
                    "label": "Recycling Day notice",
                    "purpose": "Inform all parents",
                }]
            },
        },
    )

    obligation = contract["obligations"][0]
    assert obligation["languages"] == ["en", "ms"]
    assert obligation["audience"] == "school_community"
    assert obligation["recipient_type"] == "school_community"


def test_explicit_audience_recipient_and_source_are_not_closed_or_rewritten():
    contract = build_user_intent_contract(
        "Prepare an invitation for the visiting university research team.",
        {},
        source_outputs=[{
            "artifact_role": "school_document",
            "label": "Research team invitation",
            "purpose": "Invite the team to the school briefing",
            "audience": "private_recipient",
            "recipient_type": "visiting_university_research_team",
            "languages": ["en"],
            "explicit": True,
            "source": "source_phrase_parser",
        }],
    )

    obligation = contract["obligations"][0]
    assert obligation["audience"] == "private_recipient"
    assert obligation["recipient_type"] == "visiting_university_research_team"
    assert obligation["languages"] == ["en"]
    assert obligation["explicit"] is True
    assert obligation["source"] == "source_phrase_parser"


def test_one_generic_artifact_cannot_cover_two_same_role_obligations():
    contract = build_user_intent_contract(
        "Prepare an intake log and handover checklist.",
        {"situation": {"requested_outputs": [
            {"artifact_role": "school_document", "label": "Asset intake log"},
            {"artifact_role": "school_document", "label": "Handover checklist"},
        ]}},
    )

    coverage = align_deliverables_to_contract(
        contract,
        [{
            "deliverable_id": "asset_intake_log",
            "artifact_role": "school_document",
            "label": "Asset intake log",
        }],
    )

    assert coverage["pass"] is False
    assert len(coverage["covered"]) == 1
    assert coverage["covered"][0]["obligation"]["label"] == "Asset intake log"
    assert [item["label"] for item in coverage["missing"]] == [
        "Handover checklist"
    ]


def test_explicit_links_win_and_extra_artifacts_are_reported_not_hidden():
    contract = build_user_intent_contract(
        "Prepare two logs.",
        {"situation": {"requested_outputs": [
            {"artifact_role": "school_document", "label": "Intake log"},
            {"artifact_role": "school_document", "label": "Return log"},
        ]}},
    )
    intake, returns = contract["obligations"]

    coverage = align_deliverables_to_contract(contract, [
        {
            "deliverable_id": "return_log.md",
            "artifact_role": "school_document",
            "source_obligation_id": returns["obligation_id"],
        },
        {
            "deliverable_id": "intake_log.md",
            "artifact_role": "school_document",
            "intent_obligation_id": intake["obligation_id"],
        },
        {
            "deliverable_id": "bonus_public_post.md",
            "artifact_role": "public_communication_draft",
        },
        {
            "deliverable_id": "external_release_guard",
            "artifact_role": "external_release_gate",
        },
    ])

    assert coverage["pass"] is True
    assert coverage["covered_obligation_ids"] == [
        returns["obligation_id"],
        intake["obligation_id"],
    ]
    assert coverage["unrequested_deliverable_ids"] == ["bonus_public_post.md"]
    assert coverage["ignored_governance"] == [{
        "deliverable_id": "external_release_guard",
        "artifact_role": "external_release_gate",
    }]


def test_single_semantic_speech_is_explicit_without_literal_label_match():
    contract = build_user_intent_contract(
        "Write a short speech for the headmaster to deliver at the annual "
        "prize giving ceremony.",
        {
            "requested_action": "draft",
            "requested_outputs": [{
                "artifact_role": "school_document",
                "label": "Headmaster's speech for prize giving ceremony",
                "purpose": "Provide the requested speech",
            }],
        },
    )

    assert contract["explicit_count"] == 1
    assert contract["obligations"][0]["explicit"] is True


def test_single_open_ended_semantic_suggestion_is_not_explicit():
    contract = build_user_intent_contract(
        "A guest cancelled. Prepare whatever response package is appropriate.",
        {
            "requested_action": "prepare",
            "requested_outputs": [{
                "artifact_role": "event_action_plan",
                "label": "Event response plan",
                "purpose": "Coordinate an appropriate response",
            }],
        },
    )

    assert contract["explicit_count"] == 0
    assert contract["obligations"][0]["explicit"] is False


def test_provider_explicit_false_is_respected_for_single_candidate():
    contract = build_user_intent_contract(
        "Help the school decide how to handle the situation.",
        {
            "requested_action": "prepare",
            "requested_outputs": [{
                "artifact_role": "school_document",
                "label": "Suggested coordination note",
                "explicit": False,
            }],
        },
    )

    assert contract["explicit_count"] == 0


def test_provider_explicit_true_is_not_first_party_request_evidence():
    contract = build_user_intent_contract(
        "Prepare a teacher duty roster for sports day on 25 October.",
        {
            "requested_action": "draft",
            "requested_outputs": [
                {
                    "artifact_role": "event_action_plan",
                    "label": "Sports day event action plan",
                    "explicit": True,
                },
                {
                    "artifact_role": "duty_roster",
                    "label": "Teacher duty roster",
                    "explicit": True,
                },
            ],
        },
    )

    by_role = {
        item["artifact_role"]: item for item in contract["obligations"]
    }
    assert by_role["event_action_plan"]["explicit"] is False
    assert by_role["duty_roster"]["explicit"] is True
    assert contract["explicit_count"] == 1
