"""
Skill tool — exposes the SkillManager to the LLM as a callable tool.

LLM action shape (one of):

    {
      "tool": "skill_manager",
      "operation": "create",
      "metadata": {
          "name":        "<short slug>",
          "description": "<1-2 sentence summary, used for retrieval>",
          "procedure":   "<the actual markdown body>",
          "tags":        ["...","..."]   # optional
      }
    }

    {
      "tool": "skill_manager",
      "operation": "archive",
      "metadata": { "skill_id": "skill_<hex>" }
    }

    {
      "tool": "skill_manager",
      "operation": "list",
      "metadata": { "include_archived": false }
    }

The tool is bound to a SkillManager + a "task quality" provider at
runtime. The provider is a small dict produced by the runtime that
tells the SkillManager whether the current task is worth learning
from (final_route, verification status, execution count). The LLM
itself does NOT see this — it's a governance layer between the
LLM's request and the disk write.

Note that `create` cannot bypass the quality gate by lying about
task_quality — the runtime injects the real values into action
metadata under a reserved key so the tool reads them, not the LLM.
"""
from __future__ import annotations

from typing import Any

from ..models import CandidateAction
from ..modules.module_skill_manager import SkillManager


class SkillTool:
    name = "skill_manager"

    def __init__(self, skill_manager: SkillManager) -> None:
        self.sm = skill_manager

    def __call__(self, action: CandidateAction) -> dict[str, Any]:
        op = (action.operation or "").lower()
        meta = action.metadata or {}

        if op in ("create", "save", "add"):
            # task_quality is injected by the runtime under a reserved
            # key — the LLM doesn't write here.
            task_quality = meta.get("__task_quality") or {}
            tags = meta.get("tags") or []
            out = self.sm.create_skill(
                name=str(meta.get("name") or "").strip(),
                description=str(meta.get("description") or "").strip(),
                procedure=str(meta.get("procedure")
                              or meta.get("body")
                              or meta.get("content") or "").strip(),
                task_id=str(meta.get("task_id") or ""),
                task_quality=task_quality if isinstance(task_quality, dict) else None,
                tags=tags if isinstance(tags, list) else None,
            )
            return _result(op, out)

        if op in ("archive", "deactivate"):
            sid = str(meta.get("skill_id") or "").strip()
            out = self.sm.archive_skill(sid)
            return _result(op, out)

        if op == "list":
            include_archived = bool(meta.get("include_archived", False))
            items = self.sm.list_skills(include_archived=include_archived)
            return {
                "status": "success",
                "summary": f"skill_list_returned:{len(items)}",
                "affected": [],
                "items": items,
            }

        if op == "read":
            sid = str(meta.get("skill_id") or "").strip()
            body = self.sm.read_skill(sid)
            if not body:
                return {"status": "failed",
                        "summary": f"skill_read_failed:not_found:{sid}",
                        "error": "not_found", "affected": []}
            return {
                "status": "success",
                "summary": f"skill_read_ok:{sid}:len={len(body)}",
                "affected": [], "body": body,
            }

        return {"status": "failed",
                "summary": f"unknown_skill_op:{op}",
                "error": f"unknown operation '{op}'; "
                         f"valid: create, archive, list, read",
                "affected": []}


def _result(op: str, out: dict) -> dict[str, Any]:
    if out.get("ok"):
        summary_parts = [f"skill_{op}_ok"]
        if "skill_id" in out:
            summary_parts.append(out["skill_id"])
        if "char_length" in out:
            summary_parts.append(f"len={out['char_length']}")
        return {
            "status": "success",
            "summary": ":".join(summary_parts),
            "affected": [out.get("path", "")] if out.get("path") else [],
            "skill_id": out.get("skill_id"),
        }
    err = out.get("error", "unknown_error")
    return {"status": "failed",
            "summary": f"skill_{op}_failed:{err}",
            "error": err, "affected": []}
