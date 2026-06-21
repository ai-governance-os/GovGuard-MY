"""Module 106 — Intake."""
from __future__ import annotations

import uuid

from ..models import TaskEnvelope


class IntakeModule:
    module_id = "106"

    def receive(
        self,
        *,
        raw_goal: str,
        user_id: str,
        session_id: str | None = None,
        workspace_roots: list[str] | None = None,
        attachments: list[dict] | None = None,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> TaskEnvelope:
        normalized = " ".join((raw_goal or "").strip().split())
        return TaskEnvelope(
            task_id=task_id or f"task_{uuid.uuid4().hex[:12]}",
            session_id=session_id or f"sess_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            raw_goal=raw_goal,
            normalized_goal=normalized,
            workspace_roots=list(workspace_roots or []),
            attachments=list(attachments or []),
            metadata=dict(metadata or {}),
        )
