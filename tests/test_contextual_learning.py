"""104: thresholds + hard-safety guard."""
from __future__ import annotations

from teow_agl.modules.module_104_learning import LearningModule, context_signature


def _sig():
    return context_signature(action_type="delete", path_class="safe_temp",
                             asset_class="generated_temp", backup_status="verified",
                             role_context="public_school", planning_mode="approval_first")


def test_no_patch_below_threshold():
    lm = LearningModule(profile_constraints={"min_approvals_for_friction_reduction": 3,
                                              "min_events_for_destructive_adjustment": 5},
                        hard_safety_cfg={"hard_red_categories": [], "hard_red_patterns": []})
    sig = _sig()
    for _ in range(2):
        lm.record(task_id="t", event_type="human_approved", signature=sig,
                  outcome="human_approved", features={"destructive": True, "backup_verified": True})
    assert lm.evaluate() == []


def test_patch_after_threshold():
    lm = LearningModule(profile_constraints={"min_approvals_for_friction_reduction": 3,
                                              "min_events_for_destructive_adjustment": 5},
                        hard_safety_cfg={"hard_red_categories": [], "hard_red_patterns": []})
    sig = _sig()
    for _ in range(5):
        lm.record(task_id="t", event_type="human_approved", signature=sig,
                  outcome="human_approved", features={"destructive": True, "backup_verified": True})
        lm.record(task_id="t", event_type="execution_completed", signature=sig,
                  outcome="execution_success", features={"destructive": True, "backup_verified": True})
    proposals = lm.evaluate()
    assert any(p.patch_type == "route_weight_adjustment" for p in proposals)


def test_patch_blocked_for_hard_safety():
    lm = LearningModule(profile_constraints={"min_approvals_for_friction_reduction": 3,
                                              "min_events_for_destructive_adjustment": 5},
                        hard_safety_cfg={"hard_red_categories": ["governance_bypass"], "hard_red_patterns": []})
    sig = "delete|x|x|x|x|governance_bypass"
    for _ in range(10):
        lm.record(task_id="t", event_type="human_approved", signature=sig,
                  outcome="human_approved", features={"destructive": True, "backup_verified": True})
    assert lm.evaluate() == []


def test_rejection_blocks_proposal():
    lm = LearningModule(profile_constraints={"min_approvals_for_friction_reduction": 3,
                                              "min_events_for_destructive_adjustment": 5},
                        hard_safety_cfg={"hard_red_categories": [], "hard_red_patterns": []})
    sig = _sig()
    for _ in range(5):
        lm.record(task_id="t", event_type="human_approved", signature=sig,
                  outcome="human_approved", features={"destructive": True, "backup_verified": True})
    lm.record(task_id="t", event_type="human_rejected", signature=sig,
              outcome="human_rejected", features={"destructive": True})
    proposals = lm.evaluate()
    assert all(p.proposed_change.get("context_signature") != sig
               or p.patch_type != "route_weight_adjustment" for p in proposals)
