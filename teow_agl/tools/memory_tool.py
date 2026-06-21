"""
Memory tool — exposes the UserMemory store to the LLM as a callable tool.

LLM action shape:
    {
      "tool": "memory",
      "operation": "add" | "replace" | "remove" | "clear",
      "metadata": {
          "scope": "user" | "memory",       # which file
          "entry": "..."                    # for add
          "old_substring": "...",           # for replace / remove
          "new_text": "..."                 # for replace
      }
    }

Behavior:
  * Reads the LLM's action dict, dispatches to the bound UserMemory.
  * Returns a tool-result dict consumable by 107.
  * The tool is bound to a specific UserMemory instance at runtime; the
    instance keeps the file paths and char limits.
"""
from __future__ import annotations

from typing import Any

from ..models import CandidateAction
from ..policies.user_memory import UserMemory


class MemoryTool:
    name = "memory"

    def __init__(self, user_memory: UserMemory) -> None:
        self.user_memory = user_memory

    def __call__(self, action: CandidateAction) -> dict[str, Any]:
        op = (action.operation or "").lower()
        meta = action.metadata or {}
        scope = str(meta.get("scope") or "user").lower()

        if op == "add":
            entry = meta.get("entry") or meta.get("content") or ""
            out = self.user_memory.add(scope=scope, entry=entry)
            return _result(op, scope, out)

        if op == "replace":
            old = meta.get("old_substring") or meta.get("old") or ""
            new = meta.get("new_text") or meta.get("new") or ""
            out = self.user_memory.replace(scope=scope, old_substring=old, new_text=new)
            return _result(op, scope, out)

        if op == "remove":
            sub = meta.get("substring") or meta.get("old_substring") or ""
            out = self.user_memory.remove(scope=scope, substring=sub)
            return _result(op, scope, out)

        if op == "clear":
            out = self.user_memory.clear(scope=scope)
            return _result(op, scope, out)

        return {"status": "failed", "summary": f"unknown_memory_op:{op}",
                "error": f"unknown operation '{op}'; valid: add, replace, remove, clear",
                "affected": []}


def _result(op: str, scope: str, out: dict) -> dict[str, Any]:
    if out.get("ok"):
        return {"status": "success",
                "summary": f"memory_{op}_ok:scope={scope}:len={out.get('length',0)}",
                "affected": [f"{scope}.md"]}
    err = out.get("error", "unknown_error")
    return {"status": "failed", "summary": f"memory_{op}_failed:{err}",
            "error": err, "affected": []}
