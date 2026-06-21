"""
Module 101C — Semantic Intake (L2 intent classifier).

Sits between 101A's keyword lookup (L1) and the planner. When L1 lands
in the catch-all category, a cheap LLM call classifies the goal into
ONE category from a CLOSED set. Three possible decisions:

  * ``override`` — confident label; runtime re-runs 101A's data-driven
    assessment with the label as a category override. Planning mode,
    risk weights and routing stay 100% config-driven — the LLM only
    supplies the label.
  * ``clarify``  — ambiguous; runtime answers with ONE short clarifying
    question instead of guessing ("never bluff" product rule).
  * ``abstain``  — no usable signal (LLM off/unavailable/invalid
    output/low confidence). Runtime behaves exactly as before this
    module existed.

Purity contract (mirrors 101A/101B):
  * NO category vocabulary, clarify wording, threshold or route
    literals in this file. They live in configs/semantic_intake.json
    and configs/intake_classifier.json.
  * The module can only ever return a category present in the closed
    set it was constructed with. Empty closed set ⇒ permanent abstain
    (the purity test locks this in).
  * Hard safety (L0) runs in 101A BEFORE this module and is never
    delegated to the LLM. The closed set handed to this module must
    already exclude hard-block categories — see
    ``closed_categories_from_classifier``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_NONE_LABEL = "none"
_DEFAULT_MODE_KEY = "unknown"
_ENV_KILL_SWITCH = "SEMANTIC_INTAKE_ENABLED"

_DECISION_OVERRIDE = "override"
_DECISION_CLARIFY = "clarify"
_DECISION_ABSTAIN = "abstain"


def closed_categories_from_classifier(intake_classifier: dict) -> list[str]:
    """The CLOSED label set 101C may choose from: every category that
    has a configured planning mode, minus hard-block categories (those
    stay L0/L1-only — the LLM must never be the reason a task is
    blocked) and minus the default/catch-all key."""
    mode_map = dict(intake_classifier.get("default_planning_mode_by_category", {}))
    blocked = set(intake_classifier.get("hard_block_categories", []))
    return [
        c for c in mode_map
        if c not in blocked and c != _DEFAULT_MODE_KEY
    ]


def _has_cjk(text: str) -> bool:
    return any(
        ("一" <= ch <= "鿿") or ("㐀" <= ch <= "䶿")
        for ch in text or ""
    )


class SemanticIntakeModule:
    module_id = "101C"

    def __init__(
        self,
        *,
        config: dict,
        closed_categories: list[str],
        chat_llm=None,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.closed = list(closed_categories or [])
        self.prompt_path = Path(prompt_path) if prompt_path is not None else None
        self._prompt_cached: str | None = None
        if chat_llm is None:
            env_name = str(self.config.get("llm_env_var") or "SEMANTIC_INTAKE_LLM")
            backend = os.environ.get(env_name) or None
            from ..adapters.chat_llm import ChatLLM
            chat_llm = ChatLLM(backend=backend)
        self.chat_llm = chat_llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def classify(self, text: str) -> dict[str, Any]:
        """Returns a decision dict:
        ``{"decision": override|clarify|abstain, "category": str|None,
           "confidence": float, "rationale": str,
           "clarify_question": str, "reason": str}``
        Never raises; any failure degrades to abstain so the pipeline
        behaves exactly as it did before 101C existed."""
        if not self._enabled():
            return self._abstain("disabled")
        if not self.closed:
            # Purity: an emptied category config must collapse behavior.
            return self._abstain("empty_closed_set")
        if not (text or "").strip():
            return self._abstain("empty_text")

        max_chars = int(self.config.get("max_input_chars", 1000))
        max_tokens = int(self.config.get("max_tokens", 350))
        try:
            payload = self.chat_llm.chat_json(
                self._system_prompt(),
                (text or "")[:max_chars],
                max_tokens=max_tokens,
            )
        except Exception:
            payload = {}
        if not isinstance(payload, dict) or not payload:
            return self._abstain("llm_unavailable_or_unparseable")

        category = str(payload.get("category") or "").strip()
        rationale = str(payload.get("rationale") or "")[:300]
        clarify_q = str(payload.get("clarify_question") or "").strip()
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        thresholds = self.config.get("confidence_thresholds") or {}
        accept_at = float(thresholds.get("accept", 0.7))
        clarify_at = float(thresholds.get("clarify", 0.4))

        if category == _NONE_LABEL:
            if clarify_q and bool(self.config.get("allow_clarify_on_none", True)) \
                    and confidence >= clarify_at:
                return self._clarify(clarify_q, text, confidence, rationale,
                                     reason="none_with_question")
            return self._abstain("no_category_fits", confidence=confidence,
                                 rationale=rationale)

        if category not in self.closed:
            # Invented / excluded / hard-block label → never act on it.
            return self._abstain("label_outside_closed_set",
                                 confidence=confidence, rationale=rationale)

        if confidence >= accept_at:
            return {
                "decision": _DECISION_OVERRIDE,
                "category": category,
                "confidence": confidence,
                "rationale": rationale,
                "clarify_question": "",
                "reason": "confident",
            }
        if confidence >= clarify_at:
            return self._clarify(clarify_q, text, confidence, rationale,
                                 reason="low_confidence", category=category)
        return self._abstain("below_clarify_threshold",
                             confidence=confidence, rationale=rationale)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _enabled(self) -> bool:
        if os.environ.get(_ENV_KILL_SWITCH, "1").lower() in ("0", "false", "no", "off"):
            return False
        return bool(self.config.get("enabled", False))

    def _abstain(self, reason: str, *, confidence: float = 0.0,
                 rationale: str = "") -> dict[str, Any]:
        return {
            "decision": _DECISION_ABSTAIN,
            "category": None,
            "confidence": confidence,
            "rationale": rationale,
            "clarify_question": "",
            "reason": reason,
        }

    def _clarify(self, question: str, text: str, confidence: float,
                 rationale: str, *, reason: str,
                 category: str | None = None) -> dict[str, Any]:
        if not question:
            templates = self.config.get("clarify_templates") or {}
            question = str(
                (templates.get("cjk") if _has_cjk(text) else templates.get("default"))
                or templates.get("default") or ""
            ).strip()
        if not question:
            # No usable question anywhere → nothing helpful to say.
            return self._abstain("no_clarify_template",
                                 confidence=confidence, rationale=rationale)
        return {
            "decision": _DECISION_CLARIFY,
            "category": category,
            "confidence": confidence,
            "rationale": rationale,
            "clarify_question": question,
            "reason": reason,
        }

    def _system_prompt(self) -> str:
        if self._prompt_cached is None:
            template = ""
            if self.prompt_path is not None:
                try:
                    template = self.prompt_path.read_text(encoding="utf-8")
                except OSError:
                    template = ""
            if not template:
                # Minimal inline fallback — generic instructions only;
                # the category vocabulary still comes from config below.
                template = (
                    "Classify the user goal into AT MOST ONE category id "
                    "from the CLOSED LIST, or \"none\" if no listed "
                    "category fits. Output STRICT JSON only: "
                    "{\"category\": \"...\", \"confidence\": 0.0, "
                    "\"rationale\": \"...\", \"clarify_question\": \"...\"}. "
                    "Never invent category ids.\n\nCLOSED LIST\n\n{{CATEGORIES}}"
                )
            descriptions = self.config.get("category_descriptions") or {}
            lines = []
            for cat in self.closed:
                desc = str(descriptions.get(cat) or "").strip()
                lines.append(f"- {cat}: {desc}" if desc else f"- {cat}")
            self._prompt_cached = template.replace("{{CATEGORIES}}", "\n".join(lines))
        return self._prompt_cached
