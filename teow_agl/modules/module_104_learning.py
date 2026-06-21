"""Module 104 — Contextual Learning."""
from __future__ import annotations

from collections import defaultdict

from ..models import LearningEvent, PolicyPatchProposal


_LEARNED_TARGET_FILE = "learned_contextual_policy.json"
_PATCH_ROUTE_WEIGHT = "route_weight_adjustment"
_PATCH_PLANNER_HINT = "planner_mode_hint"


def context_signature(*, action_type: str, path_class: str, asset_class: str, backup_status: str, role_context: str, planning_mode: str) -> str:
    parts = [action_type, path_class, asset_class, backup_status, role_context, planning_mode]
    return "|".join(p or "unknown" for p in parts)


class LearningModule:
    module_id = "104"

    def __init__(self, *, profile_constraints: dict, hard_safety_cfg: dict) -> None:
        self.events: list[LearningEvent] = []
        self.proposals: list[PolicyPatchProposal] = []
        self.constraints = dict(profile_constraints or {})
        self.hard_safety_cfg = dict(hard_safety_cfg or {})

    def record(
        self,
        *,
        task_id: str,
        event_type: str,
        signature: str,
        outcome: str,
        features: dict | None = None,
    ) -> LearningEvent:
        ev = LearningEvent(
            task_id=task_id, event_type=event_type,
            context_signature=signature, outcome=outcome,
            features=features or {},
        )
        self.events.append(ev)
        return ev

    def evaluate(self) -> list[PolicyPatchProposal]:
        proposals: list[PolicyPatchProposal] = []
        by_sig: dict[str, list[LearningEvent]] = defaultdict(list)
        for e in self.events:
            by_sig[e.context_signature].append(e)

        min_appr = int(self.constraints.get("min_approvals_for_friction_reduction", 3))
        min_evt = int(self.constraints.get("min_events_for_destructive_adjustment", 5))

        for sig, evts in by_sig.items():
            approvals = sum(1 for e in evts if e.outcome == "human_approved")
            rejections = sum(1 for e in evts if e.outcome == "human_rejected")
            successes = sum(1 for e in evts if e.outcome == "execution_success")
            failures = sum(1 for e in evts if e.outcome == "execution_failed")
            destructive = any(e.features.get("destructive") for e in evts)

            if rejections > 0 or failures > 0:
                continue
            if self._signature_touches_hard_safety(sig):
                continue
            if approvals < min_appr:
                continue
            if destructive and len(evts) < min_evt:
                continue

            backup_verified = any("backup_verified" in e.features and e.features["backup_verified"] for e in evts)
            if destructive and not backup_verified:
                continue

            adjustment = -8.0 if destructive else -5.0

            proposals.append(
                PolicyPatchProposal(
                    patch_type=_PATCH_ROUTE_WEIGHT,
                    target_file=_LEARNED_TARGET_FILE,
                    proposed_change={
                        "type": _PATCH_ROUTE_WEIGHT,
                        "context_signature": sig,
                        "adjustment": adjustment,
                        "reason": f"{approvals} approvals, {successes} successes, no rejections, no failures",
                    },
                    reason=f"Context signature has {approvals} approvals and {successes} successes with no rejections.",
                    evidence=[{"approvals": approvals, "successes": successes, "destructive": destructive}],
                    status="proposed",
                )
            )

        cat_recovery: dict[str, dict[str, int]] = defaultdict(lambda: {"refusals": 0, "recoveries": 0})
        for e in self.events:
            cat = e.features.get("task_category")
            if not cat:
                continue
            if e.event_type == "planner_refusal":
                cat_recovery[cat]["refusals"] += 1
            if e.event_type == "refusal_recovery_success":
                cat_recovery[cat]["recoveries"] += 1

        for cat, stats in cat_recovery.items():
            if stats["recoveries"] >= min_appr and stats["refusals"] >= min_appr:
                proposals.append(
                    PolicyPatchProposal(
                        patch_type=_PATCH_PLANNER_HINT,
                        target_file=_LEARNED_TARGET_FILE,
                        proposed_change={
                            "type": _PATCH_PLANNER_HINT,
                            "task_category": cat,
                            "planning_mode": "approval_first",
                            "reason": "model consistently refuses but recovery succeeds",
                        },
                        reason=f"{stats['refusals']} refusals and {stats['recoveries']} recoveries for {cat}",
                        evidence=[stats], status="proposed",
                    )
                )

        self.proposals.extend(proposals)
        return proposals

    def _signature_touches_hard_safety(self, sig: str) -> bool:
        s = sig.lower()
        for cat in self.hard_safety_cfg.get("hard_red_categories", []):
            if cat.lower() in s:
                return True
        for pat in self.hard_safety_cfg.get("hard_red_patterns", []):
            if pat.lower() in s:
                return True
        return False

    @staticmethod
    def approve_patch(proposal: PolicyPatchProposal, approved_by: str) -> PolicyPatchProposal:
        proposal.status = "approved"
        proposal.approved_by = approved_by
        return proposal

    @staticmethod
    def reject_patch(proposal: PolicyPatchProposal, rejected_by: str) -> PolicyPatchProposal:
        proposal.status = "rejected"
        proposal.approved_by = rejected_by
        return proposal
