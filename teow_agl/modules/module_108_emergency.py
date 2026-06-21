"""Module 108 — Emergency / Block Channel."""
from __future__ import annotations

from datetime import datetime, timezone


class EmergencyModule:
    module_id = "108"

    def __init__(self) -> None:
        self._halted = False
        self._block_records: list[dict] = []

    @property
    def halted(self) -> bool:
        return self._halted

    def block(self, *, task_id: str, action_id: str, reason: str, decision_id: str | None = None) -> dict:
        rec = {
            "task_id": task_id, "action_id": action_id, "decision_id": decision_id,
            "reason": reason, "blocked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._block_records.append(rec)
        return rec

    def halt(self, reason: str) -> dict:
        self._halted = True
        return {"halted": True, "reason": reason, "halted_at": datetime.now(timezone.utc).isoformat()}

    def resume(self) -> None:
        self._halted = False

    def records(self) -> list[dict]:
        return list(self._block_records)
