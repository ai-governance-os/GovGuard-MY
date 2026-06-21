"""Chat tool — surfaces conversational LLM answers as execution output.

The planner (Module 102) is already an LLM. For free-form questions,
small talk, explanations, translations, code answers, etc. the planner
writes the answer text directly into `action.metadata.body` (or
`.content` / `.answer`). This tool's job is purely to surface that
text as `output_summary` so the UI can render it as the agent's reply.

If the planner left the body empty AND a fallback chat adapter was
provided at construction time, the tool will call that adapter with
the user's original intent to produce an answer at execution time.
This is the safety net that prevents 'empty answer' UX failures when
the planner forgets to fill content.
"""
from __future__ import annotations

from typing import Callable

from ..models import CandidateAction


class ChatTool:
    """Conversational answer tool.

    Operations:
        answer — return metadata.body / metadata.content / metadata.answer
                 as the execution summary. If empty and a synth callback
                 is wired, call it with (user_intent) to generate text.
    """

    name = "chat"

    def __init__(self, synth: Callable[[str], str] | None = None) -> None:
        """`synth` is an optional fallback callable: synth(user_intent) -> str."""
        self.synth = synth

    def __call__(self, action: CandidateAction) -> dict:
        op = (action.operation or "").lower()
        if op not in ("answer", "reply", "respond", "explain", ""):
            return {
                "status": "failed",
                "summary": "",
                "affected": [],
                "error": f"chat_tool_unknown_operation:{action.operation}",
            }

        meta = action.metadata or {}
        body = (
            meta.get("body")
            or meta.get("content")
            or meta.get("answer")
            or meta.get("text")
            or ""
        )
        body = str(body).strip()

        # Fallback synth: only triggers when planner left body empty AND we
        # have a configured synth adapter. Use action.metadata.user_intent
        # (which the runtime threads in) or fall back to action.purpose.
        if not body and self.synth is not None:
            user_intent = (meta.get("user_intent") or action.purpose or "").strip()
            if user_intent:
                try:
                    body = (self.synth(user_intent) or "").strip()
                except Exception as exc:
                    return {
                        "status": "failed",
                        "summary": "",
                        "affected": [],
                        "error": f"chat_synth_failed:{exc}",
                    }

        if not body:
            return {
                "status": "failed",
                "summary": "",
                "affected": [],
                "error": "chat_empty_body",
            }

        return {
            "status": "success",
            "summary": body,
            "affected": [],
        }
