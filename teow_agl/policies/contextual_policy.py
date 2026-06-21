"""Learned contextual policy view. Apply approved patches only."""
from __future__ import annotations


class LearnedPolicyView:
    def __init__(self, learned: dict) -> None:
        self.learned = learned

    @property
    def route_weight_adjustments(self) -> dict[str, float]:
        return dict(self.learned.get("route_weight_adjustments", {}))

    @property
    def contextual_allow_patterns(self) -> list[dict]:
        return list(self.learned.get("contextual_allow_patterns", []))

    @property
    def planner_mode_hints(self) -> dict[str, str]:
        return dict(self.learned.get("planner_mode_hints", {}))

    @property
    def learned_sensitive_patterns(self) -> list[str]:
        return list(self.learned.get("learned_sensitive_patterns", []))

    def adjustment_for(self, signature: str) -> float:
        return float(self.route_weight_adjustments.get(signature, 0.0))

    def is_allowed_pattern(self, signature: str) -> bool:
        for entry in self.contextual_allow_patterns:
            if entry.get("context_signature") == signature:
                return True
        return False


def apply_approved_patch(learned: dict, patch: dict, hard_safety_cfg: dict, profile_constraints: dict) -> tuple[bool, str]:
    if patch.get("status") != "approved":
        return False, "patch_not_approved"

    patch_type = patch.get("patch_type")
    change = patch.get("proposed_change", {})

    if profile_constraints.get("never_weaken_universal_hard_safety", True):
        hard_categories = set(hard_safety_cfg.get("hard_red_categories", []))
        hard_patterns = set(p.lower() for p in hard_safety_cfg.get("hard_red_patterns", []))
        ctx_sig = str(change.get("context_signature", "")).lower()
        if any(cat.lower() in ctx_sig for cat in hard_categories):
            return False, "patch_would_weaken_hard_safety_category"
        for pat in hard_patterns:
            if pat and pat in ctx_sig:
                return False, "patch_would_weaken_hard_safety_pattern"

    if patch_type == "route_weight_adjustment":
        sig = change.get("context_signature")
        adj = float(change.get("adjustment", 0.0))
        if not sig:
            return False, "missing_context_signature"
        rwa = learned.setdefault("route_weight_adjustments", {})
        rwa[sig] = adj
        learned.setdefault("approved_patches", []).append(patch.get("patch_id"))
        return True, "applied"

    if patch_type == "sensitive_pattern_addition":
        pat = change.get("pattern")
        if not pat:
            return False, "missing_pattern"
        lst = learned.setdefault("learned_sensitive_patterns", [])
        if pat not in lst:
            lst.append(pat)
        learned.setdefault("approved_patches", []).append(patch.get("patch_id"))
        return True, "applied"

    if patch_type == "planner_mode_hint":
        cat = change.get("task_category")
        mode = change.get("planning_mode")
        if not cat or not mode:
            return False, "missing_planner_mode_hint_fields"
        learned.setdefault("planner_mode_hints", {})[cat] = mode
        learned.setdefault("approved_patches", []).append(patch.get("patch_id"))
        return True, "applied"

    if patch_type == "model_behavior_patch":
        learned.setdefault("approved_patches", []).append(patch.get("patch_id"))
        return True, "recorded"

    return False, f"unknown_patch_type:{patch_type}"
