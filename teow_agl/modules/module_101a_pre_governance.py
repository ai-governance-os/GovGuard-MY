"""
Module 101A — Pre-Governance / Pre-Intuition.
Decisions are entirely data-driven from intake_classifier.json,
universal_hard_safety.json, and learned_contextual_policy.json.
This module contains NO hardcoded English vocabulary, file path
literals, or route literals.
"""
from __future__ import annotations

import re
from typing import Any

from ..models import PreGovernanceAssessment, TaskEnvelope
from ..policies.governance_profile import ProfileView
from ..policies import hard_safety as hs


_UNKNOWN_CATEGORY = "unknown"
_DEFAULT_MODE_KEY = "unknown"
_BLOCKED_PLANNING_MODE = "blocked"


class PreGovernanceModule:
    module_id = "101A"

    def __init__(
        self,
        *,
        intake_classifier: dict,
        hard_safety_cfg: dict,
        learned_policy: dict,
    ) -> None:
        self.classifier = intake_classifier
        self.hard_safety_cfg = hard_safety_cfg
        self.learned_policy = learned_policy

    def assess(
        self,
        envelope: TaskEnvelope,
        profile: ProfileView,
        *,
        category_override: str | None = None,
        override_reason: str | None = None,
        defer_contextual_data_use: bool = False,
    ) -> PreGovernanceAssessment:
        text = envelope.normalized_goal
        category = self._classify(text)
        lexical_category = category
        hard_block_categories = set(
            self.classifier.get("hard_block_categories", [])
        )
        override_rejected = False
        # Semantic override (101C). Only labels the data layer already
        # knows are accepted — the override changes WHICH configured
        # category applies, never what the configuration says about it.
        # Hard-block checks below still run on the overridden category,
        # so an override can tighten routing but the config decides.
        if category_override:
            known = set(self.classifier.get("default_planning_mode_by_category", {})) \
                | set(self.classifier.get("category_keywords", {}))
            if category_override in known:
                # A broad school-output override must never erase a lexical
                # credential/destructive hard category. Semantic context may
                # tighten or organise safe work; it cannot weaken an existing
                # deterministic hard stop.
                if lexical_category in hard_block_categories:
                    override_rejected = True
                else:
                    category = category_override
        red_pat = hs.detect_red_pattern(text, self.hard_safety_cfg)

        hard_block = False
        hard_block_code: str | None = None
        reasons: list[str] = []
        if category_override and category == category_override:
            reasons.append(f"category_override:{override_reason or 'semantic_intake'}")
        if override_rejected:
            reasons.append(
                f"category_override_rejected:hard_block:{lexical_category}"
            )

        if red_pat is not None:
            hard_block = True
            hard_block_code = self.classifier.get("default_pattern_hard_block_code") or red_pat
            reasons.append(f"hard_red_pattern_match:{red_pat}")

        # P2.1 — deterministic concept-gates for public-service risks. A rule
        # fires when the goal contains ≥1 phrase from EACH of its concept
        # groups (sensitive entity × risky intent), so rewording does not slip
        # past — it matches CONCEPTS, not exact sentences. Fully offline; the
        # LLM is never consulted for these hard-safety decisions.
        if not hard_block and not defer_contextual_data_use:
            risk_rule = self._match_risk_rule(text)
            if risk_rule is not None:
                category = risk_rule.get("category", category)
                hard_block = True
                if risk_rule.get("block") == "infeasible":
                    hard_block_code = "infeasible_" + risk_rule.get("category", "request")
                else:
                    hard_block_code = risk_rule.get("category", "policy_block")
                reasons.append(f"risk_rule:{risk_rule.get('id')}")
                # Carry a config-driven safe alternative so the blocked answer
                # can show the user what to do instead (not just "blocked").
                alt = (risk_rule.get("safe_alternative") or "").strip()
                if alt:
                    reasons.append(f"safe_alternative: {alt}")

        for blocking_category in hard_block_categories:
            if category == blocking_category:
                hard_block = True
                hard_block_code = blocking_category
                reasons.append(f"category_hard_block:{blocking_category}")
                break

        # Infeasibility patterns (capability/resource limits) — distinct from
        # hard-RED safety violations. Pre-flags hard_block_code with an
        # "infeasible_" prefix so 103 routes INFEASIBLE not RED.
        if not hard_block and not defer_contextual_data_use:
            infeasibility_patterns = self.classifier.get("infeasibility_patterns", [])
            lowered_for_inf = text.lower()
            for pat in infeasibility_patterns:
                p = pat.lower()
                if p and p in lowered_for_inf:
                    hard_block = True
                    hard_block_code = self.classifier.get(
                        "infeasibility_default_code", "infeasible_resource"
                    )
                    reasons.append(f"infeasibility_pattern_match:{pat}")
                    break

        context_sensitive = category in set(self.classifier.get("context_sensitive_categories", []))

        if hard_block:
            planning_mode = _BLOCKED_PLANNING_MODE
        else:
            hint = self.learned_policy.get("planner_mode_hints", {}).get(category)
            default_map = self.classifier.get("default_planning_mode_by_category", {})
            planning_mode = hint or default_map.get(category) or default_map.get(_DEFAULT_MODE_KEY) or "explain_only"

        context_features = self._context_features(envelope, profile, category)

        planning_brief = {
            "task_id": envelope.task_id,
            "user_intent": envelope.normalized_goal,
            "task_category": category,
            "planning_mode": planning_mode,
            "context_features": context_features,
            "workspace_roots": envelope.workspace_roots or profile.workspace_roots,
            "must_not_directly_execute": True,
            "must_emit_json_only": True,
        }

        return PreGovernanceAssessment(
            task_id=envelope.task_id,
            task_category=category,
            planning_mode=planning_mode,  # type: ignore[arg-type]
            hard_block=hard_block,
            hard_block_code=hard_block_code,
            context_sensitive=context_sensitive,
            reasons=reasons or [f"category:{category}"],
            context_features=context_features,
            planning_brief=planning_brief,
        )

    def _classify(self, text: str) -> str:
        if not text:
            return _UNKNOWN_CATEGORY
        lowered = text.lower()
        kw_map: dict[str, list[str]] = self.classifier.get("category_keywords", {})
        best_category = _UNKNOWN_CATEGORY
        best_pos = len(lowered) + 1
        for cat, kws in kw_map.items():
            for kw in kws:
                k = kw.lower()
                if not k:
                    continue
                # ASCII command fragments must begin/end at a token boundary.
                # A plain substring search made `rm ` match the tail of
                # `inform ` and misclassified a parent notice as file deletion.
                pattern = re.escape(k)
                if k[0].isascii() and (k[0].isalnum() or k[0] == "_"):
                    pattern = r"(?<![A-Za-z0-9_])" + pattern
                if k[-1].isascii() and (k[-1].isalnum() or k[-1] == "_"):
                    pattern = pattern + r"(?![A-Za-z0-9_])"
                match = re.search(pattern, lowered)
                pos = match.start() if match else -1
                if pos != -1 and pos < best_pos:
                    best_pos = pos
                    best_category = cat
                    break
        return best_category

    def _match_risk_rule(self, text: str) -> dict | None:
        """Return the first `risk_rules` entry whose every concept group has
        at least one phrase present in `text` (concept × intent). Deterministic,
        offline, case-insensitive. Config-driven — no vocabulary in code."""
        low = (text or "").lower()
        for rule in self.classifier.get("risk_rules", []) or []:
            groups = rule.get("require_all") or []
            if not groups:
                continue
            exclusions = rule.get("exclude_any") or []
            if any(str(p).lower() in low for p in exclusions if str(p).strip()):
                continue
            if all(any(str(p).lower() in low for p in grp) for grp in groups):
                return rule
        return None

    def _context_features(self, envelope: TaskEnvelope, profile: ProfileView, category: str) -> dict[str, Any]:
        text = envelope.normalized_goal
        low = (text or "").lower()
        terms = self.classifier.get("sensitive_mention_terms", []) or []
        sensitive_mention = any(str(t).lower() in low for t in terms)
        return {
            "task_category": category,
            "raw_text_length": len(text),
            "role_context": profile.role_context,
            "workspace_root_count": len(envelope.workspace_roots or profile.workspace_roots),
            "has_attachments": bool(envelope.attachments),
            # P2.2 fail-safe signal: the goal mentions sensitive student/guardian
            # personal data. 103 uses this to refuse silent auto-execution
            # (BLUE -> GREEN) for anything the concept gates didn't already
            # block — fail toward asking a human, never toward silent action.
            "sensitive_data_mention": sensitive_mention,
        }
