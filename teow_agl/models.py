"""Pydantic data models, mirrored from contracts/schemas/*.json."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TaskEnvelope(BaseModel):
    task_id: str = Field(default_factory=lambda: _new_id("task"))
    session_id: str
    user_id: str
    raw_goal: str
    normalized_goal: str
    workspace_roots: list[str] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)


PlanningMode = Literal[
    "direct", "inspect_first", "draft_first", "approval_first", "explain_only", "blocked",
]


class PreGovernanceAssessment(BaseModel):
    assessment_id: str = Field(default_factory=lambda: _new_id("preg"))
    task_id: str
    task_category: str
    planning_mode: PlanningMode
    hard_block: bool = False
    hard_block_code: str | None = None
    context_sensitive: bool = False
    reasons: list[str] = Field(default_factory=list)
    context_features: dict = Field(default_factory=dict)
    planning_brief: dict = Field(default_factory=dict)


Reversibility = Literal["high", "medium", "low", "unknown"]
Uncertainty = Literal["low", "medium", "high", "unknown"]


class CandidateAction(BaseModel):
    action_id: str = Field(default_factory=lambda: _new_id("act"))
    tool: str
    operation: str
    target: str = ""
    purpose: str = ""
    expected_effect: str = ""
    reversibility: Reversibility = "unknown"
    uncertainty: Uncertainty = "unknown"
    risk_factors: list[str] = Field(default_factory=list)
    requires_governance: bool = True
    metadata: dict = Field(default_factory=dict)


class CandidatePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: _new_id("plan"))
    task_id: str
    planner_id: str
    planning_mode: str
    used_refusal_recovery: bool = False
    actions: list[CandidateAction] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


RefusalType = Literal[
    "universal_hard_safety_refusal",
    "context_sensitive_overrefusal",
    "format_failure",
    "model_error",
    "empty_plan",
]


class PlannerRefusal(BaseModel):
    refusal_id: str = Field(default_factory=lambda: _new_id("refusal"))
    task_id: str
    planner_id: str
    refusal_type: RefusalType
    message: str = ""
    raw_output_hash: str = ""
    recovery_allowed: bool = True


class HardRefusalEscalation(BaseModel):
    escalation_id: str = Field(default_factory=lambda: _new_id("esc"))
    task_id: str
    reason: str
    refusal: PlannerRefusal


RiskLevel = Literal["low", "medium", "high", "critical"]
# Routes (ordered low→high severity):
#   BLUE       — auto-executable after ticket
#   GREEN      — needs human approval
#   INFEASIBLE — capability/resource constraint; can't be done here, route to human
#   RED        — universal hard safety violation; blocked
Route = Literal["BLUE", "GREEN", "INFEASIBLE", "RED"]


class ActionRiskAssessment(BaseModel):
    risk_id: str = Field(default_factory=lambda: _new_id("risk"))
    task_id: str
    action_id: str
    risk_score: float
    risk_level: RiskLevel
    features: dict = Field(default_factory=dict)
    recommended_route: Route
    reasons: list[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=lambda: _new_id("appr"))
    task_id: str
    action_id: str
    summary: str
    risk_factors: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    status: Literal["pending", "approved", "rejected", "modified"] = "pending"
    human_note: str | None = None
    approved_at: str | None = None


class ExecutionTicket(BaseModel):
    ticket_id: str
    task_id: str
    action_id: str
    route: Literal["BLUE", "GREEN"]
    issued_by: Literal["103"] = "103"
    approval_id: str | None = None
    action_hash: str
    expires_at: str
    constraints: dict = Field(default_factory=dict)
    signature: str = Field(alias="_signature")
    model_config = {"populate_by_name": True}


class GovernanceDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: _new_id("gov"))
    task_id: str
    action_id: str
    route: Route
    reasons: list[str] = Field(default_factory=list)
    ticket_required: bool
    approval_required: bool = False
    approval_request: ApprovalRequest | None = None
    execution_ticket: dict | None = None
    policy_version: str = ""


ExecutionStatus = Literal["success", "failed", "denied", "skipped"]


class ExecutionResult(BaseModel):
    result_id: str = Field(default_factory=lambda: _new_id("res"))
    task_id: str
    action_id: str
    ticket_id: str
    status: ExecutionStatus
    output_summary: str = ""
    error: str | None = None
    affected_resources: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=_now)
    completed_at: str = Field(default_factory=_now)


class LearningEvent(BaseModel):
    learning_event_id: str = Field(default_factory=lambda: _new_id("learn"))
    task_id: str
    event_type: str
    context_signature: str
    outcome: str
    features: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now)


PatchStatus = Literal["proposed", "approved", "rejected", "applied"]


class PolicyPatchProposal(BaseModel):
    patch_id: str = Field(default_factory=lambda: _new_id("patch"))
    patch_type: str
    target_file: str
    proposed_change: dict
    reason: str
    evidence: list[dict] = Field(default_factory=list)
    status: PatchStatus = "proposed"
    created_at: str = Field(default_factory=_now)
    approved_by: str | None = None


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: _new_id("ev"))
    timestamp: str = Field(default_factory=_now)
    session_id: str
    task_id: str
    module: str
    event_type: str
    input_hash: str = ""
    output_hash: str = ""
    summary: str = ""
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase 13 — Task Tree models. A complex user goal can be decomposed into
# a flat tree (depth 1) of sub-goals; each leaf is executed as its own
# task through the full pipeline (101A→…→104). The TaskRunResult of the
# parent carries one entry per leaf so audits can replay the whole tree.
# ---------------------------------------------------------------------------

SubGoalStatus = Literal["pending", "running", "done", "failed",
                        "skipped_due_to_failure"]


class SubGoal(BaseModel):
    """One leaf in a Task Tree."""
    sub_goal_id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    status: SubGoalStatus = "pending"
    # task_id of the sub-task spawned for this leaf (set once it runs)
    spawned_task_id: str | None = None
    final_route: str | None = None
    # Short summary of the leaf's outcome — surfaced in UI without
    # forcing it to chase model_dump() of the full sub-result.
    summary: str = ""


class TaskTree(BaseModel):
    """A decomposition of one user goal into sub-goals.

    `leaves` is the ordered list produced by 102T. `order` is the
    topologically-sorted execution sequence (computed at construction
    time by the tree driver). `parent_task_id` is the root task this
    tree belongs to."""
    tree_id: str = Field(default_factory=lambda: _new_id("tree"))
    parent_task_id: str
    root_goal: str
    leaves: list[SubGoal] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    reasoning: str = ""
