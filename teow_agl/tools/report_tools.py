"""Report tool — produces a draft report payload."""
from __future__ import annotations

from ..models import CandidateAction


class ReportTool:
    name = "report"

    def __call__(self, action: CandidateAction) -> dict:
        topic = action.metadata.get("topic") or action.target or action.purpose
        body = f"# Report\n\nTopic: {topic}\n\n{action.purpose}\n"
        action.metadata["content"] = body
        return {"status": "success", "summary": f"drafted_report:{len(body)}_chars", "affected": []}
