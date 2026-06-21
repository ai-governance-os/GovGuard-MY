"""
HMAC-signed execution ticket helper.
Tickets are issued ONLY by Module 103. Ported from 10.7.1.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone


_DEFAULT_SECRET = "teow-agl-10.7.3-dev-secret"


def _secret() -> str:
    return os.environ.get("TEOW_AGL_TICKET_SECRET", _DEFAULT_SECRET)


def hash_action(action_id: str, tool: str, operation: str, target: str) -> str:
    msg = "|".join([action_id, tool, operation, target]).encode()
    return hashlib.sha256(msg).hexdigest()


def issue_ticket(
    *,
    task_id: str,
    action_id: str,
    tool: str,
    operation: str,
    target: str,
    route: str,
    approval_id: str | None = None,
    ttl_minutes: int = 30,
) -> dict:
    if route not in ("BLUE", "GREEN"):
        raise ValueError("Tickets may only be issued for BLUE or GREEN routes")
    ticket_id = f"ticket_{uuid.uuid4().hex[:16]}"
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    action_hash = hash_action(action_id, tool, operation, target)
    sig = _sign(ticket_id, task_id, action_id, route, action_hash, approval_id or "")
    return {
        "ticket_id": ticket_id,
        "task_id": task_id,
        "action_id": action_id,
        "route": route,
        "issued_by": "103",
        "approval_id": approval_id,
        "action_hash": action_hash,
        "expires_at": expires_at,
        "constraints": {},
        "_signature": sig,
    }


def contract_view(
    ticket: dict,
    *,
    action_type: str,
    tool: str,
    scope: str,
    demo_mode: bool,
    governance_reason: str,
    approved_by: str,
    approved_at: str | None,
) -> dict:
    """Return the Task-8 signed-ticket *contract* view of an issued ticket.

    Additive only: it copies the full issued ticket (so verify_ticket still
    works on the result) and adds the contract fields. `signature` is exposed
    as a stable alias of the internal `_signature` (HMAC-SHA256).
    Required contract keys: ticket_id, task_id, route, approved_by,
    approved_at, action_type, tool, scope, demo_mode, signature,
    governance_reason.
    """
    view = dict(ticket)
    view["signature"] = ticket.get("_signature", "")
    view["action_type"] = action_type
    view["tool"] = tool
    view["scope"] = scope
    view["demo_mode"] = bool(demo_mode)
    view["governance_reason"] = governance_reason
    view["approved_by"] = approved_by
    view["approved_at"] = approved_at
    return view


def verify_ticket(ticket: dict) -> tuple[bool, str]:
    try:
        if ticket.get("issued_by") != "103":
            return False, "ticket_not_issued_by_103"
        if ticket.get("route") not in ("BLUE", "GREEN"):
            return False, "ticket_route_invalid"
        expires = datetime.fromisoformat(ticket["expires_at"])
        if expires < datetime.now(timezone.utc):
            return False, "ticket_expired"
        sig = ticket.get("_signature", "")
        expected = _sign(
            ticket["ticket_id"],
            ticket["task_id"],
            ticket["action_id"],
            ticket["route"],
            ticket["action_hash"],
            ticket.get("approval_id") or "",
        )
        if not hmac.compare_digest(sig, expected):
            return False, "ticket_signature_invalid"
        return True, "ok"
    except Exception as exc:
        return False, f"ticket_malformed:{exc}"


def _sign(ticket_id: str, task_id: str, action_id: str, route: str, action_hash: str, approval_id: str) -> str:
    msg = "|".join([ticket_id, task_id, action_id, route, action_hash, approval_id]).encode()
    return hmac.new(_secret().encode(), msg, hashlib.sha256).hexdigest()
