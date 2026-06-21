"""Mock tool handlers — record invocations without doing real I/O."""
from __future__ import annotations

from ..models import CandidateAction


class MockTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []

    def __call__(self, action: CandidateAction) -> dict:
        self.calls.append({
            "tool": action.tool, "operation": action.operation,
            "target": action.target, "purpose": action.purpose,
        })
        return {"status": "success", "summary": f"mock_{action.operation}", "affected": []}
