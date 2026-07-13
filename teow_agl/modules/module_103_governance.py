"""
Module 103 — Governance Arbiter (sole authority for routing).
Fully data-driven; no hardcoded keyword/path/category literals.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..models import (
    ApprovalRequest,
    CandidateAction,
    ActionRiskAssessment,
    GovernanceDecision,
    PreGovernanceAssessment,
)
from ..policies.governance_profile import ProfileView
from ..policies import hard_safety as hs
from ..util.ticket import issue_ticket


_ROUTE_ORDER = {"BLUE": 0, "GREEN": 1, "INFEASIBLE": 2, "RED": 3}
_ROUTE_BY_RANK = {v: k for k, v in _ROUTE_ORDER.items()}
_BLUE = "BLUE"
_GREEN = "GREEN"
_INFEASIBLE = "INFEASIBLE"
_RED = "RED"


def _route_max(*routes: str) -> str:
    return _ROUTE_BY_RANK[max(_ROUTE_ORDER[r] for r in routes)]


class GovernanceModule:
    module_id = "103"

    def __init__(
        self,
        *,
        profile: ProfileView,
        hard_safety_cfg: dict,
        learned_policy: dict,
        action_taxonomy: dict,
        policy_version: str = "unknown",
    ) -> None:
        self.profile = profile
        self.hard_safety_cfg = hard_safety_cfg
        self.learned_policy = learned_policy
        self.taxonomy: dict[str, list[str]] = dict(action_taxonomy.get("feature_predicates", {}))
        self.policy_version = policy_version

    def decide(
        self,
        *,
        pre: PreGovernanceAssessment,
        action: CandidateAction,
        risk: ActionRiskAssessment,
        signature: str | None = None,
    ) -> GovernanceDecision:
        reasons: list[str] = []

        if pre.hard_block:
            # An "infeasible_" hard_block_code routes INFEASIBLE rather than
            # RED. INFEASIBLE = capability/resource problem (we can't), not a
            # policy violation (we won't).
            if (pre.hard_block_code or "").startswith("infeasible_"):
                reasons.append(f"pre_governance_infeasible:{pre.hard_block_code}")
                return self._infeasible(action, reasons)
            reasons.append(f"pre_governance_hard_block:{pre.hard_block_code}")
            return self._red(action, reasons, ticket_required=False)

        if hs.is_hard_red_category(pre.hard_block_code or "", self.hard_safety_cfg):
            reasons.append("hard_safety_category_match")
            return self._red(action, reasons, ticket_required=False)

        # Infeasibility features from 101B (capability/resource constraints)
        matched_infeasible = self._match_action_names(
            risk.features, self.profile.profile.get("infeasibility_actions", [])
        )
        if matched_infeasible:
            reasons.extend(f"infeasibility:{n}" for n in matched_infeasible)
            return self._infeasible(action, reasons)

        matched_hard = self._match_action_names(risk.features, self.profile.hard_block_actions)
        if matched_hard:
            reasons.extend(f"profile_hard_block:{n}" for n in matched_hard)
            return self._red(action, reasons, ticket_required=False)

        route: str = risk.recommended_route
        reasons.append(f"risk_recommended:{route}")
        # Carry forward 101D's SPECIFIC data-use reason (e.g. "official record
        # write needs human verification") so the approval card / decision
        # explains WHY, not just a bare "risk_recommended:GREEN".
        for _r in ((risk.features or {}).get("data_use_guard") or {}).get("reasons", []):
            if _r and _r not in reasons:
                reasons.append(_r)

        # Mandatory policy approvals establish a non-negotiable route floor.
        # Learned friction-reduction may streamline safe work, but it must
        # never learn away a human gate imposed by the base profile, a domain
        # pack, or the deterministic data-use guard.
        policy_floor = _BLUE
        md = action.metadata or {}
        contract_local_draft = bool(
            md.get("school_output_contract")
            and (
                md.get("school_content_role") == "chat_companion"
                or md.get("release_state") == "draft_only"
            )
        )
        matched_approval = self._match_action_names(risk.features, self.profile.approval_required_actions)
        if contract_local_draft:
            matched_approval = []
        if matched_approval:
            reasons.extend(f"profile_approval_required:{n}" for n in matched_approval)
            route = _route_max(route, _GREEN)
            policy_floor = _GREEN

        data_use_guard = (risk.features or {}).get("data_use_guard") or {}
        if str(data_use_guard.get("decision") or "").upper() == _GREEN:
            policy_floor = _GREEN
            reasons.append("policy_floor:data_use_guard:GREEN")

        if signature:
            adjustment = float(self.learned_policy.get("route_weight_adjustments", {}).get(signature, 0.0))
            if adjustment < 0:
                cap_violated = self._violates_downgrade_cap(risk.features, adjustment)
                if not cap_violated:
                    tiers = max(1, int(abs(adjustment) // 5))
                    new_rank = max(0, _ROUTE_ORDER[route] - tiers)
                    new_route = _ROUTE_BY_RANK[new_rank]
                    if new_route != route:
                        reasons.append(f"learned_downgrade:{signature}:adj={adjustment}:{route}->{new_route}")
                        route = new_route
                else:
                    reasons.append(f"learned_downgrade_blocked_by_cap:{cap_violated}")
            elif adjustment > 0:
                if route == _BLUE:
                    reasons.append(f"learned_upgrade:{signature}:adj={adjustment}:BLUE->GREEN")
                    route = _GREEN

        if _ROUTE_ORDER[route] < _ROUTE_ORDER[policy_floor]:
            reasons.append(
                f"policy_floor_enforced:{route}->{policy_floor}"
            )
            route = policy_floor

        # P2.2 — fail-safe default. If the goal mentions sensitive student /
        # guardian personal data and the action would otherwise auto-execute
        # (BLUE), refuse silent execution: upgrade to GREEN so a human
        # approves. Catches anything the deterministic concept gates didn't
        # already block — we fail toward asking a human, never toward silent
        # action. Deterministic; the LLM is never the safety authority.
        if (route == _BLUE
                and (pre.context_features or {}).get("sensitive_data_mention")
                and not contract_local_draft):
            reasons.append("failsafe_sensitive_mention:BLUE->GREEN")
            route = _GREEN

        if route == _RED:
            return self._red(action, reasons, ticket_required=False)

        if route == _BLUE:
            ticket = issue_ticket(
                task_id=risk.task_id, action_id=action.action_id,
                tool=action.tool, operation=action.operation, target=action.target,
                route=_BLUE, approval_id=None,
            )
            return GovernanceDecision(
                task_id=risk.task_id, action_id=action.action_id, route=_BLUE,
                reasons=reasons, ticket_required=True, approval_required=False,
                approval_request=None, execution_ticket=ticket,
                policy_version=self.policy_version,
            )

        # Prefer a human-readable label for the approval card: the workflow
        # step name, else the action's purpose, else the raw tool.op fallback.
        # (A bare "chat.answer on (unresolved target)" reads as confusion, not
        # governance.)
        _label = (action.metadata.get("workflow_step_name")
                  or action.purpose
                  or f"{action.tool}.{action.operation} on "
                     f"{action.target or '(unresolved target)'}")
        approval = ApprovalRequest(
            approval_id=f"appr_{uuid.uuid4().hex[:12]}",
            task_id=risk.task_id, action_id=action.action_id,
            summary=str(_label),
            risk_factors=[k for k, v in risk.features.items() if v],
            context={"reasons": reasons, "purpose": action.purpose,
                     "user_intent": action.metadata.get("user_intent", "")},
            status="pending",
        )
        return GovernanceDecision(
            task_id=risk.task_id, action_id=action.action_id, route=_GREEN,
            reasons=reasons, ticket_required=True, approval_required=True,
            approval_request=approval, execution_ticket=None,
            policy_version=self.policy_version,
        )

    def issue_ticket_after_approval(
        self,
        *,
        decision: GovernanceDecision,
        action: CandidateAction,
    ) -> dict:
        if decision.route != _GREEN:
            raise ValueError("only GREEN decisions need post-approval ticketing")
        if not decision.approval_request or decision.approval_request.status != "approved":
            raise ValueError("approval not granted; cannot issue ticket")
        ticket = issue_ticket(
            task_id=decision.task_id, action_id=decision.action_id,
            tool=action.tool, operation=action.operation, target=action.target,
            route=_GREEN, approval_id=decision.approval_request.approval_id,
        )
        decision.execution_ticket = ticket
        return ticket

    def _red(self, action: CandidateAction, reasons: list[str], ticket_required: bool) -> GovernanceDecision:
        return GovernanceDecision(
            task_id=action.metadata.get("task_id", ""),
            action_id=action.action_id, route=_RED,
            reasons=reasons, ticket_required=ticket_required,
            approval_required=False, approval_request=None,
            execution_ticket=None, policy_version=self.policy_version,
        )

    def _infeasible(self, action: CandidateAction, reasons: list[str]) -> GovernanceDecision:
        """INFEASIBLE: capability/resource constraint. Like RED, no ticket
        issued and 107 will never execute — but semantically different from
        a policy violation. 108 handles both, but the UI distinguishes."""
        return GovernanceDecision(
            task_id=action.metadata.get("task_id", ""),
            action_id=action.action_id, route=_INFEASIBLE,
            reasons=reasons, ticket_required=False,
            approval_required=False, approval_request=None,
            execution_ticket=None, policy_version=self.policy_version,
        )

    def _match_action_names(self, features: dict[str, Any], action_names: list[str]) -> list[str]:
        out: list[str] = []
        for name in action_names:
            preds = self.taxonomy.get(name)
            if preds is None:
                continue
            if all(bool(features.get(p)) for p in preds):
                out.append(name)
        return out

    def _violates_downgrade_cap(self, features: dict[str, Any], adjustment: float) -> str | None:
        caps = self.profile.max_auto_downgrade
        magnitude = abs(adjustment)
        for feature_name, cap in caps.items():
            if features.get(feature_name) and magnitude > float(cap):
                return f"{feature_name}:cap={cap}<adjustment={magnitude}"
        return None
