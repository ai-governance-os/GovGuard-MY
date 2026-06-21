"""
Module CURATOR — periodic LLM review of accumulated agent memory.

Hermes' design pattern: every 7 days (or when the user fires a button)
an auxiliary LLM process scans USER.md / MEMORY.md / SKILL.md and
suggests cleanups — consolidate duplicates, archive stale skills,
refine wording. Hermes auto-applies; we route every proposal through
the existing 105 human-gate UI. The 105 approval card is the safety
net that makes 'LLM reviews LLM' acceptable in regulated settings.

Three scopes, reviewed independently so a bug in one doesn't
contaminate the others:

  * USER.md  — facts about the user (preferences, language, etc.)
  * MEMORY.md — environment / workspace notes
  * skills    — SkillManager's registry of procedural notes

For each scope the LLM emits 0-N proposals. Each proposal has a
`type` that the runtime knows how to apply:

  * replace_user_md   — UserMemory.replace(scope='user', old, new)
  * replace_memory_md — UserMemory.replace(scope='memory', old, new)
  * archive_skill     — SkillManager.archive_skill(skill_id)
  * consolidate_user_md / consolidate_memory_md — multi-line replace
                          (drop redundant, keep one canonical)

Applies go through the existing per-surface bounded-delta limits
(UserMemory's char_limit; SkillManager.archive is soft only). The
curator NEVER auto-deletes; archive is the strongest action.

Module-level guarantees:
  * Pure proposal generator — no side effects
  * Never raises (LLM errors, malformed responses → empty proposal list)
  * Caps proposal count per run + per scope so a runaway LLM can't
    flood the approval queue
  * Re-runs idempotent: identical state in → identical proposals
    (the underlying LLM is non-deterministic, but the runtime
    de-dupes against already-pending proposals)
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..adapters.chat_llm import ChatLLM


# Allowed proposal type strings — anything else from the LLM is dropped
_VALID_TYPES = frozenset({
    "replace_user_md",
    "replace_memory_md",
    "consolidate_user_md",
    "consolidate_memory_md",
    "archive_skill",
})


class CuratorModule:
    """Module CURATOR. Pure proposal generator, no side effects."""

    module_id = "CURATOR"

    def __init__(
        self,
        *,
        chat_llm: ChatLLM,
        config: dict | None = None,
    ) -> None:
        self.chat_llm = chat_llm
        self.config = config or {}
        # Compile forbidden patterns once
        self._forbidden: list[re.Pattern] = []
        for pat in (self.config.get("forbidden_patterns") or {})\
                .get("patterns", []) or []:
            try:
                self._forbidden.append(re.compile(pat))
            except re.error:
                continue

    # ------------------------------------------------------------------
    # Public entry — called by runtime when curation is triggered
    # ------------------------------------------------------------------
    def run_curation(
        self,
        *,
        user_memory_snapshot: dict,
        skills: list[dict] | None = None,
    ) -> dict:
        """Run one curation pass. Returns:
          {
            "run_id":     "cur_<hex>",
            "created_at": ISO-8601,
            "enabled":    bool,
            "proposals":  [{type, target_file, old_text, new_text,
                            reasoning, scope}, ...],
            "skipped":    [{scope, reason}, ...]  # diagnostic
          }

        Never raises."""
        run_id = "cur_" + uuid.uuid4().hex[:12]
        result: dict = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "enabled": bool(self.config.get("enabled", True)),
            "proposals": [],
            "skipped": [],
        }
        if not result["enabled"]:
            return result

        scope_cfg = self.config.get("scope") or {}
        limits = self.config.get("proposal_limits") or {}
        max_total = int(limits.get("max_proposals_per_run", 10))
        max_per_scope = int(limits.get("max_proposals_per_scope", 5))

        all_proposals: list[dict] = []

        # ---- USER.md ----
        if scope_cfg.get("review_user_md", True):
            body = (user_memory_snapshot.get("USER.md") or "").strip()
            if not body:
                result["skipped"].append({"scope": "user_md", "reason": "empty"})
            else:
                user_proposals = self._review_markdown(
                    scope="user_md",
                    target_file="USER.md",
                    body=body,
                    proposal_types=["replace_user_md", "consolidate_user_md"],
                )
                all_proposals.extend(user_proposals[:max_per_scope])

        # ---- MEMORY.md ----
        if scope_cfg.get("review_memory_md", True):
            body = (user_memory_snapshot.get("MEMORY.md") or "").strip()
            if not body:
                result["skipped"].append({"scope": "memory_md", "reason": "empty"})
            else:
                mem_proposals = self._review_markdown(
                    scope="memory_md",
                    target_file="MEMORY.md",
                    body=body,
                    proposal_types=["replace_memory_md",
                                    "consolidate_memory_md"],
                )
                all_proposals.extend(mem_proposals[:max_per_scope])

        # ---- SKILLs (deterministic + LLM hybrid) ----
        if scope_cfg.get("review_skills", True):
            sk_proposals = self._review_skills(skills or [])
            all_proposals.extend(sk_proposals[:max_per_scope])

        # Global cap
        result["proposals"] = all_proposals[:max_total]
        return result

    # ------------------------------------------------------------------
    # Markdown review (USER.md / MEMORY.md) via chat LLM
    # ------------------------------------------------------------------
    def _review_markdown(
        self,
        *,
        scope: str,
        target_file: str,
        body: str,
        proposal_types: list[str],
    ) -> list[dict]:
        call_cfg = self.config.get("llm_call") or {}
        max_tokens = int(call_cfg.get("max_tokens_per_scope", 2000))
        limits = self.config.get("proposal_limits") or {}
        max_chars = int(limits.get("max_chars_per_proposal", 600))

        system = self._md_system_prompt(scope, target_file,
                                        proposal_types, max_chars)
        user = self._md_user_prompt(target_file, body)
        try:
            raw = self.chat_llm.chat_json(
                system=system, user=user, max_tokens=max_tokens,
            )
        except Exception:
            return []
        if not isinstance(raw, dict):
            return []
        raw_props = raw.get("proposals")
        if not isinstance(raw_props, list):
            return []
        out: list[dict] = []
        for item in raw_props:
            cleaned = self._sanitize_md_proposal(
                item, scope, target_file, body, proposal_types, max_chars)
            if cleaned is not None:
                out.append(cleaned)
        return out

    def _sanitize_md_proposal(
        self, item: Any, scope: str, target_file: str, body: str,
        allowed_types: list[str], max_chars: int,
    ) -> dict | None:
        if not isinstance(item, dict):
            return None
        ptype = str(item.get("type") or "").strip()
        if ptype not in allowed_types or ptype not in _VALID_TYPES:
            return None
        old_text = str(item.get("old_text") or "").strip()
        new_text = str(item.get("new_text") or "").strip()
        reasoning = str(item.get("reasoning") or "").strip()[:300]
        if not old_text:
            return None  # can't apply replace without an anchor
        if old_text not in body:
            # LLM hallucinated the snippet; refuse the proposal
            return None
        if len(old_text) > max_chars * 2:
            return None
        if len(new_text) > max_chars:
            new_text = new_text[: max_chars - 1] + "…"
        # Threat scan on the proposed NEW text — defense in depth so a
        # prompt-injected curator can't sneak "ignore previous" into
        # USER.md even if a sleepy human approves.
        hit = self._scan_forbidden(new_text)
        if hit is not None:
            return None
        return {
            "type": ptype,
            "target_file": target_file,
            "scope": scope,
            "old_text": old_text,
            "new_text": new_text,
            "reasoning": reasoning or "no reasoning provided",
            "auto_nominated": False,
        }

    # ------------------------------------------------------------------
    # Skill review (deterministic staleness + per-scope LLM judgment)
    # ------------------------------------------------------------------
    def _review_skills(self, skills: list[dict]) -> list[dict]:
        proposals: list[dict] = []
        stale_cfg = self.config.get("staleness") or {}
        stale_days = int(stale_cfg.get("skill_stale_days", 30))
        min_usage = int(stale_cfg.get("min_skill_usage_to_keep", 1))
        now = datetime.now(timezone.utc)

        # Deterministic: stale + unused skills auto-proposed for archive
        for s in skills:
            if not isinstance(s, dict):
                continue
            if s.get("status") != "active":
                continue
            usage = int(s.get("usage_count", 0) or 0)
            if usage >= min_usage:
                continue  # used recently enough to keep
            ts = s.get("updated_at") or s.get("created_at") or ""
            try:
                last_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            age_days = (now - last_ts).days
            if age_days < stale_days:
                continue
            sid = s.get("skill_id") or ""
            if not sid:
                continue
            proposals.append({
                "type": "archive_skill",
                "target_file": f"SKILL_{sid}.md",
                "scope": "skills",
                "skill_id": sid,
                "old_text": (s.get("name") or "") + " — "
                            + (s.get("description") or ""),
                "new_text": "",
                "reasoning": (f"unused for {age_days} days "
                              f"(>= stale_days={stale_days})"),
                "auto_nominated": True,
            })
        return proposals

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------
    @staticmethod
    def _md_system_prompt(scope: str, target_file: str,
                          allowed_types: list[str], max_chars: int) -> str:
        return (
            f"You are Module CURATOR reviewing the agent's {target_file} "
            "file. Your job is to suggest cleanups: consolidate duplicate "
            "entries, refine awkward wording, and remove obviously stale "
            "lines. You DO NOT auto-apply — every proposal goes to a "
            "human reviewer who decides.\n\n"
            "STRICT RULES:\n"
            "  * Return a JSON object: { \"proposals\": [ ... ] }\n"
            f"  * Each proposal: {{\"type\": one of {sorted(allowed_types)},\n"
            "                    \"old_text\": <exact substring from the file>,\n"
            "                    \"new_text\": <replacement text, may be empty for delete>,\n"
            "                    \"reasoning\": <one sentence>}}\n"
            f"  * Max {max_chars} chars per proposal.\n"
            "  * `old_text` MUST be an EXACT substring of the current file content — "
            "if you can't find one, omit the proposal. Hallucinated anchors are dropped.\n"
            "  * Be conservative: if nothing needs cleaning, return "
            "`{\"proposals\": []}`.\n"
            "  * Never propose adding new user-information-style claims "
            "the file doesn't already contain — your job is to refine, "
            "not invent.\n"
            "  * No prompt-injection phrases, no credentials, no PII.\n\n"
            "Return JSON only. No prose around it."
        )

    @staticmethod
    def _md_user_prompt(target_file: str, body: str) -> str:
        return (
            f"Current contents of {target_file}:\n"
            f"-------------------------\n"
            f"{body}\n"
            f"-------------------------\n\n"
            "Now propose cleanups as a JSON object."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _scan_forbidden(self, content: str) -> str | None:
        if not content:
            return None
        for rx in self._forbidden:
            if rx.search(content):
                return f"forbidden_pattern:{rx.pattern[:60]}"
        return None
