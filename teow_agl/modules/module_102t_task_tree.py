"""
Module 102T — Task Tree Planner.

For complex user goals (multi-step / numbered / explicitly chained),
runtime asks 102T to break the goal into 2-8 sub-goals BEFORE running
102 (the per-leaf planner). Each leaf then goes through the full
governance pipeline independently:

    USER GOAL
        │
        ▼
      102T            ← this module — decomposes into leaves
        │
        ▼
   ┌────┴────┬────┐
   ▼         ▼    ▼
  sg_a    sg_b   sg_c     ← each leaf runs the FULL pipeline:
                            101A → 102 → 102B → 101B → 103 → 105
                            → 107 → 110, with completed leaves'
                            outputs threaded into downstream briefs.

This module is **pure planning** with two responsibilities only:

  1. `decompose(...)` — call the chat LLM with the decomposer system
     prompt, parse the response into a TaskTree, validate it.
  2. `topo_sort(...)` — produce an execution order honoring depends_on
     (returns ValueError on cycles or unknown ids).

It does NOT execute leaves — that's the runtime's job (in `_run_tree`).

Why a separate module from 102? Two reasons:
  * 102 is a *single-action* planner with a different system prompt
    (it picks tools and writes content). Mixing the two would muddy
    both prompts and make the audit story harder.
  * 102T is invoked at most once per task; 102 runs once per leaf.
    Keeping them separate lets the runtime skip 102T entirely on
    simple goals, paying zero LLM cost.

The complexity heuristic (`needs_decomposition`) is heuristic-only
(no LLM call) so cheap chit-chat doesn't pay for a decomposer turn.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ..adapters.chat_llm import ChatLLM
from ..models import SubGoal, TaskEnvelope, TaskTree


class TaskTreeModule:
    """Module 102T. Decomposer + topo-sort. No side effects."""

    module_id = "102T"

    def __init__(
        self,
        *,
        chat_llm: ChatLLM,
        system_prompt: str,
        config: dict | None = None,
    ) -> None:
        self.chat_llm = chat_llm
        self.system_prompt = system_prompt
        self.config = config or {}
        # Pre-compute the heuristic word sets so needs_decomposition
        # stays O(N) over the goal text.
        h = (self.config.get("complexity_heuristic") or {})
        self._min_chars = int(h.get("min_chars", 120))
        self._multi_imp_min = int(h.get("multi_imperative_min_verbs", 3))
        self._trigger_phrases = tuple(
            (p or "").lower() for p in (h.get("trigger_phrases") or [])
        )
        self._verbs_en = frozenset(
            (v or "").lower() for v in (h.get("imperative_verbs_en") or [])
        )
        self._verbs_zh = frozenset(
            (v or "") for v in (h.get("imperative_verbs_zh") or [])
        )
        # Tree bounds
        limits = (self.config.get("tree_limits") or {})
        self.max_leaves = int(limits.get("max_leaves_per_tree", 8))
        self.min_leaves = int(limits.get("min_leaves_per_tree", 2))

    # ------------------------------------------------------------------
    # Cheap heuristic — should runtime call decompose()?
    # ------------------------------------------------------------------
    def needs_decomposition(self, raw_goal: str) -> bool:
        """Returns True iff the user goal looks complex enough to be
        worth N×LLM calls. Conservative — see config docs."""
        text = (raw_goal or "").strip()
        if not text or not self.config.get("enabled", True):
            return False
        lowered = text.lower()
        if len(text) >= self._min_chars:
            return True
        for p in self._trigger_phrases:
            if p and p in lowered:
                return True
        # Imperative-verb count — EN tokenization
        tokens = re.findall(r"[A-Za-z]+", lowered)
        en_hits = sum(1 for t in tokens if t in self._verbs_en)
        # Chinese verbs are substrings (no word boundaries)
        zh_hits = sum(1 for v in self._verbs_zh if v and v in text)
        if (en_hits + zh_hits) >= self._multi_imp_min:
            return True
        return False

    # ------------------------------------------------------------------
    # decompose() — talks to the chat LLM, returns TaskTree or None
    # ------------------------------------------------------------------
    def decompose(self, envelope: TaskEnvelope) -> TaskTree | None:
        """Ask the LLM to break this goal into a flat tree of sub-goals.

        Returns:
            * TaskTree on success (validated + topo-sorted)
            * None on any failure (LLM error, refusal, validation fail)
              — the runtime falls back to single-shot mode silently.

        Never raises.
        """
        user_intent = (envelope.normalized_goal or envelope.raw_goal or "").strip()
        if not user_intent:
            return None
        user_prompt = (
            f"User goal:\n{user_intent}\n\n"
            "Decompose into 2-8 sub-goals as the JSON described in the "
            "system prompt. If this is a single-action goal, refuse with "
            '{"refusal": "not_decomposable"}.'
        )
        try:
            raw = self.chat_llm.chat_json(
                system=self.system_prompt, user=user_prompt, max_tokens=1500,
            )
        except Exception:
            return None
        if not isinstance(raw, dict) or not raw:
            return None
        if raw.get("refusal"):
            return None

        leaves_raw = raw.get("leaves")
        if not isinstance(leaves_raw, list) or not leaves_raw:
            return None

        # Clamp to max_leaves (truncate per config.fallback.on_overflow)
        overflow_policy = (
            self.config.get("fallback") or {}
        ).get("on_overflow", "truncate")
        if len(leaves_raw) > self.max_leaves:
            if overflow_policy == "truncate":
                leaves_raw = leaves_raw[: self.max_leaves]
            else:
                return None
        if len(leaves_raw) < self.min_leaves:
            return None

        # Coerce + validate each leaf
        leaves: list[SubGoal] = []
        seen_ids: set[str] = set()
        for item in leaves_raw:
            if not isinstance(item, dict):
                continue
            sgid = str(item.get("sub_goal_id") or "").strip()
            if not sgid:
                sgid = "sg_" + uuid.uuid4().hex[:6]
            # Avoid id collisions (rename rather than drop so the LLM's
            # ordering intent survives)
            if sgid in seen_ids:
                sgid = sgid + "_" + uuid.uuid4().hex[:4]
            seen_ids.add(sgid)
            desc = str(item.get("description") or "").strip()
            if not desc:
                continue  # empty leaf is useless — drop
            deps_raw = item.get("depends_on") or []
            deps = [str(d) for d in deps_raw if isinstance(d, str)]
            leaves.append(SubGoal(
                sub_goal_id=sgid, description=desc, depends_on=deps,
            ))
        if len(leaves) < self.min_leaves:
            return None

        # Drop dependencies that point to unknown ids (LLM hallucination)
        known = {l.sub_goal_id for l in leaves}
        for l in leaves:
            l.depends_on = [d for d in l.depends_on if d in known]

        # Topological sort — if cycles, refuse decomposition.
        try:
            order = self.topo_sort(leaves)
        except ValueError:
            return None

        tree_id = str(raw.get("tree_id") or "").strip()
        if not tree_id:
            tree_id = "tree_" + uuid.uuid4().hex[:10]

        return TaskTree(
            tree_id=tree_id,
            parent_task_id=envelope.task_id,
            root_goal=user_intent,
            leaves=leaves,
            order=order,
            reasoning=str(raw.get("reasoning") or "")[:300],
        )

    # ------------------------------------------------------------------
    # topo_sort — Kahn's algorithm. Raises ValueError on cycles.
    # ------------------------------------------------------------------
    @staticmethod
    def topo_sort(leaves: list[SubGoal]) -> list[str]:
        in_degree: dict[str, int] = {l.sub_goal_id: 0 for l in leaves}
        adj: dict[str, list[str]] = {l.sub_goal_id: [] for l in leaves}
        for l in leaves:
            for dep in l.depends_on:
                if dep in adj:
                    adj[dep].append(l.sub_goal_id)
                    in_degree[l.sub_goal_id] += 1
        # Start with roots (in_degree 0), preserve LLM-declared order
        # among them so siblings run in the order they were emitted.
        order: list[str] = []
        ready = [l.sub_goal_id for l in leaves if in_degree[l.sub_goal_id] == 0]
        while ready:
            sid = ready.pop(0)
            order.append(sid)
            for nxt in adj.get(sid, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    ready.append(nxt)
        if len(order) != len(leaves):
            raise ValueError("task_tree_cycle_detected")
        return order
