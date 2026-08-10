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
        hard_block_categories = set(
            self.classifier.get("hard_block_categories", [])
        )
        intent_hard_rule = self._match_intent_hard_block_rule(text)
        intent_required_categories = set(
            self.classifier.get("intent_required_hard_block_categories", [])
        )
        if intent_hard_rule is not None:
            category = str(intent_hard_rule.get("category") or category)
        elif category in intent_required_categories:
            # A bare reference to credentials may be an ordinary explanation
            # request.  Only the configured object x access/disclosure intent
            # contract turns it into a hard stop.
            category = _UNKNOWN_CATEGORY
        lexical_category = category
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
        if intent_hard_rule is not None:
            reasons.append(
                f"intent_hard_block_rule:{intent_hard_rule.get('id')}"
            )
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

    def _match_intent_hard_block_rule(self, text: str) -> dict | None:
        """Match config-declared hard floors that require object x intent.

        Unlike contextual school data-use rules, these protect secrets before
        any semantic/category override and therefore are never deferred.  The
        vocabulary remains entirely in configuration.
        """
        low = (text or "").lower()
        for rule in self.classifier.get("hard_block_intent_rules", []) or []:
            local_contract = rule.get("local_intent_contract")
            if isinstance(local_contract, dict):
                if self._matches_unnegated_local_intent(low, local_contract):
                    return rule
                # A local contract replaces the old whole-sentence bag of
                # words.  Falling through to require_all would reintroduce
                # the exact negation and educational false positives this
                # contract exists to prevent.
                continue
            groups = rule.get("require_all") or []
            if not groups:
                continue
            exclusions = rule.get("exclude_any") or []
            if any(str(p).lower() in low for p in exclusions if str(p).strip()):
                continue
            if all(any(str(p).lower() in low for p in group) for group in groups):
                return rule
        return None

    @staticmethod
    def _configured_term_spans(
        text: str,
        terms: list[Any],
    ) -> list[tuple[int, int]]:
        """Return config-term spans with safe ASCII token boundaries."""
        spans: set[tuple[int, int]] = set()
        for raw in terms or []:
            term = str(raw or "").strip().lower()
            if not term:
                continue
            pattern = re.escape(term)
            if term[0].isascii() and (term[0].isalnum() or term[0] == "_"):
                pattern = r"(?<![A-Za-z0-9_])" + pattern
            if term[-1].isascii() and (term[-1].isalnum() or term[-1] == "_"):
                pattern += r"(?![A-Za-z0-9_])"
            spans.update((match.start(), match.end()) for match in re.finditer(
                pattern, text,
            ))
        return sorted(spans)

    @staticmethod
    def _configured_bridge_allowed(
        bridge: str,
        allowed_terms: list[Any],
        *,
        max_chars: int,
    ) -> bool:
        """Accept only grammar particles configured for an object binding."""
        if len(bridge) > max_chars:
            return False
        tokens = re.findall(
            r"[A-Za-z0-9_]+|[\u3400-\u9fff]+",
            str(bridge or "").casefold(),
        )
        allowed_tokens = {
            token
            for term in allowed_terms or []
            for token in re.findall(
                r"[A-Za-z0-9_]+|[\u3400-\u9fff]+",
                str(term or "").casefold(),
            )
        }
        return all(token in allowed_tokens for token in tokens)

    @classmethod
    def _action_is_locally_negated(
        cls,
        text: str,
        action_start: int,
        contract: dict,
    ) -> bool:
        """Check negation in the action's local clause, not the whole goal."""
        window_size = int(contract.get("negation_window_chars") or 96)
        prefix = text[max(0, action_start - window_size):action_start]
        # Sentence punctuation ends a negation scope. A dot only counts when
        # it ends a sentence, so the dot in `.env` cannot split the clause.
        boundary_matches = list(re.finditer(r"\.(?=\s|$)|[!?;:\n]", prefix))
        if boundary_matches:
            prefix = prefix[boundary_matches[-1].end():]
        breakers = cls._configured_term_spans(
            prefix, list(contract.get("scope_break_terms") or []),
        )
        if breakers:
            prefix = prefix[breakers[-1][1]:]
        return bool(cls._configured_term_spans(
            prefix, list(contract.get("negation_terms") or []),
        ))

    @classmethod
    def _credential_object_is_topic_only(
        cls,
        text: str,
        object_start: int,
        contract: dict,
    ) -> bool:
        """True when the credential words name a document topic, not data.

        For example, ``an explanation of API keys`` makes ``it`` refer to the
        explanation. The topic prefixes are configuration, not code lexicon.
        """
        window_size = int(contract.get("topic_prefix_window_chars") or 64)
        prefix = text[max(0, object_start - window_size):object_start].rstrip()
        for raw in contract.get("topic_container_prefix_terms") or []:
            term = str(raw or "").strip().casefold()
            if term and prefix.casefold().endswith(term):
                return True
        return False

    @classmethod
    def _matches_unnegated_local_intent(
        cls,
        text: str,
        contract: dict,
    ) -> bool:
        """Match a credential object bound to an unnegated risky action.

        The bridge is deliberately grammatical rather than proximity-only:
        ``copy its token`` binds directly, while ``copy this explanation of
        API keys`` does not. All vocabulary and bridge particles remain in
        configuration.
        """
        action_spans = cls._configured_term_spans(
            text, list(contract.get("action_terms") or []),
        )
        object_spans = cls._configured_term_spans(
            text, list(contract.get("credential_object_terms") or []),
        )
        reverse_action_spans = set(cls._configured_term_spans(
            text, list(contract.get("reverse_action_terms") or []),
        ))
        reference_spans = cls._configured_term_spans(
            text, list(contract.get("reference_pronoun_terms") or []),
        )
        max_chars = int(contract.get("max_bridge_chars") or 56)
        direct_terms = list(contract.get("direct_bridge_terms") or [])
        reverse_terms = list(contract.get("reverse_bridge_terms") or [])
        anaphora_terms = list(contract.get("anaphora_bridge_terms") or [])
        max_anaphora = int(contract.get("max_anaphora_chars") or 120)
        for action_start, action_end in action_spans:
            if cls._action_is_locally_negated(text, action_start, contract):
                continue
            for object_start, object_end in object_spans:
                if action_end <= object_start:
                    if cls._configured_bridge_allowed(
                        text[action_end:object_start],
                        direct_terms,
                        max_chars=max_chars,
                    ):
                        return True
                elif object_end <= action_start:
                    if cls._credential_object_is_topic_only(
                        text, object_start, contract,
                    ):
                        continue
                    # Reverse word order is authoritative only for configured
                    # passive/post-positive verbs. A base verb such as
                    # ``upload`` needs an immediate reference pronoun so
                    # document topics do not become credential objects.
                    if (
                        (action_start, action_end) in reverse_action_spans
                        and cls._configured_bridge_allowed(
                            text[object_end:action_start],
                            reverse_terms,
                            max_chars=max_chars,
                        )
                    ):
                        return True
                    if (
                        action_start - object_end <= max_anaphora
                        and cls._configured_bridge_allowed(
                            text[object_end:action_start],
                            anaphora_terms,
                            max_chars=max_anaphora,
                        )
                        and any(
                            action_end <= ref_start
                            and cls._configured_bridge_allowed(
                                text[action_end:ref_start],
                                direct_terms,
                                max_chars=max_chars,
                            )
                            for ref_start, _ref_end in reference_spans
                        )
                    ):
                        return True
        return False

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
