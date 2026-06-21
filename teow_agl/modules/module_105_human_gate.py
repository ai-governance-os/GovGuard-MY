"""Module 105 — Human Gate (CLI / test variants)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from ..models import ApprovalRequest


class HumanGate:
    module_id = "105"

    def __init__(
        self,
        decider: Callable[[ApprovalRequest], tuple[str, str | None]] | str = "prompt",
    ) -> None:
        self.decider = decider

    def review(self, approval: ApprovalRequest) -> ApprovalRequest:
        if callable(self.decider):
            status, note = self.decider(approval)
        elif self.decider == "approve_all":
            status, note = "approved", "auto-approved by test gate"
        elif self.decider == "reject_all":
            status, note = "rejected", "auto-rejected by test gate"
        else:
            status, note = self._prompt(approval)
        approval.status = status  # type: ignore[assignment]
        approval.human_note = note
        if status == "approved":
            approval.approved_at = datetime.now(timezone.utc).isoformat()
        return approval

    def _prompt(self, approval: ApprovalRequest) -> tuple[str, str]:
        print(f"\n[105 Approval Required]")
        print(f"Action: {approval.summary}")
        print(f"Risk factors: {approval.risk_factors}")
        print(f"Context reasons: {approval.context.get('reasons', [])}")
        ans = input("Approve? [y/N/note]: ").strip()
        if ans.lower().startswith("y"):
            return "approved", "approved by interactive user"
        if ans.lower().startswith("n"):
            return "rejected", "rejected by interactive user"
        return "rejected", ans or "no answer"
