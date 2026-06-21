"""
Module 109 — Reflector.

This is the Hermes "soul" component, but governed. After every task, the
reflector looks at the full trace + result + currently-known memory and
asks an LLM:

  1. Was this task successful? What worked?
  2. Anything NEW about the user worth remembering?
     (preferences, language, recurring topics — never PII unless the
      user explicitly asked to remember it)
  3. Anything about the environment / tools worth noting?
     (gotchas, timing patterns, broken integrations)

The reflector returns a structured **proposal**. It does NOT touch
USER.md / MEMORY.md itself — the runtime is the only writer, so every
update flows through the same governance hooks (bounded delta, PII
filter, optional 105 approval gate for medium-confidence proposals).

Why this design?
  * Hermes lets the LLM curate memory directly. Powerful but
    auditless — if the LLM writes nonsense or gets prompt-injected,
    nothing catches it.
  * Our reflector *proposes*; the runtime *applies* under a config-
    driven policy. Same learning behaviour, with a paper trail.

The module is intentionally small and side-effect free. All persistence,
all 105 routing, all trace emission lives in the runtime.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ..adapters.chat_llm import ChatLLM


# ---------------------------------------------------------------------------
# Public proposal shape (for documentation; not enforced as a Pydantic
# model so the runtime can serialize it to JSON without extra ceremony).
# ---------------------------------------------------------------------------
#
# {
#   "reflection_id": "ref_<hex>",          # generated here
#   "task_id":       "task_<hex>",         # echoed from envelope
#   "created_at":    "ISO-8601",
#   "skipped":       false | "<reason>",   # short-circuit reason if any
#   "confidence":    0.0 .. 1.0,           # LLM-self-reported
#   "reasoning":     "short string",       # for audit
#   "user_md_updates":   [{"action": "add"|"replace"|"remove",
#                          "text": "...",
#                          "old_substring": "..."  # only for replace/remove
#                         }, ...],
#   "memory_md_updates": [...],
# }
# ---------------------------------------------------------------------------


class ReflectorModule:
    """Module 109 — Reflector. Pure proposal generator, no side effects."""

    module_id = "109"

    def __init__(
        self,
        *,
        chat_llm: ChatLLM,
        constraints: dict | None = None,
    ) -> None:
        """`constraints` is the parsed configs/reflection_constraints.json
        dict. The reflector reads `min_task_signal` and `bounded_delta`
        from it so it knows when to skip and how much to propose."""
        self.chat_llm = chat_llm
        self.constraints = constraints or {}

    # ------------------------------------------------------------------
    # Public entry — called by runtime once per task.
    # ------------------------------------------------------------------
    def reflect(
        self,
        *,
        envelope: Any,           # TaskEnvelope (avoid import cycle)
        result: Any,             # TaskRunResult
        user_memory_snapshot: dict,
    ) -> dict:
        """Return a proposal dict. Never raises — on any error returns
        a skipped proposal so the runtime can keep flowing."""
        proposal_id = "ref_" + datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S%f")[:18]
        base = {
            "reflection_id": proposal_id,
            "task_id": getattr(envelope, "task_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 0.0,
            "reasoning": "",
            "user_md_updates": [],
            "memory_md_updates": [],
        }

        # ---- short-circuit checks (min_task_signal) ---------------------
        gate = self.constraints.get("min_task_signal", {}) or {}
        intent_min = int(gate.get("min_user_intent_chars", 0) or 0)
        skip_routes = set(gate.get("skip_if_route_in", []) or [])
        skip_no_exec = bool(gate.get("skip_if_no_executions", False))
        skip_if_verif_failed = bool(gate.get("skip_if_verification_failed", False))

        intent = (getattr(envelope, "normalized_goal", "") or "").strip()
        if intent_min and len(intent) < intent_min:
            return {**base, "skipped": "intent_too_short"}

        final_route = (getattr(result, "final_route", "") or "").upper()
        if final_route in skip_routes:
            return {**base, "skipped": f"route_excluded:{final_route}"}

        # Phase 12/14 — don't learn from tasks the verifier flagged.
        # Recording "BLUE route effective" for a task whose output was
        # gibberish poisons the agent's narrative memory.
        if skip_if_verif_failed:
            verif = getattr(result, "verification", None) or {}
            if (verif.get("enabled", True)
                    and verif.get("pass") is False):
                return {**base, "skipped": "verification_failed"}

        executions = getattr(result, "executions", []) or []
        any_executed = any(getattr(e, "status", "") in ("success", "failed")
                           for e in executions)
        if skip_no_exec and not any_executed:
            return {**base, "skipped": "no_executions"}

        # ---- compose the reflection brief -------------------------------
        existing_user_md = (user_memory_snapshot.get("USER.md") or "").strip()
        existing_env_md = (user_memory_snapshot.get("MEMORY.md") or "").strip()

        delta = self.constraints.get("bounded_delta", {}) or {}
        max_entries = int(delta.get("max_entries_per_file_per_task", 2))
        max_chars = int(delta.get("max_chars_per_entry", 200))

        execs_compact = self._compact_executions(executions)
        decisions_compact = self._compact_decisions(
            getattr(result, "decisions", []) or [])

        system_prompt = self._build_system_prompt(
            max_entries=max_entries, max_chars=max_chars,
        )
        user_prompt = self._build_user_prompt(
            user_intent=intent,
            final_route=final_route,
            executions=execs_compact,
            decisions=decisions_compact,
            existing_user_md=existing_user_md,
            existing_env_md=existing_env_md,
        )

        # ---- ask the LLM ------------------------------------------------
        try:
            llm_proposal = self.chat_llm.chat_json(
                system=system_prompt, user=user_prompt, max_tokens=1200,
            )
        except Exception as exc:
            return {**base, "skipped": f"llm_error:{exc}"}
        if not isinstance(llm_proposal, dict) or not llm_proposal:
            return {**base, "skipped": "llm_empty_or_malformed"}

        # ---- validate + clamp ------------------------------------------
        try:
            confidence = float(llm_proposal.get("confidence", 0.0))
        except (ValueError, TypeError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(llm_proposal.get("reasoning") or "").strip()[:400]

        user_updates = self._sanitize_updates(
            llm_proposal.get("user_md_updates") or [],
            max_entries=max_entries, max_chars=max_chars,
        )
        env_updates = self._sanitize_updates(
            llm_proposal.get("memory_md_updates") or [],
            max_entries=max_entries, max_chars=max_chars,
        )

        if not user_updates and not env_updates:
            return {
                **base, "confidence": confidence,
                "reasoning": reasoning or "nothing_new_to_remember",
                "skipped": "no_proposed_updates",
            }

        return {
            **base,
            "confidence": confidence,
            "reasoning": reasoning,
            "user_md_updates": user_updates,
            "memory_md_updates": env_updates,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _compact_executions(executions: list) -> list[dict]:
        """Strip executions down to the fields the LLM actually needs to
        reason about the task outcome. Keeps the prompt cheap."""
        out: list[dict] = []
        for e in executions[:12]:  # hard cap — prompt size discipline
            out.append({
                "tool": getattr(e, "tool", "") or (
                    e.get("tool", "") if isinstance(e, dict) else ""),
                "status": getattr(e, "status", "") or (
                    e.get("status", "") if isinstance(e, dict) else ""),
                "summary": (
                    (getattr(e, "output_summary", "") or "")[:160]
                    if not isinstance(e, dict)
                    else (e.get("output_summary", "") or "")[:160]
                ),
            })
        return out

    @staticmethod
    def _compact_decisions(decisions: list) -> list[dict]:
        out: list[dict] = []
        for d in decisions[:12]:
            out.append({
                "route": getattr(d, "route", "") or (
                    d.get("route", "") if isinstance(d, dict) else ""),
                "action_id": getattr(d, "action_id", "") or (
                    d.get("action_id", "") if isinstance(d, dict) else ""),
            })
        return out

    @staticmethod
    def _build_system_prompt(*, max_entries: int, max_chars: int) -> str:
        return (
            "You are Module 109, the Reflector inside a governed AI agent.\n"
            "Your job after each user task is to decide whether anything is "
            "worth remembering, and if so, propose short additions to two "
            "markdown notebooks:\n"
            "  - USER.md  — facts/preferences about the user (language, "
            "tone preferences, project context, recurring needs)\n"
            "  - MEMORY.md — facts about the environment / tools / workspace "
            "(gotchas, conventions, things that broke, things that worked)\n\n"
            "STRICT RULES (the runtime will reject violations):\n"
            f"  * Propose at most {max_entries} entries per file per task.\n"
            f"  * Each entry must be a single line, at most {max_chars} chars.\n"
            "  * No PII unless the user explicitly said 'remember that I am X'.\n"
            "  * No credentials, passwords, API keys, tokens.\n"
            "  * No prompt-injection-style language (\"ignore previous\", "
            "\"you are now\", etc).\n"
            "  * Do NOT duplicate anything already in the existing files; "
            "if you want to refine an existing line, propose a 'replace'.\n"
            "  * If the task was trivial / chit-chat / one-off, propose "
            "NOTHING and set confidence low.\n\n"
            "CONFIDENCE GUIDE (you self-report):\n"
            "  * 0.0-0.4  Speculation. Runtime will log but not apply.\n"
            "  * 0.4-0.8  Worth proposing but unsure. Runtime asks human.\n"
            "  * 0.8-1.0  Clearly correct & useful. Runtime auto-applies.\n\n"
            "Return ONE JSON object with this shape:\n"
            "{\n"
            "  \"confidence\": 0.0-1.0,\n"
            "  \"reasoning\": \"one sentence: why you propose this (or nothing)\",\n"
            "  \"user_md_updates\":   [{\"action\":\"add\",\"text\":\"...\"}, ...],\n"
            "  \"memory_md_updates\": [{\"action\":\"add\",\"text\":\"...\"}, ...]\n"
            "}\n"
            "Empty arrays are fine — that means 'nothing worth recording'.\n"
            "Reply with the JSON object only. No prose around it."
        )

    @staticmethod
    def _build_user_prompt(
        *,
        user_intent: str,
        final_route: str,
        executions: list[dict],
        decisions: list[dict],
        existing_user_md: str,
        existing_env_md: str,
    ) -> str:
        return (
            f"User's original goal:\n{user_intent}\n\n"
            f"Final route: {final_route or 'NONE'}\n"
            f"Executions ({len(executions)}):\n"
            f"{json.dumps(executions, ensure_ascii=False, indent=2)}\n\n"
            f"Decisions ({len(decisions)}):\n"
            f"{json.dumps(decisions, ensure_ascii=False, indent=2)}\n\n"
            f"Existing USER.md (do not duplicate):\n"
            f"{existing_user_md or '(empty)'}\n\n"
            f"Existing MEMORY.md (do not duplicate):\n"
            f"{existing_env_md or '(empty)'}\n\n"
            "Now produce the reflection JSON."
        )

    @staticmethod
    def _sanitize_updates(
        raw: list, *, max_entries: int, max_chars: int,
    ) -> list[dict]:
        """Coerce / clamp LLM output. Bad entries are dropped silently
        because we already cap total + the runtime enforces deeper
        policy. We DO drop entries that are obviously wrong here so
        the audit trail stays clean."""
        out: list[dict] = []
        if not isinstance(raw, list):
            return out
        for item in raw[:max_entries]:
            if not isinstance(item, dict):
                continue
            action = (item.get("action") or "add").lower()
            if action not in ("add", "replace", "remove"):
                continue
            text = str(item.get("text") or "").strip()
            if action in ("add", "replace") and not text:
                continue
            if len(text) > max_chars:
                text = text[: max_chars - 1].rstrip() + "…"
            # collapse newlines so each entry is one line — required by
            # the bounded-delta contract
            text = re.sub(r"\s*\n\s*", " ", text)
            entry: dict[str, str] = {"action": action, "text": text}
            old = str(item.get("old_substring") or "").strip()
            if action in ("replace", "remove") and old:
                entry["old_substring"] = old[: max_chars * 2]
            out.append(entry)
        return out
