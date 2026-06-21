"""Task-8 consolidated per-task audit-trace contract.

The trace engine emits a granular EVENT STREAM (106/101A/103/105/107/110/…).
This module assembles a single per-task CONTRACT RECORD from a TaskRunResult
(plus the event stream for retrieval/ticket detail). It does NOT replace or
weaken the richer event schema — it is an additive export with a pinned shape:

  task_id, created_at, domain_pack,
  intake{category, confidence, language},
  retrieval{documents_used, sources},
  planner{model, proposed_actions},
  risk{signals, sensitive_data_detected, external_action_detected},
  governance{route, reason, policy_rules_applied},
  approval{required, status, ticket_id},
  execution{status: not_run|success|blocked|simulated|failed, artifacts},
  verification{status, checks},
  learning{allowed, exclusion_reason}
"""
from __future__ import annotations

from typing import Any

_EXEC_STATUSES = {"not_run", "success", "blocked", "simulated", "failed"}
_SENSITIVE_FEATURES = ("sensitive_path", "credential_path", "high_value_asset")
_EXTERNAL_FEATURES = ("external_facing",)


def _language_of(text: str) -> str:
    has_cjk = any("一" <= ch <= "鿿" for ch in (text or ""))
    has_latin = any(("a" <= ch.lower() <= "z") for ch in (text or ""))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "und"


def _normalize_exec_status(raw: str) -> str:
    r = (raw or "").lower()
    if r in _EXEC_STATUSES:
        return r
    if r in ("ok", "succeeded", "done"):
        return "success"
    if r in ("denied", "blocked_by_ticket", "rejected"):
        return "blocked"
    if r in ("error", "exception"):
        return "failed"
    return "failed"


def build_task_trace_record(
    result: Any,
    *,
    domain_pack: str | None,
    demo_mode: bool,
    trace_events: list[dict] | None = None,
) -> dict:
    """Assemble the Task-8 audit-trace contract record for one task."""
    env = result.envelope
    pre = result.pre_assessment
    task_id = getattr(result, "task_id", None) or env.task_id
    events = [e for e in (trace_events or []) if e.get("task_id") == task_id]

    # --- risk signals ---
    signals: set[str] = set()
    sensitive = external = False
    for ra in getattr(result, "risk_assessments", []) or []:
        feats = getattr(ra, "features", {}) or {}
        for name, val in feats.items():
            if val:
                signals.add(name)
                if name in _SENSITIVE_FEATURES:
                    sensitive = True
                if name in _EXTERNAL_FEATURES:
                    external = True

    # --- governance ---
    decisions = getattr(result, "decisions", []) or []
    policy_rules: list[str] = []
    for d in decisions:
        policy_rules.extend(getattr(d, "reasons", []) or [])
    policy_rules.extend(getattr(pre, "reasons", []) or [])
    reason = ""
    if decisions:
        reason = "; ".join(getattr(decisions[0], "reasons", []) or [])
    elif getattr(pre, "reasons", None):
        reason = "; ".join(pre.reasons)

    # --- approval + ticket ---
    approvals = getattr(result, "approvals", []) or []
    approval_required = any(getattr(d, "approval_required", False) for d in decisions)
    approval_status = approvals[0].status if approvals else (
        "required" if approval_required else "not_required"
    )
    ticket_id = ""
    for e in events:
        if e.get("event_type") == "ticket_issued":
            ticket_id = (e.get("details") or {}).get("ticket_id", "") or ticket_id
    executions = getattr(result, "executions", []) or []
    if not ticket_id:
        for ex in executions:
            if getattr(ex, "ticket_id", ""):
                ticket_id = ex.ticket_id
                break

    # --- execution ---
    artifacts: list[str] = []
    for ex in executions:
        artifacts.extend(getattr(ex, "affected_resources", []) or [])
    final_route = getattr(result, "final_route", "") or ""
    if not executions:
        exec_status = "blocked" if final_route == "RED" else "not_run"
    else:
        norm = [_normalize_exec_status(getattr(ex, "status", "")) for ex in executions]
        if "failed" in norm:
            exec_status = "failed"
        elif demo_mode and external:
            exec_status = "simulated"
        elif "blocked" in norm and all(s == "blocked" for s in norm):
            exec_status = "blocked"
        else:
            exec_status = "success"

    # --- retrieval (from event stream, best-effort) ---
    docs_used, sources = 0, []
    for e in events:
        det = e.get("details") or {}
        if "documents_used" in det:
            docs_used = det.get("documents_used") or docs_used
        if "sources" in det and isinstance(det.get("sources"), list):
            sources = det["sources"]

    # --- verification ---
    ver = getattr(result, "verification", None) or {}
    verification = {
        "status": ver.get("status", "not_run") if isinstance(ver, dict) else "not_run",
        "checks": ver.get("checks", []) if isinstance(ver, dict) else [],
    }

    # --- learning ---
    if getattr(pre, "hard_block", False):
        learning = {"allowed": False,
                    "exclusion_reason": getattr(pre, "hard_block_code", None) or "hard_block"}
    else:
        refl = getattr(result, "reflection", None) or {}
        skipped = isinstance(refl, dict) and refl.get("decision") == "skipped"
        learning = {
            "allowed": not skipped,
            "exclusion_reason": (refl.get("skip_reason") if skipped else None),
        }

    plan = getattr(result, "plan", None)
    return {
        "task_id": task_id,
        "created_at": getattr(env, "created_at", "") or "",
        "domain_pack": domain_pack,
        "intake": {
            "category": getattr(pre, "task_category", None),
            "confidence": (getattr(pre, "context_features", {}) or {}).get("confidence"),
            "language": _language_of(getattr(env, "normalized_goal", "")),
        },
        "retrieval": {"documents_used": docs_used, "sources": sources},
        "planner": {
            "model": getattr(plan, "planner_id", None) if plan else None,
            "proposed_actions": (
                [f"{a.tool}.{a.operation}" for a in plan.actions] if plan else []
            ),
        },
        "risk": {
            "signals": sorted(signals),
            "sensitive_data_detected": sensitive,
            "external_action_detected": external,
        },
        "governance": {
            "route": final_route,
            "reason": reason,
            "policy_rules_applied": sorted(set(policy_rules)),
        },
        "approval": {
            "required": approval_required,
            "status": approval_status,
            "ticket_id": ticket_id,
        },
        "execution": {"status": exec_status, "artifacts": artifacts},
        "verification": verification,
        "learning": learning,
    }


# Required keys, exported for the contract test.
REQUIRED_TOP_KEYS = (
    "task_id", "created_at", "domain_pack", "intake", "retrieval", "planner",
    "risk", "governance", "approval", "execution", "verification", "learning",
)
REQUIRED_NESTED_KEYS = {
    "intake": ("category", "confidence", "language"),
    "retrieval": ("documents_used", "sources"),
    "planner": ("model", "proposed_actions"),
    "risk": ("signals", "sensitive_data_detected", "external_action_detected"),
    "governance": ("route", "reason", "policy_rules_applied"),
    "approval": ("required", "status", "ticket_id"),
    "execution": ("status", "artifacts"),
    "verification": ("status", "checks"),
    "learning": ("allowed", "exclusion_reason"),
}
