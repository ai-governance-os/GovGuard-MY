"""
Module 110 — Verifier (light).

After the executor (107) finishes the plan, the verifier asks one
question: *did the output actually satisfy the user's goal?*

Three deterministic checks, no LLM:

  1. length_check   — "user asked for ~N words → did we deliver
                      between N×0.5 and N×2.5?"
  2. format_check   — "user asked for a .docx → is there a non-empty
                      .docx in affected_resources?"
  3. refusal_sniff  — "the chat answer literally says 'I can't help
                      with that' AND governance said it's fine to
                      proceed (BLUE/GREEN) — that's a soft failure"

Returns a structured verification dict the runtime stores on the
TaskRunResult. The runtime decides what to do with a failure (record
a failure outcome in 104, emit a trace event); the verifier itself is
side-effect free.

Phase 14 will upgrade this module with an LLM-as-judge step. The
plumbing here (rules JSON, dict shape, runtime hook) is designed so
that upgrade is purely additive — the light checks remain as a cheap
pre-filter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import (
    CandidateAction, ExecutionResult, GovernanceDecision, TaskEnvelope,
)
from .module_school_artifact_guard import (
    is_school_output_contract,
    school_artifact_verification_checks,
)
from .module_deliverable_mentions import is_requested_output_mention


# Tools whose output is conversational prose (counted by words against
# user's word-count intent).
_PROSE_TOOLS = {"chat", "docx", "report", "fs"}

# These executions are runtime/UI bookkeeping, not task work.  In particular,
# a verifier_synthetic failure must never be fed back into a later verification
# pass as if it were a user-requested action, and a chat companion must not make
# a task look substantively successful when every requested artifact was
# intentionally stopped by governance.
_NON_SUBSTANTIVE_ACTION_IDS = {"verifier_synthetic"}
_NON_SUBSTANTIVE_SCHOOL_ROLES = {"chat_companion"}


class VerifierModule:
    """Module 110 — verifier. Phase 12 mechanical checks + Phase 14
    LLM-as-judge upgrade. Both layers are side-effect-free; the runtime
    decides what to do with failures (record + retry / record + give up).
    """

    module_id = "110"

    def __init__(
        self,
        *,
        rules: dict | None = None,
        rubrics: dict | None = None,
        chat_llm: Any | None = None,
    ) -> None:
        """`rules` is the parsed configs/verifier_rules.json dict.
        `rubrics` is the parsed configs/judge_rubrics.json dict.
        `chat_llm` is required only if LLM-judge will be used; when
        absent, llm_judge() short-circuits to a skipped result."""
        self.rules = rules or {}
        self.rubrics = rubrics or {}
        self.chat_llm = chat_llm
        self._compiled_word_patterns: list[re.Pattern] = []
        for pat in (self.rules.get("length_check", {}) or {})\
                .get("word_intent_patterns", []) or []:
            try:
                self._compiled_word_patterns.append(
                    re.compile(pat, re.IGNORECASE))
            except re.error:
                continue
        self._compiled_ext_patterns: dict[str, re.Pattern] = {}
        ext_map = (self.rules.get("format_check", {}) or {})\
            .get("extension_patterns", {}) or {}
        for ext, pat in ext_map.items():
            try:
                self._compiled_ext_patterns[ext] = re.compile(pat, re.IGNORECASE)
            except re.error:
                continue

    # ------------------------------------------------------------------
    # Public entry — called by runtime once per task.
    # ------------------------------------------------------------------
    def verify(
        self,
        *,
        envelope: TaskEnvelope,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        final_route: str,
        governance_decisions: list[GovernanceDecision | dict] | None = None,
        task_category: str | None = None,
        used_adapted_skill: bool = False,
        adapted_target_tool: str = "",
    ) -> dict:
        """Return a verification report dict. Never raises.

        `task_category` (Phase B) is the category 101A assigned to the
        task. When supplied, scenario-specific sub-checks from
        `verifier_rules.scenario_checks.by_category[<category>]` are
        applied in addition to the three baseline checks.

        `used_adapted_skill` (Phase 2 L4.6) — set True by the runtime when
        this task was solved using a CROSS-CONTEXT ADAPTED skill (the
        synthesizer rewrote a stored procedure from one tool to another).
        When True, the `_skill_adapted_strict_mode` sub-checks run: a
        higher length floor and a mandatory target-tool format match.
        `adapted_target_tool` is the tool the skill was adapted TO
        (e.g. "pptx"); it drives the format-match requirement.

        Shape:
          {
            "enabled": bool,
            "pass": bool,
            "checks": [
              {"name": "length_check", "pass": bool,
               "reason": str, "details": {...}},
              ...
            ],
            "summary": "short string for UI",
            "verification_status": "verified|verified_partial|verified_safe_stop|...",
            "scope": {"verified_action_ids": [...], "blocked_action_ids": [...]},
            "fail_outcome": "failure"  # passed through from config
          }
        """
        out: dict = {
            "enabled": bool(self.rules.get("enabled", True)),
            "pass": True,
            "checks": [],
            "summary": "",
            # Stable, UI-friendly state.  `pass` is retained for backwards
            # compatibility; `verification_status` distinguishes a useful
            # partial result and an intentional governed safe stop.
            "verification_status": "pending",
            # A mechanical pass proves policy/format safety, not that the
            # user's goal was semantically completed. Runtime promotes this
            # to ``goal_complete`` only after the semantic judge passes.
            "verification_grade": "safe_format_only",
            "fail_outcome": self.rules.get("fail_outcome", "failure"),
        }
        if not out["enabled"]:
            out["summary"] = "verifier_disabled"
            out["verification_status"] = "disabled"
            return out

        scope = self.verification_scope(
            plan_actions=plan_actions,
            executions=executions,
            governance_decisions=governance_decisions,
        )
        scoped_actions = scope["plan_actions"]
        scoped_executions = scope["executions"]

        # Canonical response-pack coverage is evaluated from the whole plan,
        # including deliberately excluded GREEN/RED actions. Without this
        # ledger a selected deliverable can disappear before Module 110 sees
        # it and a correct sibling can make the whole task look complete.
        coverage = self._response_pack_coverage(
            envelope=envelope,
            plan_actions=plan_actions,
            executions=executions,
            governance_decisions=governance_decisions,
        )
        if coverage["active"]:
            out["coverage_ledger"] = coverage["items"]
            out["coverage_summary"] = coverage["summary"]
            out["checks"].append({
                "name": "school.goal_coverage",
                "pass": coverage["complete"],
                "reason": (
                    "all_selected_deliverables_have_governed_dispositions"
                    if coverage["complete"]
                    else "missing_or_failed_selected_deliverables:"
                         + ",".join(coverage["incomplete_deliverable_ids"])
                ),
                "details": {
                    "incomplete_deliverable_ids": (
                        coverage["incomplete_deliverable_ids"]
                    ),
                    "status_counts": coverage["status_counts"],
                },
            })
        out["scope"] = {
            "verified_action_ids": scope["verified_action_ids"],
            "excluded_action_ids": scope["excluded_action_ids"],
            "blocked_action_ids": scope["blocked_action_ids"],
            "pending_green_action_ids": scope["pending_green_action_ids"],
            "missing_governance_action_ids": (
                scope["missing_governance_action_ids"]
            ),
            "execution_before_approval_action_ids": (
                scope["execution_before_approval_action_ids"]
            ),
            "execution_of_blocked_action_ids": (
                scope["execution_of_blocked_action_ids"]
            ),
        }
        governance_integrity_errors: list[str] = []
        if scope["missing_governance_action_ids"]:
            governance_integrity_errors.append(
                "missing_governance_decision:"
                + ",".join(scope["missing_governance_action_ids"])
            )
        if scope["execution_before_approval_action_ids"]:
            governance_integrity_errors.append(
                "execution_before_approval:"
                + ",".join(scope["execution_before_approval_action_ids"])
            )
        if scope["execution_of_blocked_action_ids"]:
            governance_integrity_errors.append(
                "execution_of_blocked_action:"
                + ",".join(scope["execution_of_blocked_action_ids"])
            )
        if governance_integrity_errors:
            # This is an audit-integrity failure, not an artifact-quality
            # failure.  Do not let route exemptions, a successful execution,
            # or a pending-approval shortcut conceal missing authority.
            out["pass"] = False
            out["audit_failure"] = True
            out["checks"].append({
                "name": "governance.execution_authority",
                "pass": False,
                "reason": ";".join(governance_integrity_errors),
                "details": {
                    "missing_governance_action_ids": (
                        scope["missing_governance_action_ids"]
                    ),
                    "execution_before_approval_action_ids": (
                        scope["execution_before_approval_action_ids"]
                    ),
                    "execution_of_blocked_action_ids": (
                        scope["execution_of_blocked_action_ids"]
                    ),
                },
            })
            out["verification_status"] = "failed"
            out["verification_grade"] = "failed"
            out["summary"] = (
                "failed: governance.execution_authority="
                + ";".join(governance_integrity_errors)
            )
            return out
        input_boundary = self._input_governance_boundary(envelope)
        partial = bool(scope["blocked_action_ids"] or input_boundary)

        # Do not let a final RED/INFEASIBLE route conceal a response-pack
        # compiler or execution gap. Policy-blocked external actions have a
        # valid BLOCKED disposition; missing selected artifacts do not.
        if coverage["active"] and not coverage["complete"]:
            out["pass"] = False
            out["verification_status"] = "failed"
            out["verification_grade"] = "failed"
            out["summary"] = (
                "failed: school.goal_coverage="
                + ",".join(coverage["incomplete_deliverable_ids"])
            )
            return out

        any_success = bool(scope["successful_substantive_action_ids"])
        expected_substantive = bool(scope["expected_substantive_action_ids"])
        scoped_route = scope["effective_route"] or final_route
        out["effective_route"] = scoped_route
        # A pure RED / INFEASIBLE task intentionally produces no artifact or
        # external side effect. A mixed response pack can still have useful
        # BLUE artifacts alongside one blocked step; those successful outputs
        # must continue through verification.
        if ((final_route or "").upper() in {"RED", "INFEASIBLE"}
                and not any_success
                and (
                    not scope["route_aware"]
                    or not expected_substantive
                )):
            out["summary"] = f"skipped:route_exempt:{final_route.upper()}"
            out["verification_status"] = "verified_safe_stop"
            out["verification_grade"] = "safe_stop"
            return out

        # An unapproved GREEN action has not failed and is not yet eligible
        # for verification.  Keep it visibly pending instead of manufacturing
        # a missing-artifact error before the human gate has authorised it.
        if (not any_success and not expected_substantive
                and scope["pending_green_action_ids"]):
            out["summary"] = "skipped:awaiting_human_approval"
            out["verification_status"] = "awaiting_approval"
            out["verification_grade"] = "partial"
            return out

        # If nothing executed successfully, there's nothing to verify;
        # we leave the upstream failure handling to do its job and pass
        # silently. (We do NOT mark this as a verifier-pass; we mark it
        # as not-applicable.)
        if not any_success:
            if is_school_output_contract(scoped_actions):
                school_checks = school_artifact_verification_checks(
                    envelope, scoped_actions, scoped_executions)
                out["checks"].extend(school_checks)
                out["pass"] = False
                out["verification_status"] = "failed"
                out["verification_grade"] = "failed"
                out["summary"] = (
                    "failed: school.execution_completeness="
                    "no_successful_executions"
                )
                return out
            out["pass"] = True
            out["summary"] = "skipped:no_successful_executions"
            out["verification_status"] = "skipped"
            out["verification_grade"] = "safe_format_only"
            return out

        user_intent = (envelope.normalized_goal or "").strip()

        # Run checks; collect into `checks`.
        len_check = self._length_check(
            user_intent, scoped_actions, scoped_executions)
        if len_check is not None:
            out["checks"].append(len_check)

        preferred_ext = ""
        if used_adapted_skill:
            preferred_ext = self._TOOL_EXTENSION.get(
                (adapted_target_tool or "").lower(), "")
        fmt_check = self._format_check(
            user_intent, scoped_actions, scoped_executions,
            preferred_ext=preferred_ext,
        )
        if fmt_check is not None:
            out["checks"].append(fmt_check)

        ref_check = self._refusal_sniff(
            scoped_actions, scoped_executions, scoped_route)
        if ref_check is not None:
            out["checks"].append(ref_check)

        # P0.2 — no internal generation-failure / apology text inside any
        # generated office artifact (the judge sees the file, not the reason).
        art_check = self._artifact_failure_sniff(
            scoped_actions, scoped_executions)
        if art_check is not None:
            out["checks"].append(art_check)

        # School artifacts are checked independently. A correct sibling can
        # never hide a contaminated, ungrounded or failed file in an aggregate.
        out["checks"].extend(school_artifact_verification_checks(
            envelope, scoped_actions, scoped_executions))

        # Phase B — scenario-specific checks (per-category sub-rules).
        # Returns a LIST of check dicts (one per applicable sub-rule) so
        # the UI / trace can see which specific scenario rule passed or
        # failed (e.g. office_doc.no_placeholder vs research.has_sources).
        scenario_checks = self._scenario_checks(
            user_intent, scoped_actions, scoped_executions, task_category)
        out["checks"].extend(scenario_checks)

        # Phase 2 (L4.6) — stricter bar for tasks solved via an adapted
        # skill. Only runs when the runtime flags used_adapted_skill.
        if used_adapted_skill:
            strict_checks = self._skill_adapted_strict_checks(
                user_intent, scoped_actions, scoped_executions,
                adapted_target_tool)
            out["checks"].extend(strict_checks)
            out["adapted_skill_strict_mode"] = True

        failed_checks = [c for c in out["checks"] if not c["pass"]]
        out["pass"] = len(failed_checks) == 0
        if out["pass"]:
            out["verification_status"] = (
                "verified_partial" if partial else "verified"
            )
            out["verification_grade"] = (
                "partial" if partial else "safe_format_only"
            )
            ok_names = [c["name"] for c in out["checks"]]
            out["summary"] = (f"all checks passed ({len(ok_names)}: "
                              f"{','.join(ok_names) or 'none_applied'})"
                              if ok_names else "no_applicable_checks")
        else:
            out["verification_status"] = "failed"
            out["verification_grade"] = "failed"
            reasons = [f"{c['name']}={c['reason']}" for c in failed_checks]
            out["summary"] = f"failed: {' ; '.join(reasons)[:300]}"
        return out

    # ------------------------------------------------------------------
    # Route-aware scope
    # ------------------------------------------------------------------
    @staticmethod
    def verification_scope(
        *,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        governance_decisions: list[GovernanceDecision | dict] | None = None,
    ) -> dict:
        """Return the actions/results that Module 110 is allowed to judge.

        BLUE work is always expected.  GREEN work is expected only after the
        human gate explicitly approved its approval request.  Execution is
        never evidence of approval: a successful execution while the request
        is pending/rejected/missing is an audit-integrity violation.
        RED/INFEASIBLE work is deliberately absent and therefore excluded
        from completeness checks.  With no decisions supplied, the legacy
        all-actions scope is retained so existing API callers remain
        compatible until Runtime passes its decision list.
        """
        action_by_id = {a.action_id: a for a in plan_actions}
        execution_by_id: dict[str, ExecutionResult] = {}
        for execution in executions:
            if execution.action_id in _NON_SUBSTANTIVE_ACTION_IDS:
                continue
            current = execution_by_id.get(execution.action_id)
            if current is None or execution.status == "success":
                execution_by_id[execution.action_id] = execution

        decision_by_id: dict[str, GovernanceDecision | dict] = {}
        for decision in governance_decisions or []:
            action_id = VerifierModule._decision_value(
                decision, "action_id")
            if action_id:
                decision_by_id[str(action_id)] = decision

        route_aware = governance_decisions is not None
        included_ids: set[str] = set()
        blocked_ids: list[str] = []
        pending_green_ids: list[str] = []
        missing_governance_ids: list[str] = []
        execution_before_approval_ids: list[str] = []
        execution_of_blocked_action_ids: list[str] = []
        expected_substantive_ids: list[str] = []
        included_routes: list[str] = []

        for action in plan_actions:
            action_id = action.action_id
            decision = decision_by_id.get(action_id)
            route = str(
                VerifierModule._decision_value(decision, "route") or ""
            ).upper()
            auxiliary = VerifierModule._is_non_substantive(action)

            if not route_aware:
                include = True
            elif route == "BLUE":
                include = True
            elif route == "GREEN":
                approved = VerifierModule._green_was_approved(
                    decision, execution_by_id.get(action_id))
                include = approved
                if not approved:
                    pending_green_ids.append(action_id)
                    execution = execution_by_id.get(action_id)
                    if (execution is not None
                            and execution.status == "success"):
                        execution_before_approval_ids.append(action_id)
            elif route in {"RED", "INFEASIBLE"}:
                reasons = VerifierModule._decision_value(
                    decision, "reasons") or []
                technical_failure = any(
                    str(reason).startswith((
                        "school_artifact_generation_not_verified",
                        "linked_artifact_not_available",
                        "artifact_generation_failure",
                    ))
                    for reason in reasons
                )
                # Policy-denied work is correctly absent. A required artifact
                # that became INFEASIBLE only because generation failed is not
                # a governed success: keep it in scope so completeness fails.
                include = technical_failure
                if not auxiliary and not technical_failure:
                    blocked_ids.append(action_id)
                execution = execution_by_id.get(action_id)
                if execution is not None and execution.status == "success":
                    execution_of_blocked_action_ids.append(action_id)
            else:
                # Auxiliary UI copy is still checked for scope/hygiene.  A
                # substantive action without a governance decision is not
                # silently treated as authorised once route-aware mode is on.
                include = auxiliary
                if route_aware and not auxiliary:
                    missing_governance_ids.append(action_id)

            if include:
                included_ids.add(action_id)
                if not auxiliary:
                    expected_substantive_ids.append(action_id)
                    if route in {"BLUE", "GREEN"}:
                        included_routes.append(route)

        scoped_actions = [
            action for action in plan_actions
            if action.action_id in included_ids
        ]
        scoped_executions = [
            execution for execution in executions
            if execution.action_id in included_ids
            and execution.action_id not in _NON_SUBSTANTIVE_ACTION_IDS
        ]
        successful_substantive_ids = sorted({
            execution.action_id
            for execution in scoped_executions
            if execution.status == "success"
            and not VerifierModule._is_non_substantive(
                action_by_id.get(execution.action_id))
        })
        excluded_ids = [
            action.action_id for action in plan_actions
            if action.action_id not in included_ids
        ]
        route_rank = {"BLUE": 0, "GREEN": 1}
        effective_route = (
            max(included_routes, key=lambda value: route_rank[value])
            if included_routes else ""
        )
        return {
            "route_aware": route_aware,
            "plan_actions": scoped_actions,
            "executions": scoped_executions,
            "verified_action_ids": [a.action_id for a in scoped_actions],
            "excluded_action_ids": excluded_ids,
            "blocked_action_ids": blocked_ids,
            "pending_green_action_ids": pending_green_ids,
            "missing_governance_action_ids": missing_governance_ids,
            "execution_before_approval_action_ids": (
                execution_before_approval_ids
            ),
            "execution_of_blocked_action_ids": (
                execution_of_blocked_action_ids
            ),
            "expected_substantive_action_ids": expected_substantive_ids,
            "successful_substantive_action_ids": successful_substantive_ids,
            "effective_route": effective_route,
        }

    @classmethod
    def _response_pack_coverage(
        cls,
        *,
        envelope: TaskEnvelope,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        governance_decisions: list[GovernanceDecision | dict] | None,
    ) -> dict:
        """Return one auditable disposition for every selected pack item.

        Artifact creation, external release and official-system change are
        deliberately different outcomes. A policy BLOCKED or approval-pending
        external action is governed; a selected draft that never became a file
        is incomplete. The distinction prevents siblings from hiding omissions.
        """
        pack = (envelope.metadata or {}).get("school_response_pack") or {}
        intent_coverage = pack.get("intent_coverage") or {}
        intent_contract_incomplete = bool(
            isinstance(intent_coverage, dict)
            and intent_coverage.get("pass") is False
        )
        selected = [
            item for item in (pack.get("deliverables") or [])
            if isinstance(item, dict) and item.get("selected") is True
        ]
        if not selected and not intent_contract_incomplete:
            return {
                "active": False, "complete": True, "items": [],
                "incomplete_deliverable_ids": [], "status_counts": {},
                "summary": "no_selected_response_pack",
            }

        actions_by_deliverable: dict[str, list[CandidateAction]] = {}
        actions_by_role: dict[str, list[CandidateAction]] = {}
        action_by_id = {action.action_id: action for action in plan_actions}
        for action in plan_actions:
            meta = action.metadata or {}
            deliverable_id = str(meta.get("deliverable_id") or "")
            role = str(meta.get("artifact_role") or "")
            if deliverable_id:
                actions_by_deliverable.setdefault(deliverable_id, []).append(action)
            if role:
                actions_by_role.setdefault(role, []).append(action)

        execution_by_id: dict[str, ExecutionResult] = {}
        for execution in executions:
            if execution.action_id in _NON_SUBSTANTIVE_ACTION_IDS:
                continue
            previous = execution_by_id.get(execution.action_id)
            if previous is None or execution.status == "success":
                execution_by_id[execution.action_id] = execution

        decision_by_id: dict[str, GovernanceDecision | dict] = {}
        for decision in governance_decisions or []:
            action_id = str(cls._decision_value(decision, "action_id") or "")
            if action_id:
                decision_by_id[action_id] = decision

        items: list[dict] = []
        incomplete: list[str] = []
        status_counts: dict[str, int] = {}
        for expected in selected:
            deliverable_id = str(expected.get("deliverable_id") or "")
            role = str(expected.get("artifact_role") or "")
            kind = str(expected.get("kind") or "artifact").lower()
            candidates = actions_by_deliverable.get(deliverable_id) or []
            if not candidates and role:
                role_candidates = actions_by_role.get(role) or []
                # Legacy plans may predate deliverable_id. Only use a role when
                # it identifies exactly one action; ambiguity is a coverage gap.
                if len(role_candidates) == 1:
                    candidates = role_candidates
            action = candidates[0] if len(candidates) == 1 else None
            decision = decision_by_id.get(action.action_id) if action else None
            route = str(cls._decision_value(decision, "route") or "").upper()
            decision_reasons = cls._decision_value(decision, "reasons") or []
            technical_failure = any(
                str(reason).startswith((
                    "school_artifact_generation_not_verified",
                    "linked_artifact_not_available",
                    "artifact_generation_failure",
                ))
                for reason in decision_reasons
            )
            execution = execution_by_id.get(action.action_id) if action else None
            approval = cls._decision_value(decision, "approval_request")
            if isinstance(approval, dict):
                approval_status = str(approval.get("status") or "").lower()
            else:
                approval_status = str(
                    getattr(approval, "status", "") or ""
                ).lower()

            status = "MISSING"
            detail = "no_unique_action_for_selected_deliverable"
            if action is not None:
                success = execution is not None and execution.status == "success"
                if kind == "artifact":
                    resources = [
                        str(path) for path in (
                            execution.affected_resources if execution else []
                        ) if str(path)
                    ]
                    target_name = Path(str(action.target or "")).name.lower()
                    matching_file = any(
                        (not target_name or Path(path).name.lower() == target_name)
                        for path in resources
                    )
                    if success and matching_file:
                        status = "CREATED"
                        detail = "artifact_execution_succeeded"
                    elif execution is not None and execution.status == "failed":
                        status = "FAILED"
                        detail = execution.error or "artifact_execution_failed"
                    elif technical_failure:
                        status = "FAILED"
                        detail = "technical_contract_failure"
                    elif route in {"RED", "INFEASIBLE"}:
                        status = "BLOCKED"
                        detail = "artifact_blocked_without_safe_created_replacement"
                    elif route == "GREEN" and approval_status != "approved":
                        status = "AWAITING_APPROVAL"
                        detail = "artifact_waiting_for_human_approval"
                    else:
                        detail = "artifact_file_not_created"
                else:
                    if technical_failure:
                        status = "FAILED"
                        detail = "technical_contract_failure"
                    elif route in {"RED", "INFEASIBLE"} or approval_status == "rejected":
                        status = "BLOCKED"
                        detail = "governed_action_stopped"
                    elif route == "GREEN" and approval_status != "approved":
                        status = "AWAITING_APPROVAL"
                        detail = "human_approval_required"
                    elif success:
                        status = "COMPLETED"
                        detail = "governed_action_completed"
                    elif execution is not None and execution.status == "failed":
                        status = "FAILED"
                        detail = execution.error or "governed_action_failed"
                    else:
                        detail = "selected_governed_action_not_completed"

            # Drafts must exist. For external/system actions, BLOCKED and
            # AWAITING_APPROVAL are complete governed dispositions, not errors.
            complete = (
                status == "CREATED" if kind == "artifact"
                else status in {"COMPLETED", "BLOCKED", "AWAITING_APPROVAL"}
            )
            if not complete:
                incomplete.append(deliverable_id or role or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            items.append({
                "deliverable_id": deliverable_id,
                "artifact_role": role,
                "label": expected.get("label") or role or deliverable_id,
                "kind": kind,
                "requirement": expected.get("requirement"),
                "action_id": action.action_id if action else "",
                "route": route,
                "status": status,
                "complete": complete,
                "detail": str(detail)[:240],
            })

        if intent_contract_incomplete:
            identifier = "intent_contract_cardinality"
            incomplete.append(identifier)
            status_counts["MISSING"] = status_counts.get("MISSING", 0) + 1
            items.append({
                "deliverable_id": identifier,
                "artifact_role": "intent_contract",
                "label": "Explicit requested-output contract",
                "kind": "intent_contract",
                "requirement": "explicit_user_request",
                "action_id": "",
                "route": "",
                "status": "MISSING",
                "complete": False,
                "detail": (
                    "semantic intake did not preserve all explicitly declared "
                    "output slots; cardinality_gap="
                    + str(intent_coverage.get("cardinality_gap") or 0)
                )[:240],
            })

        complete = not incomplete
        return {
            "active": True,
            "complete": complete,
            "items": items,
            "incomplete_deliverable_ids": incomplete,
            "status_counts": status_counts,
            "summary": (
                "coverage_complete" if complete
                else "coverage_incomplete:" + ",".join(incomplete)
            ),
        }

    @staticmethod
    def _decision_value(decision: GovernanceDecision | dict | None,
                        key: str) -> Any:
        if decision is None:
            return None
        if isinstance(decision, dict):
            return decision.get(key)
        return getattr(decision, key, None)

    @staticmethod
    def _green_was_approved(
        decision: GovernanceDecision | dict | None,
        execution: ExecutionResult | None,
    ) -> bool:
        # `execution` remains in the signature for compatibility with callers
        # of this internal helper.  It is deliberately not authority: only an
        # explicit human-gate status of `approved` authorises GREEN work.
        del execution
        approval = VerifierModule._decision_value(
            decision, "approval_request")
        if isinstance(approval, dict):
            return str(approval.get("status") or "").lower() == "approved"
        return str(getattr(approval, "status", "") or "").lower() == "approved"

    @staticmethod
    def _is_non_substantive(action: CandidateAction | None) -> bool:
        if action is None:
            return False
        if action.action_id in _NON_SUBSTANTIVE_ACTION_IDS:
            return True
        role = str(
            (action.metadata or {}).get("school_content_role") or ""
        ).lower()
        return role in _NON_SUBSTANTIVE_SCHOOL_ROLES

    @staticmethod
    def _input_governance_boundary(envelope: TaskEnvelope) -> bool:
        pack = (envelope.metadata or {}).get("school_response_pack") or {}
        boundary = pack.get("input_governance") or {}
        decision = str(boundary.get("decision") or "").upper()
        return decision in {"RED", "INFEASIBLE"}

    # ------------------------------------------------------------------
    # Check 1: length sanity
    # ------------------------------------------------------------------
    def _length_check(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> dict | None:
        cfg = self.rules.get("length_check") or {}
        if not cfg.get("enabled", True):
            return None
        target_words = self._extract_target_words(user_intent)
        if target_words is None:
            # User didn't ask for a specific length — check is N/A.
            return None
        applies = set(cfg.get("applies_to_tools", []))
        min_ratio = float(cfg.get("min_ratio", 0.5))
        max_ratio = float(cfg.get("max_ratio", 2.5))

        body = self._collect_prose_body(plan_actions, executions, applies)
        if not body:
            return {
                "name": "length_check",
                "pass": False,
                "reason": f"requested~{target_words}w_no_prose_body_found",
                "details": {"target_words": target_words,
                            "actual_words": 0},
            }
        actual_words = self._word_count(body)
        lo = int(target_words * min_ratio)
        hi = int(target_words * max_ratio)
        if lo <= actual_words <= hi:
            return {
                "name": "length_check", "pass": True,
                "reason": "ok",
                "details": {"target_words": target_words,
                            "actual_words": actual_words,
                            "lower_bound": lo, "upper_bound": hi},
            }
        if actual_words < lo:
            kind = "too_short"
        else:
            kind = "too_long"
        return {
            "name": "length_check", "pass": False,
            "reason": f"{kind}:target~{target_words}_got_{actual_words}",
            "details": {"target_words": target_words,
                        "actual_words": actual_words,
                        "lower_bound": lo, "upper_bound": hi},
        }

    # ------------------------------------------------------------------
    # Check 2: format / artifact sanity
    # ------------------------------------------------------------------
    def _format_check(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        preferred_ext: str = "",
    ) -> dict | None:
        cfg = self.rules.get("format_check") or {}
        if not cfg.get("enabled", True):
            return None
        expected_exts = self._extensions_from_intent(user_intent)
        if preferred_ext and preferred_ext in expected_exts:
            expected_exts = [preferred_ext]
        if not expected_exts:
            return None  # user didn't ask for a specific file format
        min_bytes = int(cfg.get("min_bytes", 64))
        affected_files: list[Path] = []
        for e in executions:
            if e.status != "success":
                continue
            for path in (e.affected_resources or []):
                try:
                    affected_files.append(Path(path))
                except Exception:
                    continue
        missing: list[str] = []
        too_small: list[str] = []
        ok_exts: list[str] = []
        for ext in expected_exts:
            # Look for any affected file whose suffix matches.
            matches = [p for p in affected_files
                       if p.suffix.lower().lstrip(".") == ext]
            if not matches:
                missing.append(ext)
                continue
            # Check size (best-effort; if the file is gone for any
            # reason we report missing, not too_small).
            sized_ok = False
            for p in matches:
                try:
                    if p.exists() and p.stat().st_size >= min_bytes:
                        sized_ok = True
                        break
                except Exception:
                    continue
            if sized_ok:
                ok_exts.append(ext)
            else:
                too_small.append(ext)
        if not missing and not too_small:
            return {
                "name": "format_check", "pass": True,
                "reason": "ok",
                "details": {"expected_extensions": expected_exts,
                            "verified": ok_exts},
            }
        bits: list[str] = []
        if missing:
            bits.append(f"missing={','.join(missing)}")
        if too_small:
            bits.append(f"too_small={','.join(too_small)}")
        return {
            "name": "format_check", "pass": False,
            "reason": ";".join(bits),
            "details": {"expected_extensions": expected_exts,
                        "missing": missing, "too_small": too_small,
                        "verified": ok_exts,
                        "affected_files":
                            [str(p) for p in affected_files]},
        }

    # ------------------------------------------------------------------
    # Check 3: refusal sniff (chat tool only)
    # ------------------------------------------------------------------
    def _refusal_sniff(
        self,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        final_route: str,
    ) -> dict | None:
        cfg = self.rules.get("refusal_sniff") or {}
        if not cfg.get("enabled", True):
            return None
        exempt = set(r.upper() for r in cfg.get("exempt_routes", []))
        if (final_route or "").upper() in exempt:
            return None  # RED / INFEASIBLE — a refusal here is correct
        applies = set(cfg.get("applies_to_tools", ["chat"]))
        phrases = [p.lower() for p in cfg.get("phrases", [])]
        if not phrases:
            return None
        # Look at successful chat executions
        body = self._collect_prose_body(plan_actions, executions, applies)
        if not body:
            return None
        lowered = body.lower()
        matched = [p for p in phrases if p in lowered]
        if not matched:
            return {
                "name": "refusal_sniff", "pass": True,
                "reason": "no_refusal_phrases",
                "details": {"phrases_checked": len(phrases)},
            }
        # We saw a refusal phrase in approved output → soft failure.
        return {
            "name": "refusal_sniff", "pass": False,
            "reason": f"refusal_in_approved_output:{matched[0][:80]}",
            "details": {"matched_phrases": matched[:5],
                        "final_route": final_route,
                        "body_preview": body[:200]},
        }

    def _artifact_failure_sniff(
        self,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> dict | None:
        """P0.2 — fail if a generated artifact (docx/pptx/xlsx/report) still
        contains internal generation-failure / apology text. A judge sees the
        artifact, not the internal reason, so an apology baked into a .docx
        must register as a verify-FAIL, never a green 'VERIFIED'."""
        cfg = self.rules.get("artifact_failure_sniff") or {}
        if not cfg.get("enabled", True):
            return None
        phrases = [p.lower() for p in cfg.get("phrases", [])]
        if not phrases:
            return None
        applies = set(cfg.get("applies_to_tools",
                              ["docx", "pptx", "xlsx", "report"]))
        body = self._collect_prose_body(plan_actions, executions, applies)
        if not body:
            return None
        lowered = body.lower()
        matched = [p for p in phrases if p in lowered]
        if not matched:
            return {"name": "artifact_failure_sniff", "pass": True,
                    "reason": "no_generation_failure_text"}
        return {
            "name": "artifact_failure_sniff", "pass": False,
            "reason": f"generation_failure_text_in_artifact:{matched[0][:60]}",
            "details": {"matched": matched[:3], "body_preview": body[:200]},
        }

    # ------------------------------------------------------------------
    # Phase B — scenario-specific sub-checks
    #
    # Generic engine driven by `scenario_checks.by_category` in the rules
    # file. Each category may declare any combination of these sub-rules;
    # only declared ones run. Each sub-rule emits its own check entry so
    # the UI / trace shows which specific rule passed or failed
    # (e.g. `scenario.office_doc.no_placeholder`).
    # ------------------------------------------------------------------
    def _scenario_checks(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        task_category: str | None,
    ) -> list[dict]:
        if not task_category:
            return []
        sc_cfg = self.rules.get("scenario_checks") or {}
        if not sc_cfg.get("enabled", True):
            return []
        cat_rules = (sc_cfg.get("by_category") or {}).get(task_category)
        if not cat_rules:
            return []

        applies = set(sc_cfg.get("applies_to_tools",
                                  ["chat", "docx", "pptx", "xlsx",
                                   "report", "fs"]))
        body = self._collect_prose_body(plan_actions, executions, applies)
        action_by_id = {a.action_id: a for a in plan_actions}
        successful_actions = [
            action_by_id[e.action_id] for e in executions
            if e.status == "success" and e.action_id in action_by_id
        ]

        out: list[dict] = []
        prefix = f"scenario.{task_category}"

        # ---- min_body_chars --------------------------------------------
        min_chars = cat_rules.get("min_body_chars")
        if isinstance(min_chars, int) and min_chars > 0:
            n = len(body)
            out.append({
                "name": f"{prefix}.min_body_chars",
                "pass": n >= min_chars,
                "reason": "ok" if n >= min_chars
                          else f"got_{n}_chars_need_{min_chars}",
                "details": {"min": min_chars, "actual": n},
            })

        # ---- forbid_placeholders ---------------------------------------
        forbidden = cat_rules.get("forbid_placeholders") or []
        if forbidden and body:
            low = body.lower()
            hits = [p for p in forbidden if str(p).lower() in low]
            out.append({
                "name": f"{prefix}.no_placeholder",
                "pass": len(hits) == 0,
                "reason": "ok" if not hits
                          else f"placeholder_found:{hits[0][:40]}",
                "details": {"forbidden": list(forbidden),
                            "hits": hits[:5]},
            })

        # ---- must_contain_any (sources / disclaimer / assumptions …) ---
        # Each entry: {"label": "has_sources",
        #              "any_of": ["sources:", "来源:", "参考资料:"]}
        for spec in cat_rules.get("must_contain_any") or []:
            if not isinstance(spec, dict):
                continue
            label = str(spec.get("label", "must_contain"))
            needles = [str(n).lower() for n in spec.get("any_of", []) if n]
            if not needles:
                continue
            low = body.lower()
            matched = [n for n in needles if n in low]
            out.append({
                "name": f"{prefix}.{label}",
                "pass": bool(matched),
                "reason": "ok" if matched
                          else f"none_of:{','.join(needles[:3])[:80]}",
                "details": {"any_of": needles, "matched": matched[:3]},
            })

        # ---- forbid_phrases (e.g. lawyer-opinion language) -------------
        for spec in cat_rules.get("forbid_phrases") or []:
            if not isinstance(spec, dict):
                continue
            label = str(spec.get("label", "no_forbidden"))
            phrases = [str(p).lower() for p in spec.get("phrases", []) if p]
            if not phrases or not body:
                continue
            low = body.lower()
            hits = [p for p in phrases if p in low]
            out.append({
                "name": f"{prefix}.{label}",
                "pass": len(hits) == 0,
                "reason": "ok" if not hits else f"forbidden:{hits[0][:60]}",
                "details": {"phrases_checked": len(phrases),
                            "hits": hits[:3]},
            })

        # ---- pptx_min_slides -------------------------------------------
        pp_min = cat_rules.get("pptx_min_slides")
        if isinstance(pp_min, int) and pp_min > 0:
            for a in successful_actions:
                if (a.tool or "").lower() != "pptx":
                    continue
                slides = (a.metadata or {}).get("slides") or []
                n = len(slides) if isinstance(slides, list) else 0
                out.append({
                    "name": f"{prefix}.pptx_min_slides",
                    "pass": n >= pp_min,
                    "reason": "ok" if n >= pp_min
                              else f"only_{n}_slides_need_{pp_min}",
                    "details": {"min": pp_min, "actual": n,
                                "action_id": a.action_id},
                })
                break  # check the first pptx action only

        # ---- xlsx_min_rows ---------------------------------------------
        xl_min = cat_rules.get("xlsx_min_rows")
        if isinstance(xl_min, int) and xl_min > 0:
            for a in successful_actions:
                if (a.tool or "").lower() != "xlsx":
                    continue
                sheets = (a.metadata or {}).get("sheets") or {}
                rows = (a.metadata or {}).get("rows") or []
                # Count rows in the first sheet (or top-level `rows`),
                # excluding the header row.
                if isinstance(sheets, dict) and sheets:
                    first = next(iter(sheets.values()))
                    rows = first if isinstance(first, list) else []
                n = max(0, len(rows) - 1) if isinstance(rows, list) else 0
                out.append({
                    "name": f"{prefix}.xlsx_min_rows",
                    "pass": n >= xl_min,
                    "reason": "ok" if n >= xl_min
                              else f"only_{n}_data_rows_need_{xl_min}",
                    "details": {"min": xl_min, "actual": n,
                                "action_id": a.action_id},
                })
                break

        # ---- min_word_count (uses _word_count, CJK-aware) --------------
        min_words = cat_rules.get("min_word_count")
        if isinstance(min_words, int) and min_words > 0 and body:
            wc = self._word_count(body)
            out.append({
                "name": f"{prefix}.min_word_count",
                "pass": wc >= min_words,
                "reason": "ok" if wc >= min_words
                          else f"got_{wc}_words_need_{min_words}",
                "details": {"min": min_words, "actual": wc},
            })

        return out

    # ------------------------------------------------------------------
    # Phase 2 (L4.6) — adapted-skill strict mode
    #
    # When a task was solved via a cross-context ADAPTED skill, hold the
    # output to a higher bar. Two extra sub-checks, both config-driven by
    # scenario_checks._skill_adapted_strict_mode:
    #
    #   (a) strict_length — raise the length_check lower bound by
    #       extra_min_word_count_pct. Skipped silently when the user
    #       didn't request a specific word count (nothing to scale).
    #   (b) target_tool_format_match — REQUIRE that a successful action
    #       used the tool the skill was adapted TO (or that a file of the
    #       matching extension was written). This is the proof that the
    #       adaptation actually produced the new medium — without it an
    #       adapted "make a deck" skill could silently fall back to a
    #       paragraph blob and still pass.
    # ------------------------------------------------------------------
    _TOOL_EXTENSION = {
        "docx": "docx", "pptx": "pptx", "xlsx": "xlsx",
        "report": "docx", "pdf": "pdf",
    }

    def _skill_adapted_strict_checks(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        adapted_target_tool: str,
    ) -> list[dict]:
        sc_cfg = self.rules.get("scenario_checks") or {}
        strict_cfg = sc_cfg.get("_skill_adapted_strict_mode") or {}
        if not strict_cfg.get("enabled", True):
            return []

        out: list[dict] = []
        prefix = "scenario.skill_adapted"
        target_tool = (adapted_target_tool or "").lower()

        # ---- (a) strict length floor ----------------------------------
        pct = float(strict_cfg.get("extra_min_word_count_pct", 1.0) or 1.0)
        target_words = self._extract_target_words(user_intent)
        if target_words is not None and pct > 1.0:
            len_cfg = self.rules.get("length_check") or {}
            applies = set(len_cfg.get("applies_to_tools",
                                      ["chat", "docx", "report", "fs"]))
            min_ratio = float(len_cfg.get("min_ratio", 0.5))
            strict_floor = int(target_words * min_ratio * pct)
            body = self._collect_prose_body(plan_actions, executions, applies)
            actual = self._word_count(body) if body else 0
            out.append({
                "name": f"{prefix}.strict_length",
                "pass": actual >= strict_floor,
                "reason": "ok" if actual >= strict_floor
                          else f"adapted_too_short:got_{actual}_"
                               f"need_{strict_floor}",
                "details": {"target_words": target_words,
                            "strict_floor": strict_floor,
                            "actual_words": actual,
                            "extra_pct": pct},
            })

        # ---- (b) target-tool format match -----------------------------
        if strict_cfg.get("require_target_tool_format_match", True) \
                and target_tool:
            ok, evidence = self._target_tool_produced(
                target_tool, plan_actions, executions)
            out.append({
                "name": f"{prefix}.target_tool_format_match",
                "pass": ok,
                "reason": "ok" if ok
                          else f"no_{target_tool}_artifact_produced",
                "details": {"target_tool": target_tool,
                            "evidence": evidence},
            })

        return out

    def _target_tool_produced(
        self,
        target_tool: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> tuple[bool, str]:
        """True iff a successful execution actually produced an artifact
        in the adapted target tool's medium. Checks (1) a successful
        action whose tool == target_tool with real metadata, then
        (2) an affected file with the matching extension."""
        action_by_id = {a.action_id: a for a in plan_actions}
        ext = self._TOOL_EXTENSION.get(target_tool, target_tool)

        for e in executions:
            if e.status != "success":
                continue
            a = action_by_id.get(e.action_id)
            if a is None:
                continue
            tool = (a.tool or "").lower()
            meta = a.metadata or {}

            # (1) tool match with non-trivial content
            if tool == target_tool:
                if target_tool == "pptx":
                    slides = meta.get("slides") or []
                    if isinstance(slides, list) and slides:
                        return True, f"pptx_action:{len(slides)}_slides"
                elif target_tool == "xlsx":
                    sheets = meta.get("sheets") or {}
                    rows = meta.get("rows") or []
                    if (isinstance(sheets, dict) and sheets) or \
                       (isinstance(rows, list) and rows):
                        return True, "xlsx_action:has_data"
                else:  # docx / report / fs prose
                    body = (meta.get("body") or meta.get("content")
                            or e.output_summary or "")
                    if str(body).strip():
                        return True, f"{target_tool}_action:has_body"

            # (2) affected file with the matching extension
            for path in (e.affected_resources or []):
                try:
                    if Path(path).suffix.lower().lstrip(".") == ext:
                        return True, f"file:{Path(path).name}"
                except Exception:
                    continue

        return False, "none"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_target_words(self, text: str) -> int | None:
        if not text:
            return None
        for rx in self._compiled_word_patterns:
            m = rx.search(text)
            if m:
                try:
                    n = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                # sanity clamp — "1 word" or "100000 words" both off-base
                if 20 <= n <= 20000:
                    return n
        return None

    def _extensions_from_intent(self, text: str) -> list[str]:
        if not text:
            return []
        out: list[str] = []
        clauses = [
            part.strip() for part in re.split(r"[.!?;\n。！？；]+", text)
            if part.strip()
        ]
        output_request = re.compile(
            r"\b(?:create|make|generate|prepare|produce|build|write|export|"
            r"save|edit|update|populate|fill|convert|turn|put|give me|need|want)\b"
            r"|(?:生成|制作|建立|创建|写|做成|做|准备|整理成|输出|另存为|请做)",
            re.IGNORECASE,
        )
        for ext, rx in self._compiled_ext_patterns.items():
            # A format word can describe source evidence rather than the
            # requested output (for example, "a spreadsheet was emailed to the
            # wrong vendor"). Require an output-making cue in the same clause.
            if any(
                is_requested_output_mention(
                    clause, rx, request_pattern=output_request
                )
                for clause in clauses
            ):
                out.append(ext)
        # De-dup preserving order
        seen: set[str] = set()
        result: list[str] = []
        for e in out:
            if e not in seen:
                seen.add(e)
                result.append(e)
        return result

    @staticmethod
    def _read_execution_text(
        action: CandidateAction,
        execution: ExecutionResult,
    ) -> str:
        """Read the actual text file produced for one action when available.

        The planner metadata is an intent, not execution evidence. Prefer the
        affected resource whose filename matches ``action.target``; only fall
        back to metadata for legacy tests or binary office artifacts.
        """
        target = Path(str(action.target or ""))
        target_name = target.name.lower()
        resources = [Path(str(path)) for path in execution.affected_resources or []]
        ordered = sorted(
            resources,
            key=lambda path: 0 if target_name and path.name.lower() == target_name else 1,
        )
        if target:
            ordered.append(target)
        seen: set[str] = set()
        for path in ordered:
            marker = str(path)
            if not marker or marker.lower() in seen:
                continue
            seen.add(marker.lower())
            if path.suffix.lower() not in {
                ".md", ".txt", ".csv", ".json", ".html", ".htm", ".log",
            }:
                continue
            try:
                if path.is_file():
                    return path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
        return ""

    @staticmethod
    def _collect_artifact_content(
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> str:
        """Build a unified text representation of EVERY artifact the
        agent produced, for the LLM judge to evaluate. Unlike
        `_collect_prose_body` (which only captures chat / docx prose
        bodies), this serialises pptx slide structure and xlsx sheet
        structure too — so the judge sees real content for office tasks
        instead of just the short chat companion.

        Reads from action.metadata (fast; also works in tests where the
        file may not be present on disk).
        """
        action_by_id = {a.action_id: a for a in plan_actions}
        parts: list[str] = []
        for e in executions:
            if e.status != "success":
                continue
            a = action_by_id.get(e.action_id)
            if a is None:
                continue
            tool = (a.tool or "").lower()
            meta = a.metadata or {}

            if tool == "chat":
                text = str(e.output_summary or meta.get("body") or "").strip()
                if text:
                    parts.append(f"[chat reply]\n{text}")

            elif tool in {"docx", "report", "fs"}:
                title = str(meta.get("title") or "").strip()
                file_body = VerifierModule._read_execution_text(a, e)
                body = file_body or str(meta.get("body") or meta.get("content")
                                        or e.output_summary or "").strip()
                if not (title or body):
                    continue
                head = f"[{tool} document"
                if title:
                    head += f" — title: {title[:120]}"
                head += "]"
                if meta.get("school_output_contract"):
                    head = head[:-1] + (
                        f"; target={Path(a.target).name}; "
                        f"action_id={a.action_id}; "
                        f"deliverable_id={meta.get('deliverable_id')}; "
                        f"artifact_id={meta.get('artifact_id')}; "
                        f"role={meta.get('artifact_role')}; "
                        f"audience={meta.get('audience')}]"
                    )
                parts.append(f"{head}\n{body}")

            elif tool == "pptx":
                title = str(meta.get("title") or "").strip()
                slides = meta.get("slides") or []
                if not isinstance(slides, list) or not slides:
                    continue
                lines = [f"[pptx deck — title: {title[:120]}, "
                         f"{len(slides)} slides]"]
                for i, s in enumerate(slides[:12], 1):
                    if not isinstance(s, dict):
                        continue
                    st = str(s.get("title") or "").strip()
                    bullets = s.get("bullets") or []
                    lines.append(f"  Slide {i}: {st[:120]}")
                    if isinstance(bullets, list):
                        for b in bullets[:6]:
                            lines.append(f"    - {str(b)[:200]}")
                parts.append("\n".join(lines))

            elif tool == "xlsx":
                sheets = meta.get("sheets") or {}
                rows = meta.get("rows")
                if isinstance(sheets, dict) and sheets:
                    lines = ["[xlsx workbook]"]
                    for sheet_name, sheet_rows in list(sheets.items())[:5]:
                        if not isinstance(sheet_rows, list):
                            continue
                        lines.append(
                            f"  Sheet {sheet_name!r} "
                            f"({len(sheet_rows)} rows):"
                        )
                        for r in sheet_rows[:8]:
                            if isinstance(r, list):
                                lines.append(
                                    "    | "
                                    + " | ".join(str(c)[:30] for c in r)
                                    + " |"
                                )
                    parts.append("\n".join(lines))
                elif isinstance(rows, list) and rows:
                    lines = [f"[xlsx — {len(rows)} rows]"]
                    for r in rows[:8]:
                        if isinstance(r, list):
                            lines.append(
                                "    | "
                                + " | ".join(str(c)[:30] for c in r)
                                + " |"
                            )
                    parts.append("\n".join(lines))

            elif tool == "image_gen":
                # Judge doesn't see images; surface that one was produced
                # plus the prompt so it can score "did we generate the
                # requested image" qualitatively.
                prompt = str(meta.get("prompt") or "").strip()
                affected = [str(p) for p in (e.affected_resources or [])]
                if prompt or affected:
                    parts.append(
                        f"[image generated]\n"
                        f"  prompt: {prompt[:300]}\n"
                        f"  files: {', '.join(affected[:3])}"
                    )

        if not parts:
            return ""
        # Keep every produced artifact visible to the judge. A fair per-file
        # budget prevents the first two long drafts from consuming the old
        # 8,000-character global window and hiding later deliverables.
        max_total = 30000
        per_artifact = min(6000, max(900, max_total // len(parts)))
        bounded = [part[:per_artifact] for part in parts]
        return "\n\n".join(bounded)

    @staticmethod
    def _collect_prose_body(
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        applies_to_tools: set[str],
    ) -> str:
        """Concatenate prose output from successful executions of the
        configured tools. For docx we read metadata.body / metadata.content
        from the action (cheaper than opening the file); for chat we use
        execution.output_summary directly."""
        action_by_id = {a.action_id: a for a in plan_actions}
        bodies: list[str] = []
        for e in executions:
            if e.status != "success":
                continue
            a = action_by_id.get(e.action_id)
            if a is None:
                continue
            tool = (a.tool or "").lower()
            if tool not in applies_to_tools:
                continue
            if tool == "chat":
                # chat surfaces the answer text in output_summary
                bodies.append(e.output_summary or "")
            else:
                # docx/report/fs: planner wrote the body in metadata
                meta = a.metadata or {}
                body = (meta.get("body") or meta.get("content")
                        or e.output_summary or "")
                bodies.append(str(body))
        return "\n\n".join(b for b in bodies if b)

    @staticmethod
    def _word_count(text: str) -> int:
        """Count words. Three regimes:
          - dominantly CJK (CJK chars >> tokens): return CJK char count
            ('500字' = 500 Chinese characters)
          - dominantly Latin: return token count
          - MIXED CJK+Latin (S-patch fix): count Latin-only tokens
            PLUS CJK chars separately and sum them. Without this, the
            web-grounded recovery body (Chinese intro + English snippets
            + Latin URLs) under-counted: neither regime won and we fell
            back to tokens — losing all the Chinese content's weight.
        """
        if not text:
            return 0
        tokens = re.findall(r"\S+", text)
        cjk = re.findall(r"[㐀-鿿豈-﫿]", text)
        if cjk and len(cjk) > len(tokens) * 2:
            return len(cjk)
        if cjk:
            latin_tokens = sum(
                1 for t in tokens
                if not re.search(r"[㐀-鿿豈-﫿]", t)
            )
            return latin_tokens + len(cjk)
        return len(tokens)

    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_judge_issues(raw_issues: Any) -> list[dict]:
        """Normalise old string issues and the severity-aware judge schema."""
        details: list[dict] = []
        strong_failure = re.compile(
            r"(?:core request|does not (?:address|contain|provide)|"
            r"no actual|missing required|wrong (?:task|recipient|claim)|"
            r"unsupported claim|fabricat|unsafe|privacy breach|gibberish|"
            r"not bilingual|unrelated to the user)",
            re.IGNORECASE,
        )
        # Sort by severity before applying the display cap. Previously the
        # first 12 raw items were sliced before sorting, so a judge could emit
        # many minor notes followed by a critical failure and the critical
        # failure would disappear from both the audit and hard-failure gate.
        if isinstance(raw_issues, (str, dict)):
            issue_source = [raw_issues]
        else:
            issue_source = list(raw_issues or [])
        for raw in issue_source:
            if isinstance(raw, dict):
                message = str(
                    raw.get("message") or raw.get("issue")
                    or raw.get("description") or raw.get("code") or ""
                ).strip()[:300]
                severity = str(raw.get("severity") or "minor").lower()
                if severity not in {"critical", "major", "minor"}:
                    severity = "minor"
                code = str(raw.get("code") or "judge_issue")[:80]
            else:
                message = str(raw or "").strip()[:300]
                severity = "major" if strong_failure.search(message) else "minor"
                code = "judge_issue"
            if message:
                details.append({
                    "severity": severity,
                    "code": code,
                    "message": message,
                })
        severity_rank = {"critical": 0, "major": 1, "minor": 2}
        details.sort(key=lambda item: severity_rank[item["severity"]])
        return details[:8]

    @staticmethod
    def _normalise_judge_dimensions(raw_dimensions: Any) -> dict[str, int]:
        if not isinstance(raw_dimensions, dict):
            return {}
        dimensions: dict[str, int] = {}
        for name in (
            "relevance", "completeness", "factuality", "governance", "usability",
        ):
            try:
                score = int(raw_dimensions.get(name))
            except (TypeError, ValueError):
                continue
            dimensions[name] = max(0, min(100, score))
        return dimensions

    # Phase 14 — LLM-as-judge. Catches semantic failures (agent wrote
    # 500 words but it's gibberish) that the three mechanical checks
    # above can't see. Returns a structured opinion the runtime uses
    # to decide whether to fire the self-fix loop.
    # ------------------------------------------------------------------
    def llm_judge(
        self,
        *,
        envelope: TaskEnvelope,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        final_route: str,
        governance_decisions: list[GovernanceDecision | dict] | None = None,
        task_category: str = "",
    ) -> dict:
        """Ask an LLM to score the output against per-category rubrics.

        Returns:
            {
              "enabled": bool,
              "pass": bool | None,        # None = skipped
              "score": int (0-100),
              "threshold": int,
              "issues":      [str, ...],
              "suggestions": [str, ...],
              "rubric_used": str,         # category key
              "summary": "<short>",
              "skipped_reason": "..."     # only when pass=None
            }

        Never raises — judge errors fall back to skipped.
        """
        out = {
            "enabled": True, "pass": None, "score": 0, "threshold": 0,
            "issues": [], "suggestions": [],
            "rubric_used": "default", "summary": "",
        }

        cfg = self.rules.get("llm_judge") or {}
        if not cfg.get("enabled", True):
            out["enabled"] = False
            out["summary"] = "llm_judge_disabled"
            out["skipped_reason"] = "disabled_in_config"
            return out
        if self.chat_llm is None:
            out["skipped_reason"] = "no_chat_llm"
            out["summary"] = "llm_judge_skipped:no_chat_llm"
            return out

        # Judge the same governed scope as the mechanical verifier.  This is
        # especially important for a mixed task: a safe BLUE replacement can
        # be judged even when a sibling request was correctly RED-blocked.
        all_plan_actions = list(plan_actions)
        all_executions = list(executions)
        scope = self.verification_scope(
            plan_actions=plan_actions,
            executions=executions,
            governance_decisions=governance_decisions,
        )
        plan_actions = scope["plan_actions"]
        executions = scope["executions"]
        has_judgeable_success = bool(
            scope["successful_substantive_action_ids"]
        )

        school_semantics = (envelope.metadata or {}).get(
            "school_semantics") or {}
        if (envelope.metadata or {}).get("response_pack_mode") == "delta":
            # The parent task already owns the original requested pack. A
            # delta child is intentionally scoped to one user-added file, so a
            # general judge comparing it with the full parent request will
            # wrongly demand all parent siblings again. Contract, grounding
            # and role checks still run mechanically for the added artifact.
            out["skipped_reason"] = "school_response_pack_delta"
            out["summary"] = "llm_judge_skipped:school_response_pack_delta"
            return out
        if (
            school_semantics.get("checked") is True
            and school_semantics.get("school_domain") is False
            and str(school_semantics.get("case_relation") or "").lower()
            == "unrelated"
        ):
            # The correct product behaviour is a capability boundary, not a
            # report about the unrelated topic. A generic quality judge would
            # otherwise call the intentional boundary a refusal and trigger
            # two pointless self-fix retries.
            out["skipped_reason"] = "school_domain_boundary"
            out["summary"] = "llm_judge_skipped:school_domain_boundary"
            return out

        # Exempt routes (RED/INFEASIBLE) — a refusal IS the right answer
        exempt = {r.upper() for r in cfg.get("judge_exempt_routes", []) or []}
        if ((final_route or "").upper() in exempt
                and (
                    governance_decisions is None
                    or not has_judgeable_success
                )):
            out["skipped_reason"] = f"route_exempt:{final_route}"
            out["summary"] = out["skipped_reason"]
            return out

        # Category opt-in: empty list = on for all categories
        enabled_cats = cfg.get("judge_enabled_categories") or []
        if enabled_cats and task_category not in enabled_cats:
            out["skipped_reason"] = f"category_not_enabled:{task_category}"
            out["summary"] = out["skipped_reason"]
            return out

        # Q1 fix — judge needs to see ALL artifacts, not just prose body.
        # Before: `_collect_prose_body` only returned chat/docx/report/fs
        # text. For pptx/xlsx tasks that meant the judge saw only the
        # ~80-char chat companion ("我为你做了一份…") and concluded the
        # answer was "just a download link with no content". Now we also
        # serialize pptx structure (slide titles + bullets) and xlsx
        # structure (sheet name + headers + first rows) into the judge
        # brief — so the judge evaluates the REAL artifact content.
        artifact_content = self._collect_artifact_content(
            plan_actions, executions)
        if not artifact_content:
            out["skipped_reason"] = "no_judgeable_output"
            out["summary"] = out["skipped_reason"]
            return out

        rubric_key = (
            "school_governed_markdown"
            if is_school_output_contract(plan_actions)
            else task_category
        )
        rubric = self._rubric_for(rubric_key)
        out["rubric_used"] = rubric["_key"]
        threshold = int(rubric.get("pass_threshold", 60))
        out["threshold"] = threshold
        successful_execution_ids = {
            execution.action_id for execution in executions
            if execution.status == "success"
        }
        required_artifact_result_ids = [
            action.action_id for action in plan_actions
            if action.action_id in successful_execution_ids
            and (action.metadata or {}).get("school_output_contract")
            and (action.metadata or {}).get("school_content_role") == "artifact"
        ]

        # Build prompt for the judge LLM
        call_cfg = self.rubrics.get("judge_call") or {}
        max_tokens = int(call_cfg.get("max_tokens", 600))

        governance_context: dict | None = None
        if rubric_key == "school_governed_markdown":
            response_pack = (
                (envelope.metadata or {}).get("school_response_pack") or {}
            )
            action_contracts = []
            for action in plan_actions:
                meta = action.metadata or {}
                if meta.get("school_content_role") != "artifact":
                    continue
                action_contracts.append({
                    "artifact_role": meta.get("artifact_role"),
                    "purpose": action.purpose,
                    "audience": meta.get("audience"),
                    "claim_policy": meta.get("claim_policy"),
                    "safe_transformation": meta.get("safe_transformation"),
                    "excluded_data_concepts": (
                        meta.get("excluded_data_concepts") or []
                    ),
                    "requested_languages": meta.get("requested_languages") or [],
                })
            blocked_routes = []
            for decision_item in governance_decisions or []:
                if isinstance(decision_item, dict):
                    route = str(decision_item.get("route") or "").upper()
                    action_id = str(decision_item.get("action_id") or "")
                    reasons = decision_item.get("reasons") or []
                else:
                    route = str(getattr(decision_item, "route", "") or "").upper()
                    action_id = str(
                        getattr(decision_item, "action_id", "") or ""
                    )
                    reasons = getattr(decision_item, "reasons", []) or []
                if route in {"RED", "INFEASIBLE"}:
                    blocked_routes.append({
                        "action_id": action_id,
                        "route": route,
                        "reasons": list(reasons)[:5],
                    })
            coverage = self._response_pack_coverage(
                envelope=envelope,
                plan_actions=all_plan_actions,
                executions=all_executions,
                governance_decisions=governance_decisions,
            )
            selected_contracts = [
                {
                    "deliverable_id": item.get("deliverable_id"),
                    "artifact_role": item.get("artifact_role"),
                    "kind": item.get("kind"),
                    "requirement": item.get("requirement"),
                }
                for item in (response_pack.get("deliverables") or [])
                if isinstance(item, dict) and item.get("selected") is True
            ]
            governance_context = {
                "input_governance": response_pack.get("input_governance") or {},
                "authorised_artifact_contracts": action_contracts,
                "selected_response_pack_contracts": selected_contracts,
                "coverage_dispositions": coverage["items"],
                "governed_blocked_actions": blocked_routes,
                "missing_fact_policy": (
                    "Use TBC for facts the user did not supply; never demand "
                    "that a draft invent a name, date, place, result or approval."
                ),
            }

        system_prompt = self._judge_system_prompt(
            rubric, threshold,
            governed_school=governance_context is not None,
        )
        user_prompt = self._judge_user_prompt(
            envelope.normalized_goal or envelope.raw_goal,
            artifact_content,
            governance_context=governance_context,
        )

        try:
            decision = self.chat_llm.chat_json(
                system=system_prompt, user=user_prompt, max_tokens=max_tokens,
            )
        except Exception as exc:
            out["skipped_reason"] = f"judge_error:{exc}"
            out["summary"] = out["skipped_reason"]
            return out
        if not isinstance(decision, dict) or not decision:
            out["skipped_reason"] = "judge_empty_or_malformed"
            out["summary"] = out["skipped_reason"]
            return out

        try:
            score = int(decision.get("score", 0))
        except (ValueError, TypeError):
            score = 0
        score = max(0, min(100, score))

        # A single total score cannot overrule a critical defect. Normalise the
        # richer schema while retaining compatibility with older string issues.
        decision_issues = decision.get("issues")
        if isinstance(decision_issues, list):
            raw_issues = list(decision_issues)
        elif decision_issues in (None, ""):
            raw_issues = []
        else:
            raw_issues = [decision_issues]
        critical_values = decision.get("critical_failures")
        if not isinstance(critical_values, list):
            critical_values = [] if critical_values in (None, "") else [critical_values]
        for critical in critical_values:
            if isinstance(critical, dict):
                raw_issues.append({**critical, "severity": "critical"})
            else:
                raw_issues.append({
                    "severity": "critical", "code": "critical_failure",
                    "message": str(critical),
                })
        issue_details = self._normalise_judge_issues(raw_issues)
        dimensions = self._normalise_judge_dimensions(
            decision.get("dimensions")
        )
        raw_artifact_results = decision.get("artifact_results") or []
        if not isinstance(raw_artifact_results, list):
            raw_artifact_results = []
        deliverable_to_action = {
            str((action.metadata or {}).get("deliverable_id")): action.action_id
            for action in plan_actions
            if (action.metadata or {}).get("deliverable_id")
        }
        required_result_set = set(required_artifact_result_ids)
        artifact_results: list[dict] = []
        seen_required_results: set[str] = set()
        duplicate_artifact_results: list[str] = []
        invalid_artifact_results: list[str] = []
        artifact_failures: list[str] = []
        for raw_result in raw_artifact_results[:64]:
            if not isinstance(raw_result, dict):
                continue
            raw_id = str(
                raw_result.get("action_id")
                or raw_result.get("deliverable_id") or ""
            ).strip()
            action_id = (
                raw_id if raw_id in required_result_set
                else deliverable_to_action.get(raw_id, raw_id)
            )
            pass_value = raw_result.get("pass")
            issues_value = raw_result.get("issues") or []
            if isinstance(issues_value, str):
                issues_value = [issues_value]
            elif not isinstance(issues_value, list):
                issues_value = [issues_value]
            normalised_result = {
                **raw_result,
                "action_id": action_id,
                "pass": pass_value if isinstance(pass_value, bool) else None,
                "issues": [str(item)[:200] for item in issues_value[:8]],
            }
            artifact_results.append(normalised_result)
            if pass_value is False:
                artifact_failures.append(action_id or "artifact")
            if action_id not in required_result_set:
                continue
            if action_id in seen_required_results:
                duplicate_artifact_results.append(action_id)
                continue
            seen_required_results.add(action_id)
            if not isinstance(pass_value, bool):
                invalid_artifact_results.append(action_id)
        missing_artifact_results = [
            action_id for action_id in required_artifact_result_ids
            if action_id not in seen_required_results
        ]

        floor_config = cfg.get("dimension_floors") or {}
        try:
            default_floor = int(
                cfg.get("dimension_floor", max(60, threshold - 10))
            )
        except (TypeError, ValueError):
            default_floor = max(60, threshold - 10)
        dimension_failures: list[str] = []
        dimension_floors: dict[str, int] = {}
        for name in ("relevance", "completeness", "factuality", "governance"):
            try:
                floor = int(floor_config.get(name, default_floor))
            except (TypeError, ValueError):
                floor = default_floor
            floor = max(0, min(100, floor))
            dimension_floors[name] = floor
            if name in dimensions and dimensions[name] < floor:
                dimension_failures.append(
                    f"{name}:{dimensions[name]}<{floor}"
                )

        severe_issues = [
            item for item in issue_details
            if item["severity"] in {"critical", "major"}
        ]
        hard_failures = [
            f"{item['severity']}:{item['code']}" for item in severe_issues
        ]
        hard_failures.extend(
            f"artifact_failed:{item}" for item in artifact_failures
        )
        hard_failures.extend(
            f"artifact_result_missing:{item}" for item in missing_artifact_results
        )
        hard_failures.extend(
            f"artifact_result_invalid:{item}" for item in invalid_artifact_results
        )
        hard_failures.extend(
            f"artifact_result_duplicate:{item}" for item in duplicate_artifact_results
        )
        hard_failures.extend(
            f"dimension_below_floor:{item}" for item in dimension_failures
        )

        out["score"] = score
        out["issue_details"] = issue_details
        out["issues"] = [item["message"] for item in issue_details]
        out["suggestions"] = [
            str(item)[:200] for item in (decision.get("suggestions") or [])
        ][:8]
        out["dimensions"] = dimensions
        out["dimension_floors"] = dimension_floors
        out["artifact_results"] = artifact_results
        out["required_artifact_result_ids"] = required_artifact_result_ids
        out["missing_artifact_result_ids"] = missing_artifact_results
        out["hard_failures"] = hard_failures
        out["pass"] = score >= threshold and not hard_failures
        out["summary"] = (
            f"judge_pass:score={score}/{threshold}"
            if out["pass"] else
            f"judge_fail:score={score}/{threshold}:"
            + ";".join(hard_failures or out["issues"])[:240]
        )
        return out

    # ------------------------------------------------------------------
    # Judge helpers
    # ------------------------------------------------------------------
    def _rubric_for(self, task_category: str) -> dict:
        """Look up the per-category rubric, falling back to 'default'."""
        if task_category and task_category in self.rubrics:
            entry = self.rubrics.get(task_category) or {}
            if isinstance(entry, dict) and entry.get("criteria"):
                return {**entry, "_key": task_category}
        default = self.rubrics.get("default") or {}
        return ({**default, "_key": "default"} if default else
                {"_key": "default", "criteria": [], "pass_threshold": 60})

    @staticmethod
    def _judge_system_prompt(
        rubric: dict,
        threshold: int,
        *,
        governed_school: bool = False,
    ) -> str:
        criteria = rubric.get("criteria") or []
        bullets = "\n".join(f"- {c}" for c in criteria)
        governed_rules = (
            "\n\nGOVERNED SCHOOL CONTRACT RULES:\n"
            "- The supplied governance contract is the AUTHORISED objective. "
            "The original request is evidence of intent, but it does not override "
            "privacy, evidence, approval or safety boundaries.\n"
            "- When input_governance is RED or INFEASIBLE, do NOT penalise an "
            "artifact for omitting the blocked names, marks, sensitive details, "
            "unsupported metric or prohibited action. Penalise it if those items "
            "are reintroduced. Score the stated safe transformation instead.\n"
            "- TBC is the correct professional control for a material fact the "
            "user did not supply. Never demand an invented student identity, date, "
            "time, location, diagnosis, endorsement, result or completed action.\n"
            "- Clearly labelled recommendations and proposals are useful advice, "
            "not case facts, when written as future, modal or imperative steps "
            "subject to human review. Do not penalise role-consistent wording that "
            "only explains what a draft/report/checklist is for. A proposal label "
            "never excuses a past, current or completed-action assertion.\n"
            "- Use the coverage dispositions literally: BLOCKED is acceptable "
            "for an unsafe external/system action, and AWAITING_APPROVAL is "
            "acceptable for a GREEN external action. A selected draft is complete "
            "only when its disposition is CREATED.\n"
            "- A blocked literal instruction does not excuse a weak safe "
            "transformation. Judge whether each safe artifact still addresses the "
            "user's legitimate administrative objective.\n"
            "- Judge every marked artifact separately for relevance, completeness, "
            "factual fidelity and role fit; never let a good sibling hide a bad one."
            if governed_school else ""
        )
        return (
            "You are Module 110's LLM judge. Score how well an agent's "
            "output satisfies a user's goal.\n\n"
            f"Criteria (score AGAINST these):\n{bullets}\n\n"
            f"Scoring: integer 0-100. Pass threshold: {threshold}.\n"
            "Return ONE JSON object only:\n"
            "{\n"
            '  "score": <int 0-100>,\n'
            '  "dimensions": {"relevance": <0-100>, "completeness": <0-100>, '
            '"factuality": <0-100>, "governance": <0-100>, "usability": <0-100>},\n'
            '  "issues": [{"severity": "critical|major|minor", '
            '"code": "short_code", "message": "what is wrong"}],\n'
            '  "artifact_results": [{"action_id": "marker action id", '
            '"pass": <bool>, "issues": ["artifact-specific issue"]}],\n'
            '  "suggestions": ["short repair instruction", ...],\n'
            '  "reasoning": "one sentence overall"\n'
            "}\n"
            "Severity: critical = unsafe/fabricated/wrong task; major = a core "
            "goal, required deliverable or safe transformation is not addressed; "
            "minor = polish only. A critical or major issue is a hard failure "
            "even when the total score reaches the threshold. Return one "
            "artifact_results entry for every marked artifact.\n"
            "Be fair: a reasonable answer for the question deserves a "
            "pass even if not perfect. Be strict: gibberish or refusals "
            "that should have answered get a fail."
            + governed_rules
        )

    # P7 — explicit-language cues. The judge was observed hallucinating a
    # "should have been in Chinese" failure on an English-only prompt
    # (the requirement leaked from memory/context, not the request). We
    # detect a language requirement DETERMINISTICALLY from the goal text
    # and tell the judge to score language ONLY when one is actually asked
    # for. Pure regex, no LLM.
    _LANG_PATTERNS = {
        "Chinese": re.compile(
            r"in chinese|in mandarin|用中文|用中文回答|中文(?:回答|撰写|输出|版本|作答)"
            r"|译成中文|翻译成中文|以中文", re.IGNORECASE),
        "English": re.compile(
            r"in english|用英(?:文|语)|英文(?:回答|撰写|输出|版本)|译成英文"
            r"|翻译成英文|以英文", re.IGNORECASE),
    }

    @classmethod
    def _detect_requested_language(cls, goal: str) -> str:
        """Return "Chinese" / "English" only when the goal EXPLICITLY
        asks for that output language; "" when no language is specified.
        Order: check each pattern; first explicit cue wins."""
        if not goal:
            return ""
        for lang, pat in cls._LANG_PATTERNS.items():
            if pat.search(goal):
                return lang
        return ""

    @classmethod
    def _judge_user_prompt(
        cls,
        goal: str,
        output: str,
        *,
        governance_context: dict | None = None,
    ) -> str:
        requested_lang = cls._detect_requested_language(goal)
        if requested_lang:
            lang_rule = (
                f"Output language: the user explicitly asked for "
                f"{requested_lang}. Penalise if the output is not in "
                f"{requested_lang}."
            )
        else:
            lang_rule = (
                "Output language: the user did NOT specify one. Do NOT "
                "penalise the output for the language it is written in — "
                "judge content and correctness only, never assume a "
                "language requirement from memory or context."
            )
        governed_context = ""
        goal_label = "User's goal"
        if governance_context is not None:
            goal_label = "Original user request (not an authority to bypass policy)"
            governed_context = (
                "AUTHORITATIVE GOVERNED OBJECTIVE AND ACTION CONTRACTS:\n"
                + json.dumps(
                    governance_context, ensure_ascii=False, indent=2,
                )[:7000]
                + "\n\nScore the artifacts against this governed objective. "
                  "Do not score them against a blocked literal instruction.\n\n"
            )
        return (
            governed_context
            + f"{goal_label}:\n{goal}\n\n"
            f"Agent's output (may include section markers like "
            f"[chat reply] / [docx document] / [pptx deck] / "
            f"[xlsx workbook] / [image generated] — judge ALL of them "
            f"together against the goal):\n{output[:32000]}\n\n"
            "Score based on whether the produced ARTIFACTS satisfy the "
            "user's request. A short chat companion paired with a real "
            "document is fine — score the document, not the chat.\n"
            f"{lang_rule}\n"
            "Reply JSON only."
        )
