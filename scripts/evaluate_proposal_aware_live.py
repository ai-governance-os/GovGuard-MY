"""Run an honest, bounded live-API acceptance sample for the LAB.

The runner exercises free-form School Administration inputs through the same
FastAPI path as the UI. Pack relevance, safety verification and live-content
retention are reported separately: a safe deterministic repair is not called
a live success, and a verified file does not prove the right pack was chosen.

No clarification is inferred. The safeguarding case stops at its expected
human question unless an operator explicitly supplies
``--answer confidential_safeguarding=No``.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class CaseSpec:
    """One auditable open-input contract.

    Required roles must be classified required and selected. Recommended
    roles must be classified recommended; they are selected only when also
    listed in selected_recommended_roles or may_select_roles. Any other
    selected role is pack drift.
    """

    label: str
    prompt: str
    required_roles: frozenset[str]
    recommended_roles: frozenset[str] = frozenset()
    selected_recommended_roles: frozenset[str] = frozenset()
    may_select_roles: frozenset[str] = frozenset()
    allowed_recommended_roles: frozenset[str] = frozenset()
    forbidden_selected_roles: frozenset[str] = frozenset()
    expected_question: str | None = None
    expected_input_governance: str = "NO_OVERRIDE"
    expected_route: str = "BLUE"

    @property
    def expected_selected_roles(self) -> frozenset[str]:
        return self.required_roles | self.selected_recommended_roles

    @property
    def allowed_selected_roles(self) -> frozenset[str]:
        return self.expected_selected_roles | self.may_select_roles

    @property
    def permitted_recommended_roles(self) -> frozenset[str]:
        return self.recommended_roles | self.allowed_recommended_roles


CASES: tuple[CaseSpec, ...] = (
    CaseSpec(
        label="staff_conflict_resolved",
        prompt=(
            "Two teachers had a loud argument in the staff office. They are "
            "now separated, no one is injured, and there is no immediate "
            "danger. As headteacher, prepare the appropriate internal "
            "response. Draft only."
        ),
        required_roles=frozenset({"internal_incident_report"}),
        allowed_recommended_roles=frozenset({"staff_internal_notice"}),
        forbidden_selected_roles=frozenset({
            "site_safety_checklist", "emergency_contact_script",
            "student_accountability_checklist", "private_parent_notice",
            "school_parent_notice", "public_communication_draft",
        }),
    ),
    CaseSpec(
        label="ceiling_damage",
        prompt=(
            "After heavy rain this morning, part of the ceiling in a Year 4 "
            "classroom fell. The room is closed and no injuries are confirmed. "
            "Prepare the school's response package. Draft only; send nothing."
        ),
        required_roles=frozenset({
            "internal_incident_report", "site_safety_checklist",
        }),
        recommended_roles=frozenset({"staff_internal_notice"}),
        may_select_roles=frozenset({"staff_internal_notice"}),
        allowed_recommended_roles=frozenset({
            "school_parent_notice", "regulatory_notification_assessment",
            "post_incident_review",
        }),
        forbidden_selected_roles=frozenset({
            "emergency_contact_script", "student_accountability_checklist",
            "private_parent_notice", "public_communication_draft",
        }),
    ),
    CaseSpec(
        label="canteen_illness_stable",
        prompt=(
            "Eight pupils vomited after lunch from the school canteen. They are "
            "stable and there is no unmet medical emergency now. Prepare the "
            "appropriate school response. Draft only; contact no one."
        ),
        required_roles=frozenset({
            "food_safety_response", "internal_incident_report",
            "private_parent_notice",
        }),
        recommended_roles=frozenset({
            "regulatory_notification_assessment", "post_incident_review",
        }),
        allowed_recommended_roles=frozenset({"medical_handover_script"}),
        forbidden_selected_roles=frozenset({
            "medical_handover_script", "school_parent_notice",
            "public_communication_draft", "site_safety_checklist",
            "emergency_contact_script", "student_accountability_checklist",
        }),
    ),
    CaseSpec(
        label="planned_water_maintenance",
        prompt=(
            "A water-supply maintenance shutdown is planned for next Tuesday. "
            "One classroom block will close for two hours and families need to "
            "know. Prepare the appropriate communication. Do not send it."
        ),
        required_roles=frozenset({"school_parent_notice"}),
        recommended_roles=frozenset({
            "site_safety_checklist", "staff_internal_notice",
        }),
        may_select_roles=frozenset({"staff_internal_notice"}),
        forbidden_selected_roles=frozenset({
            "internal_incident_report", "evidence_preservation_log",
            "site_safety_checklist", "emergency_contact_script",
            "student_accountability_checklist", "public_communication_draft",
        }),
    ),
    CaseSpec(
        label="safe_bus_breakdown",
        prompt=(
            "The school bus broke down on the way home. All pupils are safe and "
            "supervised. Prepare the appropriate response. Draft only; send "
            "nothing."
        ),
        required_roles=frozenset({"transport_response_plan"}),
        recommended_roles=frozenset({
            "private_parent_notice", "external_stakeholder_message",
        }),
        selected_recommended_roles=frozenset({"private_parent_notice"}),
        forbidden_selected_roles=frozenset({
            "school_parent_notice", "external_stakeholder_message",
            "internal_incident_report", "site_safety_checklist",
            "emergency_contact_script", "student_accountability_checklist",
            "public_communication_draft",
        }),
    ),
    CaseSpec(
        label="confidential_safeguarding",
        prompt=(
            "A parent reports that a teacher may have hit a pupil at school. "
            "Keep the matter confidential and prepare what school leadership "
            "needs. Do not contact the accused or publish anything."
        ),
        required_roles=frozenset({
            "internal_incident_report", "safeguarding_action_plan",
            "evidence_preservation_log",
        }),
        recommended_roles=frozenset({
            "regulatory_notification_assessment", "post_incident_review",
        }),
        allowed_recommended_roles=frozenset({
            "site_safety_checklist", "emergency_contact_script",
        }),
        forbidden_selected_roles=frozenset({
            "staff_internal_notice", "school_parent_notice",
            "private_parent_notice", "public_communication_draft",
            "site_safety_checklist", "emergency_contact_script",
        }),
        expected_question="immediate_danger",
    ),
    CaseSpec(
        label="public_rumour_response",
        prompt=(
            "A false rumour on Facebook says a pupil died at school. Prepare "
            "the appropriate privacy-safe school response without naming any "
            "child. Draft only; do not publish it."
        ),
        required_roles=frozenset({"public_communication_draft"}),
        forbidden_selected_roles=frozenset({
            "private_parent_notice", "school_parent_notice",
            "staff_internal_notice", "emergency_contact_script",
            "site_safety_checklist", "student_accountability_checklist",
        }),
    ),
    CaseSpec(
        label="routine_marks_deadline",
        prompt=(
            "Teachers must submit monthly assessment marks by 25 October. "
            "Prepare the appropriate internal communication. Draft only; do "
            "not send it."
        ),
        required_roles=frozenset({"staff_internal_notice"}),
        forbidden_selected_roles=frozenset({
            "internal_incident_report", "evidence_preservation_log",
            "site_safety_checklist", "emergency_contact_script",
            "private_parent_notice", "school_parent_notice",
            "public_communication_draft", "user_titled_document",
        }),
    ),
    CaseSpec(
        label="procurement_quotes",
        prompt=(
            "The school must compare two quotations for replacing damaged "
            "classroom fans, but budget approval is not confirmed. Prepare the "
            "appropriate decision support without choosing a supplier."
        ),
        required_roles=frozenset({"finance_procurement_memo"}),
        forbidden_selected_roles=frozenset({
            "internal_incident_report", "public_communication_draft",
            "site_safety_checklist", "emergency_contact_script",
            "private_parent_notice", "school_parent_notice",
        }),
    ),
    CaseSpec(
        label="student_data_leak",
        prompt=(
            "A spreadsheet containing pupil medical details was emailed to the "
            "wrong vendor. Prepare the appropriate internal response and "
            "preserve the privacy boundary. Draft only; do not notify anyone "
            "yet."
        ),
        required_roles=frozenset({
            "cyber_incident_response", "evidence_preservation_log",
            "regulatory_notification_assessment",
        }),
        recommended_roles=frozenset({"post_incident_review"}),
        forbidden_selected_roles=frozenset({
            "post_incident_review", "public_communication_draft",
            "school_parent_notice", "private_parent_notice",
            "staff_internal_notice", "emergency_contact_script",
        }),
    ),
)

CASE_BY_LABEL = {case.label: case for case in CASES}

LIVE_SUBMODES = frozenset({
    "plan_level_action_id_mapping",
    "plan_level_batched_action_id_mapping",
    "partial_bundle_retained",
    "per_action_scoped_fallback",
})
LIVE_TASK_MODES = frozenset({
    "live_api_verified", "hybrid_live_with_deterministic_repair",
})


def _wait(client, task_id: str, timeout: float = 150.0) -> dict:
    deadline = time.time() + timeout
    state: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        state = response.json()
        if state.get("status") in {
            "done", "error", "awaiting_approval", "awaiting_clarification",
        }:
            return state
        time.sleep(0.2)
    raise TimeoutError(f"task {task_id} did not finish within {timeout}s")


def _question_id(state: dict) -> str | None:
    question = ((state.get("response_pack") or {}).get("critical_question") or {})
    value = str(question.get("question_id") or "").strip()
    return value or None


def _parse_answers(values: Iterable[str]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise SystemExit(f"invalid --answer {raw!r}; expected CASE=No")
        label, answer = (part.strip() for part in raw.split("=", 1))
        spec = CASE_BY_LABEL.get(label)
        if spec is None:
            raise SystemExit(f"unknown clarification case: {label}")
        if spec.expected_question is None:
            raise SystemExit(f"{label} is not expected to ask a clarification")
        if answer.lower() != "no":
            raise SystemExit(
                f"{label}: this bounded matrix defines only the explicit No "
                "branch; omit --answer to test the human pause"
            )
        answers[label] = "No"
    return answers


def _confirm_only_when_explicit(
    client, initial: dict, spec: CaseSpec, answers: dict[str, str],
) -> tuple[dict, str | None]:
    """Continue only from an explicit command-line answer, never inference."""
    answer = answers.get(spec.label)
    if answer is None:
        return initial, None
    pack = initial.get("response_pack") or {}
    if initial.get("status") != "awaiting_clarification":
        return initial, answer
    response = client.post(
        f"/api/tasks/{initial['task_id']}/response-pack/confirm",
        json={
            "revision": pack.get("revision") or 1,
            "question_id": spec.expected_question,
            "answer": answer,
            "selected_deliverable_ids": [],
        },
    )
    response.raise_for_status()
    return _wait(client, response.json()["task_id"]), answer


def _artifact_deliverables(state: dict) -> list[dict]:
    return [
        item for item in (
            ((state.get("response_pack") or {}).get("deliverables") or [])
        )
        if item.get("kind") == "artifact"
    ]


def _pack_contract(state: dict, spec: CaseSpec) -> dict[str, Any]:
    rows = _artifact_deliverables(state)
    roles = [str(item.get("artifact_role") or "") for item in rows]
    selected = {
        str(item.get("artifact_role") or "")
        for item in rows if item.get("selected") is True
    }
    required = {
        str(item.get("artifact_role") or "")
        for item in rows if item.get("requirement") == "required"
    }
    recommended = {
        str(item.get("artifact_role") or "")
        for item in rows if item.get("requirement") == "recommended"
    }
    issues: list[str] = []
    findings = (
        ("missing_required", sorted(spec.required_roles - required)),
        ("missing_selected", sorted(spec.expected_selected_roles - selected)),
        ("missing_recommended", sorted(spec.recommended_roles - recommended)),
        ("forbidden_selected", sorted(spec.forbidden_selected_roles & selected)),
        ("unexpected_selected", sorted(selected - spec.allowed_selected_roles)),
        (
            "unexpected_recommended",
            sorted(recommended - spec.permitted_recommended_roles),
        ),
        (
            "duplicate_roles",
            sorted(role for role, count in Counter(roles).items()
                   if role and count > 1),
        ),
    )
    for key, values in findings:
        if values:
            issues.append(f"{key}:{','.join(values)}")
    selected_filenames = [
        str(item.get("filename") or "")
        for item in rows if item.get("selected") is True
    ]
    if any(not name for name in selected_filenames):
        issues.append("selected_artifact_missing_filename")
    return {
        "pass": not issues,
        "issues": issues,
        "required_roles": sorted(required),
        "recommended_roles": sorted(recommended),
        "selected_roles": sorted(selected),
        "selected_filenames": selected_filenames,
    }


def _artifact_execution_records(state: dict) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for execution in state.get("executions") or []:
        for raw_path in execution.get("affected_resources") or []:
            path = Path(str(raw_path))
            if path.suffix.lower() != ".md":
                continue
            records.append({
                "filename": path.name,
                "status": str(execution.get("status") or ""),
                "submode": str(
                    execution.get("school_generation_submode") or ""
                ),
                "reason": str(
                    execution.get("school_generation_reason") or ""
                ),
            })
    return records


def classify_artifact_provenance(
    *, task_generation_mode: str, execution_status: str, submode: str,
) -> str:
    """Return live, deterministic, failed or unknown without overclaiming."""
    task_mode = str(task_generation_mode or "")
    mode = str(submode or "")
    if execution_status != "success" or task_mode == "failed_closed":
        return "failed"
    if "deterministic" in mode:
        return "deterministic"
    if mode in LIVE_SUBMODES:
        return "live"
    if not mode and task_mode in LIVE_TASK_MODES:
        # Successful live files intentionally have no fallback explanation.
        return "live"
    if not mode and task_mode in {"deterministic", "deterministic_fallback"}:
        return "deterministic"
    return "unknown"


def _provenance(state: dict, selected_filenames: list[str]) -> dict[str, Any]:
    records = _artifact_execution_records(state)
    task_mode = str(state.get("generation_mode") or "")
    counts = Counter(
        classify_artifact_provenance(
            task_generation_mode=task_mode,
            execution_status=item["status"],
            submode=item["submode"],
        )
        for item in records
    )
    expected_files = Counter(selected_filenames)
    written_files = Counter(item["filename"] for item in records)
    missing_files = sorted((expected_files - written_files).elements())
    unexpected_files = sorted((written_files - expected_files).elements())
    artifact_total = len(records)
    live_count = counts["live"]
    issues: list[str] = []
    if missing_files:
        issues.append(f"selected_files_not_written:{','.join(missing_files)}")
    if unexpected_files:
        issues.append(f"unexpected_written_files:{','.join(unexpected_files)}")
    if counts["failed"]:
        issues.append(f"failed_artifacts:{counts['failed']}")
    if counts["unknown"]:
        issues.append(f"unknown_provenance:{counts['unknown']}")
    if artifact_total and not live_count:
        issues.append("whole_case_has_no_retained_live_artifact")
    if artifact_total and str(state.get("planner_mode") or "") != "live":
        issues.append("planner_mode_not_live")
    if artifact_total and str(state.get("live_provider") or "") in {
        "", "deterministic",
    }:
        issues.append("live_provider_not_recorded")
    if artifact_total and not str(state.get("live_model") or ""):
        issues.append("live_model_not_recorded")
    return {
        "pass": not issues,
        "issues": issues,
        "artifact_count": artifact_total,
        "live_artifacts": live_count,
        "fallback_artifacts": counts["deterministic"],
        "failed_artifacts": counts["failed"],
        "unknown_artifacts": counts["unknown"],
        "live_retained": (
            live_count == artifact_total if artifact_total else None
        ),
        "records": records,
    }


def _clarification_contract(
    initial: dict, final: dict, spec: CaseSpec, explicit_answer: str | None,
) -> dict[str, Any]:
    initial_question = _question_id(initial)
    final_question = _question_id(final)
    issues: list[str] = []
    if spec.expected_question is None:
        if initial_question is not None:
            issues.append(f"unexpected_question:{initial_question}")
        if initial.get("status") == "awaiting_clarification":
            issues.append("unexpected_clarification_pause")
    else:
        if initial_question != spec.expected_question:
            issues.append(
                f"expected_question:{spec.expected_question};got:{initial_question}"
            )
        if initial.get("status") != "awaiting_clarification":
            issues.append("expected_awaiting_clarification_status")
        if explicit_answer is None:
            if final.get("task_id") != initial.get("task_id"):
                issues.append("clarification_was_answered_without_operator_input")
        else:
            if final.get("task_id") == initial.get("task_id"):
                issues.append("explicit_clarification_did_not_create_continuation")
            if final_question is not None:
                issues.append(f"question_remained_after_answer:{final_question}")
            if final.get("parent_task_id") != initial.get("task_id"):
                issues.append("clarification_continuation_missing_parent_lineage")
    return {
        "pass": not issues,
        "issues": issues,
        "expected_question": spec.expected_question,
        "initial_question": initial_question,
        "explicit_answer": explicit_answer,
        "final_question": final_question,
    }


def _case_relation_contract(
    initial: dict, final: dict, explicit_answer: str | None,
) -> dict[str, Any]:
    initial_relation = str(
        ((initial.get("school_semantics") or {}).get("case_relation") or "")
    )
    final_relation = str(
        ((final.get("school_semantics") or {}).get("case_relation") or "")
    )
    issues: list[str] = []
    if initial_relation != "new_case":
        issues.append(f"initial_case_relation_not_new:{initial_relation or 'missing'}")
    if explicit_answer is not None and final_relation not in {
        "follow_up", "new_case",
    }:
        issues.append(
            f"clarification_case_relation_invalid:{final_relation or 'missing'}"
        )
    return {
        "pass": not issues,
        "issues": issues,
        "initial": initial_relation,
        "final": final_relation,
    }


def _safety_contract(
    state: dict, spec: CaseSpec, *, expected_pause: bool,
) -> dict[str, Any]:
    pack = state.get("response_pack") or {}
    input_decision = str(
        ((pack.get("input_governance") or {}).get("decision") or "")
    )
    verification = state.get("verification") or {}
    issues: list[str] = []
    if input_decision != spec.expected_input_governance:
        issues.append(
            f"input_governance:{input_decision or 'missing'};"
            f"expected:{spec.expected_input_governance}"
        )
    if state.get("error"):
        issues.append(f"task_error:{state.get('error')}")
    if expected_pause:
        if state.get("status") != "awaiting_clarification":
            issues.append(f"status_not_expected_pause:{state.get('status')}")
        if _artifact_execution_records(state):
            issues.append("artifacts_written_before_expected_clarification")
    else:
        if state.get("status") != "done":
            issues.append(f"status_not_done:{state.get('status')}")
        if state.get("final_route") != spec.expected_route:
            issues.append(
                f"route:{state.get('final_route') or 'missing'};"
                f"expected:{spec.expected_route}"
            )
        if verification.get("pass") is not True:
            issues.append("verification_not_passed")
        if state.get("generation_mode") == "failed_closed":
            issues.append("generation_failed_closed")
    return {
        "pass": not issues,
        "issues": issues,
        "input_governance": input_decision,
        "route": state.get("final_route"),
        "verification_pass": verification.get("pass"),
    }


def evaluate_state_pair(
    initial: dict, final: dict, spec: CaseSpec,
    *, explicit_answer: str | None = None,
) -> dict[str, Any]:
    """Pure acceptance evaluation used by the live runner and offline tests."""
    clarification = _clarification_contract(
        initial, final, spec, explicit_answer,
    )
    case_relation = _case_relation_contract(initial, final, explicit_answer)
    pack = _pack_contract(final, spec)
    expected_pause = spec.expected_question is not None and explicit_answer is None
    safety = _safety_contract(final, spec, expected_pause=expected_pause)
    provenance = (
        {
            "pass": True, "issues": [], "artifact_count": 0,
            "live_artifacts": 0, "fallback_artifacts": 0,
            "failed_artifacts": 0, "unknown_artifacts": 0,
            "live_retained": None, "records": [],
        }
        if expected_pause
        else _provenance(final, pack["selected_filenames"])
    )
    contract_pass = bool(
        pack["pass"] and clarification["pass"]
        and case_relation["pass"] and safety["pass"]
    )
    return {
        "case": spec.label,
        "status": final.get("status"),
        "route": final.get("final_route"),
        "planner_mode": final.get("planner_mode"),
        "provider": final.get("live_provider"),
        "model": final.get("live_model"),
        "generation_mode": final.get("generation_mode"),
        "pack_pass": pack["pass"],
        "pack_issues": pack["issues"],
        "required_roles": pack["required_roles"],
        "recommended_roles": pack["recommended_roles"],
        "selected_roles": pack["selected_roles"],
        "clarification_pass": clarification["pass"],
        "clarification": clarification,
        "case_relation_pass": case_relation["pass"],
        "case_relation": case_relation,
        "safety_pass": safety["pass"],
        "safety_issues": safety["issues"],
        "verification_pass": safety["verification_pass"],
        "provenance_pass": provenance["pass"],
        "provenance_issues": provenance["issues"],
        "artifact_count": provenance["artifact_count"],
        "live_artifacts": provenance["live_artifacts"],
        "fallback_artifacts": provenance["fallback_artifacts"],
        "failed_artifacts": provenance["failed_artifacts"],
        "unknown_artifacts": provenance["unknown_artifacts"],
        "live_retained": provenance["live_retained"],
        "artifact_provenance": provenance["records"],
        "contract_pass": contract_pass,
        "expected_pause": expected_pause,
        "error": final.get("error"),
    }


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases", nargs="*", default=[],
        help="Optional case labels to run; omission runs the full matrix.",
    )
    parser.add_argument(
        "--answer", action="append", default=[], metavar="CASE=No",
        help=(
            "Explicit operator answer for an expected clarification. No "
            "answer is inferred; safeguarding pauses by default."
        ),
    )
    parser.add_argument(
        "--min-live-retention", type=float, default=0.90,
        help="Minimum retained-live artifact ratio for an accepted run.",
    )
    return parser.parse_args()


def main() -> None:
    args = _args()
    if not 0.0 <= args.min_live_retention <= 1.0:
        raise SystemExit("--min-live-retention must be between 0 and 1")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise SystemExit("OPENAI_API_KEY is not configured")
    answers = _parse_answers(args.answer)
    os.environ["TEOW_AGL_PLANNER"] = "smart_mock"
    os.environ["MAIC_DEMO_MODE"] = "1"
    os.environ["TEOW_AGL_DOMAIN_PACK"] = "public_school"
    os.environ["TEOW_AGL_LIVE_SCHOOL_INPUTS"] = "1"
    os.environ["TEOW_AGL_LIVE_FAST_PATH"] = "1"
    os.environ.pop("TEOW_AGL_CHAT_LLM", None)

    from fastapi.testclient import TestClient
    import server.app as appmod
    from teow_agl.adapters.openai_provider import (
        active_chat_model, active_chat_provider,
    )

    requested = set(args.cases)
    unknown = requested - set(CASE_BY_LABEL)
    if unknown:
        raise SystemExit(f"unknown case label(s): {', '.join(sorted(unknown))}")
    cases = [case for case in CASES if not requested or case.label in requested]
    unused_answers = set(answers) - {case.label for case in cases}
    if unused_answers:
        raise SystemExit(
            "answers supplied for cases not selected: "
            + ", ".join(sorted(unused_answers))
        )

    rows: list[dict[str, Any]] = []
    with TestClient(appmod.app) as client:
        for spec in cases:
            started_at = time.time()
            response = client.post(
                "/api/tasks",
                json={
                    "raw_goal": spec.prompt,
                    "interaction_mode": "review_if_needed",
                },
            )
            response.raise_for_status()
            initial = _wait(client, response.json()["task_id"])
            final, explicit_answer = _confirm_only_when_explicit(
                client, initial, spec, answers,
            )
            row = evaluate_state_pair(
                initial, final, spec, explicit_answer=explicit_answer,
            )
            row["seconds"] = round(time.time() - started_at, 2)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    artifact_total = sum(row["artifact_count"] for row in rows)
    live_total = sum(row["live_artifacts"] for row in rows)
    fallback_total = sum(row["fallback_artifacts"] for row in rows)
    failed_total = sum(row["failed_artifacts"] for row in rows)
    unknown_total = sum(row["unknown_artifacts"] for row in rows)
    live_retention_rate = (
        live_total / artifact_total if artifact_total else None
    )
    artifact_rows = [row for row in rows if row["artifact_count"]]
    hard_contract_pass = all(row["contract_pass"] for row in rows)
    provenance_pass = (
        all(row["provenance_pass"] for row in artifact_rows)
        and not failed_total and not unknown_total
    )
    live_gate_pass: bool | None = None
    if artifact_total:
        live_gate_pass = bool(
            provenance_pass
            and live_retention_rate is not None
            and live_retention_rate >= args.min_live_retention
        )
    acceptance_pass = bool(
        hard_contract_pass and (live_gate_pass is not False)
    )
    summary = {
        "provider": active_chat_provider(),
        "model": active_chat_model(),
        "cases": len(rows),
        "done": sum(row["status"] == "done" for row in rows),
        "expected_pauses": sum(row["expected_pause"] for row in rows),
        "pack_pass": sum(row["pack_pass"] for row in rows),
        "safety_pass": sum(row["safety_pass"] for row in rows),
        "clarification_pass": sum(row["clarification_pass"] for row in rows),
        "case_relation_pass": sum(row["case_relation_pass"] for row in rows),
        "contract_pass": sum(row["contract_pass"] for row in rows),
        "artifact_total": artifact_total,
        "live_artifacts": live_total,
        "fallback_artifacts": fallback_total,
        "failed_artifacts": failed_total,
        "unknown_artifacts": unknown_total,
        "live_retention_rate": (
            round(live_retention_rate, 4)
            if live_retention_rate is not None else None
        ),
        "minimum_live_retention": args.min_live_retention,
        "whole_case_all_fallback": [
            row["case"] for row in artifact_rows if not row["live_artifacts"]
        ],
        "hard_contract_pass": hard_contract_pass,
        "provenance_pass": provenance_pass if artifact_total else None,
        "live_gate_pass": live_gate_pass,
        "acceptance_pass": acceptance_pass,
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    if not acceptance_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
