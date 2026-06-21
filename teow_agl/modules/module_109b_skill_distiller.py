"""
Module 109B — Skill Distiller (Phase 1A of Learning Roadmap v2).

After a task finishes, this module decides whether the agent should
DISTILL the successful work into a procedural-memory artifact (a
SKILL_<id>.md). Unlike Module 109 Reflector — which writes declarative
notes (USER.md / MEMORY.md) — 109B handles PROCEDURAL memory.

Pipeline:
    runtime._after_run
        -> 109   Reflector          (declarative: USER.md / MEMORY.md)
        -> 109B  Skill Distiller    (procedural: skill proposals)
              -> 8 trigger checks across 6 dimensions
              -> LLM drafts a short SOP markdown
              -> Layer-1 PII gate (forbidden_patterns + hard_reject + redact)
              -> Curator proposals queue  (kind = "create_skill")
        -> session_index update

User then sees the proposal at /api/curator/proposals, approves it, and
the runtime's _apply_curator_proposal calls SkillManager.create_skill —
which runs Layer-2 PII gate before writing state/skills/SKILL_<id>.md.

This module never writes skills directly. Only proposes. Human-in-loop
is the contract.

See LEARNING_SYSTEM_ROADMAP_v2.md §3 for the full design rationale.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Outcomes considered a "failure" in the recent-window check (check 7).
# Subjective: "rejection" and "infeasible" we DON'T treat as failures
# (they're correct governance outcomes, not quality problems) — only
# `failure` (execution / verifier failed) signals "the system isn't
# reliable on this category right now".
_FAILURE_OUTCOMES = {"failure"}


class SkillDistiller:
    """Module 109B. Side-effect-free decision + draft producer.

    Constructor wires in:
      - chat_llm: the LLM used to draft the SKILL markdown (Phase 1A
        uses the same Qwen3 as everything else; Phase 2 will add a
        separate SKILL_ABSTRACTION_LLM for principle extraction).
      - skill_manager: needed for the dedupe check (find_active_for).
      - subject_confidence: cumulative + recent-window stats.
      - constraints: parsed configs/skill_constraints.json — both the
        `distiller` block (trigger thresholds) AND `pii_extra_patterns`
        (hard_reject / redact) AND the pre-existing `forbidden_patterns`
        (prompt-injection / API key shapes).

    Tracks an in-process per-day proposal counter (`_proposals_today`)
    so the `max_per_day` rate limit holds without needing a DB query
    on every task. Resets at UTC midnight.
    """

    module_id = "109B"

    def __init__(
        self,
        *,
        chat_llm: Any,
        skill_manager: Any,
        subject_confidence: Any,
        constraints: dict | None = None,
        abstraction_prompt_path: str | Path | None = None,
    ) -> None:
        self.chat_llm = chat_llm
        self.skill_manager = skill_manager
        self.subject_confidence = subject_confidence
        self.constraints = constraints or {}
        # Phase 2 — path to the abstraction system prompt. Lazy-read at
        # first use; missing/unreadable falls back to an inline default
        # so the Distiller is never blocked by a typo in the path.
        self.abstraction_prompt_path = (
            Path(abstraction_prompt_path)
            if abstraction_prompt_path else None
        )
        self._abstraction_prompt_cached: str | None = None

        # --- pre-compile every regex once at startup -------------------
        # 1) Pre-existing forbidden_patterns (prompt injection + API keys)
        self._compiled_forbidden: list[re.Pattern] = []
        forbidden_section = (self.constraints.get("forbidden_patterns")
                             or {})
        for raw in forbidden_section.get("patterns", []) or []:
            try:
                self._compiled_forbidden.append(re.compile(raw))
            except re.error:
                continue

        # 2) pii_extra_patterns.hard_reject
        self._compiled_hard_reject: dict[str, re.Pattern] = {}
        pii = self.constraints.get("pii_extra_patterns") or {}
        for name, raw in (pii.get("hard_reject") or {}).items():
            try:
                self._compiled_hard_reject[name] = re.compile(raw)
            except re.error:
                continue

        # 3) pii_extra_patterns.redact (pattern + replacement)
        self._compiled_redact: dict[str, dict] = {}
        for name, spec in (pii.get("redact") or {}).items():
            if not isinstance(spec, dict):
                continue
            try:
                self._compiled_redact[name] = {
                    "pattern": re.compile(spec.get("pattern", "")),
                    "replacement": spec.get("replacement", ""),
                }
            except re.error:
                continue

        # In-process daily counter (UTC). Reset on date change.
        self._proposals_today_date: str = ""
        self._proposals_today_count: int = 0

    # ==================================================================
    # Public entry — runtime calls this from _after_run
    # ==================================================================
    def maybe_propose(
        self,
        *,
        task_result: Any,
        plan_shape: str = "",
    ) -> dict | None:
        """Decide whether to propose a skill, and if so produce a draft.

        Returns:
          - None if any trigger check fails (most common case — silent)
          - A proposal dict ready for the Curator queue, when all 8
            checks pass AND the PII gate (Layer 1) allows it.

        Never raises. Logs are emitted by the caller (runtime) via
        the audit events in the returned proposal.
        """
        cfg = self.constraints.get("distiller") or {}

        # Env override for demo / dev (single env var, not 1 per setting)
        if os.environ.get("SKILL_AUTO_PROPOSE", "1").lower() in (
                "0", "false", "no", "off"):
            return None

        category = getattr(task_result, "task_category", "") or ""
        if not self._should_propose(task_result, plan_shape, cfg):
            return None

        # --- Draft the skill markdown via LLM --------------------------
        draft = self._draft_skill_from_task(task_result)
        if not draft or not draft.get("procedure"):
            return None  # LLM returned nothing useful — silently skip

        # --- Phase 2: abstraction pass (best-effort) -------------------
        # Lift the raw draft into (principle, parameters) using a
        # SEPARATE, stronger LLM (default GPT-4o-mini). On any failure
        # we leave principle/parameters empty — the proposal still
        # proceeds with the raw procedure (Phase-1A behaviour).
        abstraction = self._abstract_skill_from_draft(draft)
        principle_raw = str(abstraction.get("principle") or "").strip()
        parameters_raw = abstraction.get("parameters") or {}
        if not isinstance(parameters_raw, dict):
            parameters_raw = {}
        abstraction_model = abstraction.get("_model_used", "skipped")

        # --- Layer-1 PII gate (per field — redact propagates correctly) -
        # Scan each field separately so redact substitutions land back
        # in that specific field. hard_reject in ANY field still kills
        # the whole proposal.
        #
        # Phase 2: principle is scanned as plain text; parameters is
        # scanned via its JSON serialisation (so a leaked email inside
        # a "audience" value still hits the gate). We do NOT redact INTO
        # parameters — if a redact pattern fires there, we drop the
        # whole proposal (a JSON value can't safely carry a redacted
        # placeholder without breaking the schema downstream).
        cleaned_fields: dict[str, str] = {}
        all_audit: list[str] = []
        for field_name in ("name", "description", "procedure"):
            ok, cleaned, audit = self._pii_scan_propose(
                draft.get(field_name, ""))
            all_audit.extend(audit)
            if not ok:
                return {
                    "kind": "create_skill_blocked",
                    "reason": audit[0] if audit else "pii_blocked",
                    "field": field_name,
                    "audit": all_audit,
                    "source_task_id": getattr(task_result, "task_id", ""),
                    "source_category": category,
                }
            cleaned_fields[field_name] = cleaned

        # Principle scan — text field, redact OK
        principle_clean = ""
        if principle_raw:
            ok, cleaned, audit = self._pii_scan_propose(principle_raw)
            all_audit.extend(audit)
            if not ok:
                return {
                    "kind": "create_skill_blocked",
                    "reason": audit[0] if audit else "pii_blocked",
                    "field": "principle",
                    "audit": all_audit,
                    "source_task_id": getattr(task_result, "task_id", ""),
                    "source_category": category,
                }
            principle_clean = cleaned

        # Parameters scan — block on hard_reject hits (JSON values
        # can't safely take a redaction placeholder).
        parameters_clean: dict = {}
        if parameters_raw:
            params_dump = json.dumps(parameters_raw, ensure_ascii=False)
            ok, _, audit = self._pii_scan_propose(params_dump)
            all_audit.extend(audit)
            if not ok:
                return {
                    "kind": "create_skill_blocked",
                    "reason": audit[0] if audit else "pii_blocked",
                    "field": "parameters",
                    "audit": all_audit,
                    "source_task_id": getattr(task_result, "task_id", ""),
                    "source_category": category,
                }
            # If a redact event fired anywhere in the parameters dump
            # we drop them too (defence in depth — see comment above).
            if any(evt.startswith("skill_redacted_") for evt in audit):
                parameters_clean = {}
            else:
                parameters_clean = dict(parameters_raw)

        # Bump daily counter only AFTER all checks pass.
        self._bump_today()

        return {
            "kind": "create_skill",
            "name": cleaned_fields["name"],
            "description": cleaned_fields["description"],
            "procedure": cleaned_fields["procedure"],
            "principle": principle_clean,
            "parameters": parameters_clean,
            "tags": draft.get("tags") or [],
            "source_task_id": getattr(task_result, "task_id", ""),
            "source_category": category,
            "source_shape": plan_shape,
            "audit": all_audit,  # redact events, if any
            "draft_model": getattr(self.chat_llm, "backend", "unknown"),
            "abstraction_model": abstraction_model,
        }

    # ==================================================================
    # Internals
    # ==================================================================

    # ---- 8 trigger checks across 6 dimensions ------------------------
    def _should_propose(
        self,
        task_result: Any,
        plan_shape: str,
        cfg: dict,
    ) -> bool:
        # [dim ①] check 1 — enabled
        if not cfg.get("enabled", True):
            return False

        category = getattr(task_result, "task_category", "") or ""

        # [dim ②] check 2 — category not in exclude list
        excluded = set(cfg.get("exclude_categories", []) or [])
        if not category or category in excluded:
            return False

        # [dim ③] check 3 — this task verifier-passed
        verification = getattr(task_result, "verification", None) or {}
        if not verification.get("pass", False):
            return False

        # [dim ③] check 4 — route is BLUE or GREEN
        final_route = (getattr(task_result, "final_route", "") or "").upper()
        if final_route not in ("BLUE", "GREEN"):
            return False

        # [dim ④] check 5 — cumulative successes >= threshold
        # Env override SKILL_AUTO_PROPOSE_MIN_SUCCESSES wins for demo use.
        env_min = os.environ.get("SKILL_AUTO_PROPOSE_MIN_SUCCESSES", "")
        try:
            min_successes = int(env_min) if env_min else int(
                cfg.get("min_successes", 10))
        except (ValueError, TypeError):
            min_successes = int(cfg.get("min_successes", 10))

        agg = self.subject_confidence.aggregate_for(category)
        if agg.get("successes", 0) < min_successes:
            return False

        # [dim ④] check 6 — cumulative success rate >= threshold
        total_outcomes = (agg.get("successes", 0)
                          + agg.get("failures", 0))
        if total_outcomes > 0:
            sr = agg["successes"] / total_outcomes
            if sr < float(cfg.get("min_success_rate", 0.80)):
                return False

        # [dim ⑤] check 7 — recent N outcomes have no failure
        recent_window = int(cfg.get("recent_window", 5))
        recent = self.subject_confidence.recent_outcomes(
            category, n=recent_window)
        if any(o in _FAILURE_OUTCOMES for o in recent):
            return False

        # [dim ⑥] check 8a — no active skill for same (category, shape)
        existing = self.skill_manager.find_active_for(
            category=category, plan_shape=plan_shape or None,
        )
        if existing is not None:
            return False

        # [dim ⑥] check 8b — daily rate limit
        env_max = os.environ.get("SKILL_AUTO_PROPOSE_MAX_PER_DAY", "")
        try:
            max_per_day = int(env_max) if env_max else int(
                cfg.get("max_per_day", 3))
        except (ValueError, TypeError):
            max_per_day = int(cfg.get("max_per_day", 3))

        if self._today_count() >= max_per_day:
            return False

        return True

    # ---- LLM draft ---------------------------------------------------
    def _draft_skill_from_task(self, task_result: Any) -> dict | None:
        """Ask the chat LLM to write a short SKILL markdown for this task.

        Returns dict with keys {name, description, procedure, tags}.
        On LLM failure / empty response / unparseable output, returns None.
        Falls back GRACEFULLY (None) — the absence of a proposal is the
        same as the LLM saying "nothing worth saving here".
        """
        cfg = self.constraints.get("distiller") or {}
        max_tokens = int(cfg.get("draft_max_tokens", 1500))

        # Build a compact context for the LLM. Avoid dumping the full
        # plan / executions; only what's needed to describe HOW the
        # task was solved (so the SKILL is a generalisable note, not
        # a verbatim replay of this one task).
        user_intent = getattr(task_result, "user_intent", "") or ""
        category = getattr(task_result, "task_category", "") or ""
        action_summary = self._summarise_actions(task_result)

        sys_prompt = (
            "You are writing a short procedural-memory note (a SKILL) "
            "for an AI agent. The agent just successfully completed a "
            "task; we want a reusable SOP so similar future tasks can "
            "reference it. Output ONE JSON object only, with these "
            "keys:\n"
            '  "name":        short slug-y title (3-7 words)\n'
            '  "description": one sentence summarising what the SKILL '
            'helps with (used for retrieval)\n'
            '  "procedure":   numbered markdown steps (4-8 steps), '
            'describing HOW the task was solved at a generalisable '
            'level (not verbatim)\n'
            '  "tags":        3-5 lowercase keywords for indexing\n\n'
            "Rules: write substantive content (each step >= one full "
            "sentence). Do NOT include the user's verbatim prompt — "
            "abstract the method. Do NOT include emails, phone "
            "numbers, file paths with usernames, or API keys. Match "
            "the user's language."
        )
        user_prompt = (
            f"Task category: {category}\n"
            f"User's original request: {user_intent[:300]}\n\n"
            f"What the agent did:\n{action_summary}\n\n"
            "Return the SKILL JSON now."
        )

        try:
            obj = self.chat_llm.chat_json(
                system=sys_prompt, user=user_prompt,
                max_tokens=max_tokens,
            )
        except Exception:
            return None
        if not isinstance(obj, dict):
            return None

        name = str(obj.get("name") or "").strip()
        description = str(obj.get("description") or "").strip()
        procedure = str(obj.get("procedure") or "").strip()
        tags_raw = obj.get("tags") or []
        if isinstance(tags_raw, str):
            tags_raw = [tags_raw]
        tags = [str(t).strip().lower() for t in tags_raw if t]

        if not name or not description or not procedure:
            return None

        return {
            "name": name,
            "description": description,
            "procedure": procedure,
            "tags": tags,
        }

    # ---- Phase 2: abstraction pass -----------------------------------
    def _load_abstraction_prompt(self) -> str:
        """Return the system prompt for the abstraction pass.

        Cached after first read. Falls back to an inline minimal prompt
        if the configured file is missing — Distiller is never blocked
        by a typo'd path.
        """
        if self._abstraction_prompt_cached is not None:
            return self._abstraction_prompt_cached
        prompt_text = ""
        if self.abstraction_prompt_path and self.abstraction_prompt_path.is_file():
            try:
                prompt_text = self.abstraction_prompt_path.read_text(
                    encoding="utf-8")
            except OSError:
                prompt_text = ""
        if not prompt_text:
            prompt_text = _INLINE_ABSTRACTION_PROMPT
        self._abstraction_prompt_cached = prompt_text
        return prompt_text

    def _abstract_skill_from_draft(self, draft: dict) -> dict:
        """Lift the raw draft into (principle, parameters) via the
        configured abstraction LLM (default GPT-4o-mini via OpenAI).

        Returns a dict with keys:
          - principle: str (may be "")
          - parameters: dict (may be {})
          - _model_used: str — populated for the proposal's audit field
            ("openai:gpt-4o-mini" / "skipped:provider_off" / "skipped:no_key" /
             "skipped:failed" / "skipped:empty")

        Never raises. On any failure returns {"principle": "",
        "parameters": {}, "_model_used": "skipped:<reason>"} so the
        caller can still ship the proposal with raw procedure only.
        """
        # Env override — same kill-switch pattern as Phase 1A
        provider = (os.environ.get("SKILL_ABSTRACTION_LLM")
                    or "openai").strip().lower()
        if provider in ("0", "false", "no", "off", "none"):
            return {"principle": "", "parameters": {},
                    "_model_used": "skipped:provider_off"}

        if provider != "openai":
            # Future providers (claude, gemini) plug in here.
            return {"principle": "", "parameters": {},
                    "_model_used": f"skipped:unknown_provider:{provider}"}

        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return {"principle": "", "parameters": {},
                    "_model_used": "skipped:no_key"}

        # The user prompt is the draft itself, in a compact form.
        # We do NOT echo the user's verbatim original prompt — only the
        # already-distilled fields.
        user_payload = {
            "name": draft.get("name", ""),
            "description": draft.get("description", ""),
            "procedure": draft.get("procedure", ""),
            "tags": draft.get("tags", []),
        }
        user_text = (
            "Here is the raw skill draft. Lift it into the "
            "principle + parameters JSON.\n\n"
            f"{json.dumps(user_payload, ensure_ascii=False, indent=2)}"
        )

        try:
            from teow_agl.adapters.openai_provider import (
                openai_chat_json, _resolve_chat_model,
            )
            obj = openai_chat_json(
                system=self._load_abstraction_prompt(),
                user=user_text,
                max_tokens=400,
                temperature=0.1,
            )
        except Exception:
            return {"principle": "", "parameters": {},
                    "_model_used": "skipped:failed"}

        if not isinstance(obj, dict) or not obj:
            return {"principle": "", "parameters": {},
                    "_model_used": "skipped:empty"}

        principle = str(obj.get("principle") or "").strip()
        params = obj.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}

        # Truncate over-eager principle output (prompt says 30 words; we
        # enforce 50 as a hard ceiling). One sentence cap — no newlines.
        if principle:
            principle = principle.replace("\n", " ").strip()
            words = principle.split()
            if len(words) > 50:
                principle = " ".join(words[:50])

        try:
            model_used = "openai:" + _resolve_chat_model()
        except Exception:
            model_used = "openai:unknown"

        return {
            "principle": principle,
            "parameters": params,
            "_model_used": model_used,
        }

    @staticmethod
    def _summarise_actions(task_result: Any) -> str:
        """Compact action+output summary the LLM can read.
        We keep it short (< ~800 chars) so the draft prompt stays cheap."""
        plan = getattr(task_result, "plan", None)
        execs = getattr(task_result, "executions", []) or []
        if plan is None:
            return "(no plan recorded)"
        action_by_id = {a.action_id: a for a in (plan.actions or [])}
        lines: list[str] = []
        for i, ex in enumerate(execs, 1):
            a = action_by_id.get(getattr(ex, "action_id", ""))
            if a is None:
                continue
            tool = (a.tool or "").lower()
            op = (a.operation or "").lower()
            status = getattr(ex, "status", "")
            output = (getattr(ex, "output_summary", "") or "")[:120]
            lines.append(f"  {i}. tool={tool}.{op} status={status} "
                         f"output={output!r}")
            if len(lines) >= 6:  # cap at 6 actions
                break
        return "\n".join(lines) if lines else "(no executions)"

    # ---- PII scanning (public — re-used by Layer-2 gate at apply time) -
    def scan_text(self, text: str) -> tuple[bool, str, list[str]]:
        """Public wrapper around the three-pass PII scan, exposed so the
        server's /decide endpoint can re-run the gate at apply time
        (Layer-2 defence — same engine, different invocation site).

        Returns (allow, cleaned_text, audit_events). When `allow` is
        False the caller MUST drop the proposal — the redact pass was
        not applied because a hard_reject or forbidden_patterns hit
        already disqualified the content.
        """
        return self._pii_scan_propose(text)

    # ---- PII scanning (Layer 1) --------------------------------------
    def _pii_scan_propose(
        self, text: str,
    ) -> tuple[bool, str, list[str]]:
        """Three-pass scan:
          1. forbidden_patterns (existing): prompt-injection + API keys
             → reject the whole proposal
          2. pii_extra_patterns.hard_reject: email / phone / CC / IDs
             → reject the whole proposal
          3. pii_extra_patterns.redact: paths / URL credentials
             → sanitize in place, keep the proposal

        Returns (allow, cleaned_text, audit_events).
        """
        audit: list[str] = []

        # Pass 1 — forbidden patterns (kill on any hit)
        for pat in self._compiled_forbidden:
            if pat.search(text):
                audit.append("skill_blocked_forbidden_pattern")
                return False, text, audit

        # Pass 2 — hard_reject PII (kill on any hit, named for audit)
        for name, pat in self._compiled_hard_reject.items():
            if pat.search(text):
                audit.append(f"skill_blocked_pii_{name}")
                return False, text, audit

        # Pass 3 — redact (sanitize, keep).
        # Use a lambda so the replacement string is treated as LITERAL
        # (not as a regex template). Without this, replacements like
        # "<USER_HOME>\\" would be parsed as a regex backreference and
        # raise re.error: bad escape — because raw "\" at end of a
        # template starts an escape that never terminates.
        cleaned = text
        for name, spec in self._compiled_redact.items():
            replacement_literal = spec["replacement"]
            new, n = spec["pattern"].subn(
                lambda _m, _r=replacement_literal: _r, cleaned)
            if n > 0:
                audit.append(f"skill_redacted_{name}_x{n}")
                cleaned = new

        return True, cleaned, audit

    # ---- Daily counter ------------------------------------------------
    def _today_iso(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _today_count(self) -> int:
        if self._proposals_today_date != self._today_iso():
            self._proposals_today_date = self._today_iso()
            self._proposals_today_count = 0
        return self._proposals_today_count

    def _bump_today(self) -> None:
        if self._proposals_today_date != self._today_iso():
            self._proposals_today_date = self._today_iso()
            self._proposals_today_count = 0
        self._proposals_today_count += 1


# ==========================================================================
# Inline fallback prompt — used when prompts/skill_abstraction_prompt.md
# is missing or unreadable. Keeps the Distiller working in test harnesses
# and minimal installs that didn't copy the prompts/ directory.
# ==========================================================================
_INLINE_ABSTRACTION_PROMPT = (
    "You lift a raw SOP into a generalisable principle and a parameter "
    "list. Output one JSON object with two keys: principle (one sentence, "
    "under 30 words, no specific tools or languages) and parameters (a "
    "JSON object listing the parts of THIS instance that would vary in a "
    "similar future task — typically tool, output_language, output_format, "
    "and 0-3 task-specific keys). If you cannot honestly abstract, return "
    '{\"principle\":\"\",\"parameters\":{}}. Hard rules: no emails, no '
    "phone numbers, no API keys, no file paths, no PII. Output JSON only."
)
