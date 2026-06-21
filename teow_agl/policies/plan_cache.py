"""
PlanCache — procedural memory of successful plan shapes per task category.

When the same task category produces the same plan shape (ordered list of
tool+operation pairs) and that plan succeeds end-to-end N times in a row,
we cache the template. Future tasks in the same category with subject
confidence above threshold can be served from the cache, bypassing 102
(the LLM planner) entirely.

This is the "EXECUTE_DIRECT" / "muscle memory" path. Real token savings
because 102 is never called.

Cache integrity rules:
  * Any failure of a cached plan immediately invalidates the cache entry.
  * Cache is per (category, shape_signature) — different shapes never
    collide.
  * Templates store action skeletons; variable fields (target, body) are
    filled at materialization time from the current goal text.
  * Cache writes go through trace event "plan_cache_updated" for audit.

Storage: append-only JSONL where the latest record per (category, shape)
is authoritative. Compaction is optional.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_OFFICE_EXT_TO_TOOL = {".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx"}


def shape_signature(actions: list[dict]) -> str:
    """Stable hash of the ordered (tool, operation) pairs in a plan."""
    parts = []
    for a in actions:
        tool = (a.get("tool") or "").lower()
        op = (a.get("operation") or "").lower()
        parts.append(f"{tool}.{op}")
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _extract_path_token(text: str) -> str:
    """Find the first plausible filename/path mentioned in the goal."""
    if not text:
        return ""
    match = re.search(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,6})", text)
    return match.group(1) if match else ""


def _extract_title(text: str) -> str:
    """A rough title for documents — first 80 chars before a newline."""
    return (text or "").strip().split("\n", 1)[0][:80]


class PlanCache:
    """Append-only JSONL cache of plan templates keyed by (category, shape)."""

    def __init__(
        self,
        path: str | Path,
        *,
        min_successes_for_cache: int = 3,
        default_outputs_dir: str = "./outputs",
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.min_successes_for_cache = min_successes_for_cache
        self.default_outputs_dir = default_outputs_dir
        self._lock = threading.Lock()
        # Cache of latest entries keyed by f"{category}|{shape}"
        self._entries: dict[str, dict] | None = None

    # ---- read ----

    def _load(self) -> dict[str, dict]:
        with self._lock:
            if self._entries is not None:
                return self._entries
            entries: dict[str, dict] = {}
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        key = f"{rec.get('category', '')}|{rec.get('shape', '')}"
                        # Last-write-wins. Status=invalidated entries are kept
                        # but lookup() filters them out.
                        entries[key] = rec
            self._entries = entries
            return entries

    def lookup(self, *, category: str, shape: str | None = None) -> dict | None:
        """Look up the best cached entry. If shape is given, key by it;
        otherwise return any active entry for the category (first match)."""
        entries = self._load()
        if shape:
            rec = entries.get(f"{category}|{shape}")
            if rec and rec.get("status") == "active":
                return rec
            return None
        for key, rec in entries.items():
            cat, _, _ = key.partition("|")
            if cat == category and rec.get("status") == "active":
                return rec
        return None

    # ---- write ----

    def record_success(
        self,
        *,
        category: str,
        actions_dump: list[dict],
        task_id: str = "",
    ) -> dict | None:
        """Record a successful plan. After min_successes_for_cache, the
        template becomes available for EXECUTE_DIRECT. Returns the cache
        entry if it was created/updated, else None."""
        if not category or not actions_dump:
            return None
        shape = shape_signature(actions_dump)
        key = f"{category}|{shape}"
        entries = self._load()
        existing = entries.get(key)
        now = datetime.now(timezone.utc).isoformat()

        if existing is None:
            new_entry = {
                "cache_id": f"cache_{uuid.uuid4().hex[:10]}",
                "category": category,
                "shape": shape,
                "successes": 1,
                "failures": 0,
                # Threshold of 1 means active immediately on first success;
                # otherwise start "warming" until threshold reached.
                "status": "active" if self.min_successes_for_cache <= 1 else "warming",
                "first_seen": now,
                "last_used": now,
                "template": self._extract_template(actions_dump),
            }
            self._append(new_entry)
            return new_entry

        existing["successes"] = int(existing.get("successes", 0)) + 1
        existing["last_used"] = now
        if existing.get("status") in ("warming", "invalidated"):
            if existing["successes"] >= self.min_successes_for_cache:
                existing["status"] = "active"
        self._append(existing)
        return existing

    def record_failure(self, *, category: str, actions_dump: list[dict], task_id: str = "") -> None:
        """A failed cached-plan execution invalidates the entry immediately."""
        if not category or not actions_dump:
            return
        shape = shape_signature(actions_dump)
        key = f"{category}|{shape}"
        entries = self._load()
        existing = entries.get(key)
        now = datetime.now(timezone.utc).isoformat()
        if existing is None:
            return
        existing["failures"] = int(existing.get("failures", 0)) + 1
        existing["status"] = "invalidated"
        existing["invalidated_at"] = now
        self._append(existing)

    def _append(self, entry: dict) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._entries = None  # invalidate read cache

    @staticmethod
    def _extract_template(actions: list[dict]) -> list[dict]:
        """Strip volatile fields from action dumps so the template is the
        shape + tool/operation contract only. Volatile fields (target,
        metadata body, etc.) are re-filled at materialize() time."""
        out: list[dict] = []
        for a in actions:
            out.append({
                "tool": a.get("tool"),
                "operation": a.get("operation"),
                "purpose": a.get("purpose", ""),
                "reversibility": a.get("reversibility", "unknown"),
                "uncertainty": a.get("uncertainty", "unknown"),
                "requires_governance": a.get("requires_governance", True),
                # Keep a marker for fields that need to be filled
                "_meta_template": list((a.get("metadata") or {}).keys()),
            })
        return out

    # ---- materialize ----

    def materialize(
        self,
        cache_entry: dict,
        *,
        goal_text: str,
        task_id: str,
    ) -> list[dict]:
        """Turn a cached template into concrete action dicts, filling
        target/metadata from the current goal text. Returns a list of
        action dicts ready to be passed to CandidateAction(**a)."""
        template = cache_entry.get("template") or []
        path_token = _extract_path_token(goal_text)
        title = _extract_title(goal_text)

        actions: list[dict] = []
        for tpl in template:
            tool = tpl.get("tool", "")
            op = tpl.get("operation", "")
            meta_keys = set(tpl.get("_meta_template") or [])
            target, metadata = self._materialize_target_and_meta(
                tool=tool, operation=op, goal=goal_text,
                path_token=path_token, title=title, meta_keys=meta_keys,
            )
            actions.append({
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": tool,
                "operation": op,
                "target": target,
                "purpose": tpl.get("purpose", ""),
                "expected_effect": tpl.get("purpose", ""),
                "reversibility": tpl.get("reversibility", "unknown"),
                "uncertainty": tpl.get("uncertainty", "low"),
                "risk_factors": ["from_plan_cache"],
                "requires_governance": tpl.get("requires_governance", True),
                "metadata": metadata,
            })
        return actions

    def _materialize_target_and_meta(
        self, *, tool: str, operation: str, goal: str,
        path_token: str, title: str, meta_keys: set[str],
    ) -> tuple[str, dict]:
        """Fill in concrete target + metadata.body based on tool conventions."""
        outputs = self.default_outputs_dir.rstrip("/")
        # Office tools — pick extension from tool name
        if tool in ("docx", "pptx", "xlsx"):
            ext = "." + tool
            if path_token and path_token.lower().endswith(ext):
                target = path_token if "/" in path_token or "\\" in path_token else f"{outputs}/{path_token}"
            else:
                # synthesize a filename from the title
                safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", title)[:60] or "output"
                target = f"{outputs}/{safe}{ext}"
            if tool == "docx":
                metadata = {"title": title, "body": goal}
            elif tool == "pptx":
                metadata = {
                    "title": title,
                    "subtitle": "Generated from cached plan",
                    "slides": [{"title": title, "bullets": [goal[:200]]}],
                }
            else:
                metadata = {"sheets": {"Summary": [["Topic", title], ["Detail", goal]]}}
            return target, metadata

        # Report tool
        if tool == "report" and operation == "draft_report":
            target = f"{outputs}/report.md"
            metadata = {"topic": title, "body": goal}
            return target, metadata

        # fs.save_under_outputs
        if tool == "fs" and operation in ("save_under_outputs", "save", "write"):
            target = path_token if path_token else f"{outputs}/note.txt"
            if "/" not in target and "\\" not in target:
                target = f"{outputs}/{target}"
            return target, {"content": goal}

        # Default — empty target, empty metadata
        return "", {}

    # ---- introspection ----

    def snapshot(self) -> list[dict]:
        """Return current active+warming entries for /api/stats."""
        entries = self._load()
        out: list[dict] = []
        for rec in entries.values():
            out.append({
                "cache_id": rec.get("cache_id"),
                "category": rec.get("category"),
                "shape": rec.get("shape"),
                "status": rec.get("status"),
                "successes": rec.get("successes", 0),
                "failures": rec.get("failures", 0),
                "last_used": rec.get("last_used"),
            })
        out.sort(key=lambda x: (x.get("status") != "active", -x.get("successes", 0)))
        return out
