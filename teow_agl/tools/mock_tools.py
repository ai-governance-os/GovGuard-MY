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
        # Honest, user-safe summary: in demo mode this records the action without
        # doing real I/O. Never leak the raw internal "mock_<operation>" name to
        # the UI — it reads as a fake/unfinished system. The "[demo]" prefix lets
        # the UI recognise a simulated tool record (see extractChatAnswer).
        return {"status": "success",
                "summary": f"[demo] {action.tool} action simulated — no real "
                           f"external delivery (demo mode)",
                "operation": action.operation,
                "affected": []}
