"""Web-friendly Human Gate (Module 105) — pause/resume on browser approval."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from .module_105_human_gate import HumanGate
from ..models import ApprovalRequest


class WebHumanGate(HumanGate):
    module_id = "105"

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        super().__init__(decider="web")
        self.timeout = timeout_seconds
        self._pending: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, tuple[str, str | None]] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def review(self, approval: ApprovalRequest) -> ApprovalRequest:
        ev = threading.Event()
        with self._lock:
            self._pending[approval.approval_id] = approval
            self._events[approval.approval_id] = ev

        signaled = ev.wait(timeout=self.timeout)
        with self._lock:
            self._pending.pop(approval.approval_id, None)
            self._events.pop(approval.approval_id, None)
            decision = self._decisions.pop(approval.approval_id, None)

        if not signaled or decision is None:
            approval.status = "rejected"
            approval.human_note = "timeout_or_no_decision"
            return approval

        status, note = decision
        approval.status = status  # type: ignore[assignment]
        approval.human_note = note
        if status == "approved":
            approval.approved_at = datetime.now(timezone.utc).isoformat()
        return approval

    def decide(self, approval_id: str, status: str, note: str | None = None) -> bool:
        with self._lock:
            ev = self._events.get(approval_id)
            if ev is None:
                return False
            self._decisions[approval_id] = (status, note)
            ev.set()
            return True

    def pending_snapshot(self) -> list[dict]:
        with self._lock:
            return [a.model_dump() for a in self._pending.values()]
