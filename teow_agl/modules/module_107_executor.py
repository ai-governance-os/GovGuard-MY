"""Module 107 — Execution Layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from ..models import CandidateAction, ExecutionResult
from ..util.ticket import hash_action, verify_ticket


ToolHandler = Callable[[CandidateAction], dict]


class ExecutionModule:
    module_id = "107"

    def __init__(self, tool_registry: dict[str, ToolHandler]) -> None:
        self.tool_registry = tool_registry

    def execute(self, *, action: CandidateAction, ticket: dict) -> ExecutionResult:
        started = datetime.now(timezone.utc).isoformat()

        ok, reason = verify_ticket(ticket)
        if not ok:
            return self._denied(action, ticket, started, f"ticket_invalid:{reason}")

        if ticket.get("action_id") != action.action_id:
            return self._denied(action, ticket, started, "ticket_action_id_mismatch")

        expected_hash = hash_action(action.action_id, action.tool, action.operation, action.target)
        if ticket.get("action_hash") != expected_hash:
            return self._denied(action, ticket, started, "action_hash_mismatch")

        handler = self.tool_registry.get(action.tool)
        if handler is None:
            return self._denied(action, ticket, started, f"no_tool_handler:{action.tool}")

        try:
            tool_out = handler(action)
        except Exception as exc:
            return ExecutionResult(
                task_id=ticket["task_id"], action_id=action.action_id,
                ticket_id=ticket["ticket_id"], status="failed",
                output_summary="", error=str(exc), affected_resources=[],
                started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
            )

        return ExecutionResult(
            task_id=ticket["task_id"], action_id=action.action_id,
            ticket_id=ticket["ticket_id"],
            status=tool_out.get("status", "success"),
            output_summary=tool_out.get("summary", ""),
            error=tool_out.get("error"),
            affected_resources=list(tool_out.get("affected", [])),
            started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
        )

    def _denied(self, action: CandidateAction, ticket: dict, started: str, reason: str) -> ExecutionResult:
        return ExecutionResult(
            task_id=ticket.get("task_id", ""), action_id=action.action_id,
            ticket_id=ticket.get("ticket_id", ""), status="denied",
            output_summary="", error=reason, affected_resources=[],
            started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
        )
