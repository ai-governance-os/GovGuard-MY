from copy import deepcopy
from pathlib import Path

from teow_agl.modules.module_school_input_semantics import SchoolInputSemantics
from teow_agl.modules.module_school_intent_contract import (
    build_user_intent_contract,
    declared_output_cardinality,
    evaluate_deliverable_coverage,
)
from teow_agl.modules.module_school_situation import SchoolSituationCompiler
from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.models import TaskEnvelope


ROOT = Path(__file__).resolve().parents[1]
POLICY = (
    ROOT / "configs" / "domain_packs" / "public_school" /
    "situation_response_policy.json"
)


class _SequenceLLM:
    backend = "fake"

    def __init__(self, *payloads: dict) -> None:
        self.payloads = [deepcopy(item) for item in payloads]
        self.calls = 0

    def chat_json(self, system: str, user: str, *, max_tokens: int) -> dict:
        self.calls += 1
        index = min(self.calls - 1, len(self.payloads) - 1)
        return deepcopy(self.payloads[index])


def _output(label: str) -> dict:
    return {
        "artifact_role": "school_document",
        "label": label,
        "purpose": f"Maintain {label.casefold()}",
        "audience": "internal",
        "recipient_type": "school_staff",
        "languages": ["en"],
        "source_fact_ids": [],
    }


def _semantic_payload(outputs: list[dict]) -> dict:
    return {
        "school_domain": True,
        "case_relation": "new_case",
        "school_area": "general_admin",
        "requested_action": "prepare records",
        "audience": "internal",
        "confidence": 0.98,
        "rationale": "Explicit school-administration records are requested.",
        "situation": {
            "family": "general_school_admin",
            "phase": "planned",
            "severity": "low",
            "signals": [],
            "affected_people_types": ["staff"],
            "stakeholder_candidates": ["school_staff"],
            "case_summary": "The school is preparing administrative records.",
            "known_facts": [],
            "unknowns": [],
            "requested_outputs": outputs,
            "explicit_external_actions": [],
        },
    }


def test_declared_output_cardinality_is_conservative_and_bounded() -> None:
    assert declared_output_cardinality(
        "Prepare five separate logs for the school inventory exercise."
    ) == 5
    assert declared_output_cardinality(
        "Create 12 Markdown files for twelve classroom stations."
    ) == 12
    assert declared_output_cardinality(
        "Four pupils returned two library books each. Prepare one summary."
    ) == 1


def test_declared_single_malay_notice_collapses_semantic_duplicate() -> None:
    text = (
        "Sediakan satu surat makluman kepada semua penjaga tentang cuti "
        "tambahan sekolah pada hari Isnin."
    )
    community = {
        **_output("surat makluman kepada semua penjaga"),
        "artifact_role": "school_parent_notice",
        "audience": "school_community",
        "recipient_type": "school_community",
        "languages": ["ms"],
    }
    private = {
        **_output("surat peribadi kepada penjaga"),
        "artifact_role": "private_parent_notice",
        "audience": "private_recipient",
        "recipient_type": "guardian",
        "languages": ["ms"],
    }
    llm = _SequenceLLM(_semantic_payload([community, private]))

    result = SchoolInputSemantics(llm).classify(text)
    outputs = result["situation"]["requested_outputs"]

    assert llm.calls == 1
    assert result["situation"]["declared_output_count"] == 1
    assert [item["artifact_role"] for item in outputs] == [
        "school_parent_notice"
    ]
    contract = build_user_intent_contract(text, result)
    assert contract["explicit_count"] == 1
    assert len(contract["obligations"]) == 1


def test_declared_single_private_parent_letter_keeps_private_role() -> None:
    text = (
        "Sediakan satu surat peribadi kepada penjaga murid tersebut tentang "
        "pertemuan sokongan."
    )
    community = {
        **_output("surat makluman sekolah"),
        "artifact_role": "school_parent_notice",
        "audience": "school_community",
        "recipient_type": "school_community",
        "languages": ["ms"],
    }
    private = {
        **_output("surat peribadi kepada penjaga murid tersebut"),
        "artifact_role": "private_parent_notice",
        "audience": "private_recipient",
        "recipient_type": "guardian",
        "languages": ["ms"],
    }
    result = SchoolInputSemantics(
        _SequenceLLM(_semantic_payload([community, private]))
    ).classify(text)

    assert [
        item["artifact_role"]
        for item in result["situation"]["requested_outputs"]
    ] == ["private_parent_notice"]


def test_collapsed_semantic_outputs_receive_one_provider_neutral_repair() -> None:
    text = (
        "For the school library exercise, prepare four separate Markdown "
        "files: an accession log, a loan log, a return log, and a damage log."
    )
    collapsed = _semantic_payload([_output("Combined library record")])
    repaired_outputs = [
        _output("Accession log"),
        _output("Loan log"),
        _output("Return log"),
        _output("Damage log"),
    ]
    llm = _SequenceLLM(collapsed, {"requested_outputs": repaired_outputs})

    result = SchoolInputSemantics(llm).classify(text)

    situation = result["situation"]
    assert llm.calls == 2
    assert len(situation["requested_outputs"]) == 4
    assert situation["declared_output_count"] == 4
    assert situation["output_contract_complete"] is True
    assert situation["output_contract_status"] == "complete_after_repair"
    assert all(
        item["declared_slot_explicit"] is True
        for item in situation["requested_outputs"]
    )
    contract = build_user_intent_contract(text, result)
    assert contract["explicit_count"] == 4
    assert contract["cardinality_complete"] is True


def test_persistently_collapsed_list_cannot_pass_intent_coverage() -> None:
    text = (
        "Prepare four distinct documents for the school stocktake: intake, "
        "issue, return, and discrepancy records."
    )
    collapsed = _semantic_payload([_output("Combined stocktake document")])
    llm = _SequenceLLM(collapsed, {
        "requested_outputs": [_output("Combined stocktake document")]
    })

    result = SchoolInputSemantics(llm).classify(text)
    contract = build_user_intent_contract(text, result)
    only_obligation = contract["obligations"][0]
    coverage = evaluate_deliverable_coverage(contract, [{
        "deliverable_id": "combined_stocktake.md",
        "artifact_role": "school_document",
        "source_obligation_id": only_obligation["obligation_id"],
    }])

    assert llm.calls == 2
    assert result["situation"]["output_contract_complete"] is False
    assert contract["declared_output_count"] == 4
    assert contract["cardinality_gap"] == 3
    assert contract["cardinality_complete"] is False
    assert coverage["missing"] == []
    assert coverage["cardinality_complete"] is False
    assert coverage["pass"] is False


def test_final_verifier_consumes_incomplete_intent_cardinality() -> None:
    envelope = TaskEnvelope(
        task_id="cardinality_gap",
        session_id="s1",
        user_id="u1",
        raw_goal="Prepare four separate school files.",
        normalized_goal="Prepare four separate school files.",
        metadata={"school_response_pack": {
            "deliverables": [],
            "intent_coverage": {
                "pass": False,
                "cardinality_complete": False,
                "cardinality_gap": 3,
            },
        }},
    )

    coverage = VerifierModule._response_pack_coverage(
        envelope=envelope,
        plan_actions=[],
        executions=[],
        governance_decisions=[],
    )

    assert coverage["active"] is True
    assert coverage["complete"] is False
    assert coverage["incomplete_deliverable_ids"] == [
        "intent_contract_cardinality"
    ]
    assert coverage["items"][0]["status"] == "MISSING"


def test_complete_initial_semantics_do_not_spend_a_repair_call() -> None:
    text = (
        "Prepare three individual files for the school club: a register, "
        "an attendance sheet, and an equipment checklist."
    )
    llm = _SequenceLLM(_semantic_payload([
        _output("Club register"),
        _output("Attendance sheet"),
        _output("Equipment checklist"),
    ]))

    result = SchoolInputSemantics(llm).classify(text)

    assert llm.calls == 1
    assert result["situation"]["output_contract_status"] == "complete"
    assert len(result["situation"]["requested_outputs"]) == 3


def test_compiler_preserves_all_twelve_same_role_output_slots() -> None:
    text = "Prepare twelve separate Markdown files for the classroom inventory."
    outputs = [_output(f"Classroom inventory {index:02d}") for index in range(1, 13)]
    semantics = {
        "checked": True,
        "school_domain": True,
        "case_relation": "new_case",
        "school_area": "general_admin",
        "requested_action": "prepare inventory files",
        "audience": "internal",
        "confidence": 0.98,
        "data_use_concepts": [],
        "source": "test_semantic_llm",
        "situation": _semantic_payload(outputs)["situation"],
    }

    compiled = SchoolSituationCompiler(POLICY).compile(text, semantics)
    contract = compiled["response_pack"]["intent_contract"]
    selected = [
        item for item in compiled["response_pack"]["deliverables"]
        if item.get("selected") is True and item.get("kind") == "artifact"
        and item.get("source_obligation_id")
    ]

    assert len(contract["obligations"]) == 12
    assert contract["explicit_count"] == 12
    assert contract["cardinality_complete"] is True
    assert len(selected) == 12
    assert len({item["source_obligation_id"] for item in selected}) == 12
    assert compiled["response_pack"]["intent_coverage"]["pass"] is True
