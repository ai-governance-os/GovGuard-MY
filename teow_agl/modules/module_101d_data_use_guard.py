"""Module 101D — Data Use Guard (the agent's self-governance over data use).

Governs what the agent ITSELF intends to do with data inside its own plan —
a layer ON TOP of the existing concept gates (101A) and action-risk (101B),
never a replacement. Deterministic, offline, keyword/metadata matching; the
LLM is never the safety authority.

**Inert by default (§F).** For any action with no workflow/data-use metadata
and no obvious sensitive data use, ``assess`` returns ``NO_OVERRIDE`` and
changes nothing — the legacy hot path is untouched (no BLUE→GREEN, no block,
no risk-score change, no trace noise).

Decisions:
  RED         — the agent's own plan would (a) use guardian income / family
                background / socioeconomic data for DIFFERENTIAL treatment in
                parent communication, (b) publish IC/MyKid/passport/phone/
                home address to a public/external surface, or (c) put health /
                discipline / special-needs detail into public content.
  GREEN       — an external publish/send/submit, or an unclear sensitive use:
                require a human to approve (fail toward asking a human).
  NO_OVERRIDE — internal report / public DRAFT with only safe fields, or any
                action the guard has no opinion on: leave normal governance.

When both 101D and the existing gates apply, the STRICTER route wins (§G):
existing RED stays RED, existing GREEN never downgrades, 101D RED → RED,
101D GREEN while risk says BLUE → elevate to GREEN.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Matching runs on NORMALIZED text (lower-cased, underscores/hyphens → spaces,
# whitespace collapsed) so that the metadata form ("guardian_income") and the
# natural-language form ("guardian household income", "家长收入") match the same
# lexicon entries. This is the "A-tier" deterministic understanding layer: it
# never decides safety, it only maps phrasings to concepts for the governance
# core. Terms below are therefore written in space form.
_WS = re.compile(r"\s+")


def _normalize(text: Any) -> str:
    t = str(text or "").lower().replace("_", " ").replace("-", " ")
    return _WS.sub(" ", t).strip()


# Sensitive socioeconomic fields that must never drive differential treatment.
_SOCIO_FIELDS = (
    "guardian income", "household income", "family income", "parent income",
    "parents income", "income", "salary", "wage", "socioeconomic",
    "socio economic", "family background", "household background",
    "guardian occupation", "parent occupation", "occupation",
    "家庭收入", "家长收入", "父母收入", "家长薪水", "薪水", "收入",
    "家庭背景", "家境", "收入水平", "父母职业", "家长职业", "社会经济",
)
# Personally-identifying numbers / contacts that must never be published.
_PUBLIC_PII = (
    "ic number", "mykid number", "mykid", "passport number",
    "phone number", "home address", "contact number",
    "身份证", "证件号码", "电话号码", "联络号码", "家庭住址", "住家地址",
)
# Categories that must not appear in public content by default.
_HEALTH_FIELDS = (
    "health data", "health record", "medical record", "discipline record",
    "disciplinary record", "special needs", "special needs detail",
    "健康记录", "病历", "纪律记录", "特殊需要", "残障",
)
# Cues that the agent intends to treat people DIFFERENTLY using the data.
_DIFFERENTIAL_CUES = (
    "differential", "differentiate", "personalize", "personalise",
    "personalised", "personalized", "tailor", "treat differently",
    "prioritise", "prioritize", "segment", "by income", "based on income",
    "according to income", "based on family", "rank parents", "called first",
    "who gets called", "contact first", "call order", "contact order",
    "single out", "target parents",
    # CJK kept narrow — only phrases that clearly mean differential treatment
    # or contact-ordering, not generic task-priority words like 优先/排序/针对.
    "区别对待", "差别对待", "按收入", "依收入", "按家庭", "个性化对待",
    "先打电话", "先联系", "优先联系", "优先打给",
)
_PUBLIC_SCOPES = ("public", "public_release", "external")

_RED_REASON_SOCIO = (
    "Sensitive socioeconomic data cannot be used for differential treatment "
    "in parent communication."
)
_SAFE_ALT_SOCIO = (
    "Use student progress, attendance, homework completion, or neutral "
    "communication preferences instead."
)


def _norm_list(values: Any) -> str:
    if isinstance(values, (list, tuple, set)):
        return " ".join(str(v) for v in values).lower()
    return str(values or "").lower()


class DataUseGuard:
    """Deterministic guard over the agent's own intended data use."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir else None

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _has_metadata(action) -> bool:
        md = getattr(action, "metadata", {}) or {}
        return bool(
            md.get("workflow_id") or md.get("data_categories")
            or md.get("output_scope") or md.get("approval_boundary")
        )

    @staticmethod
    def _obvious_sensitive_use(text: str, op: str) -> bool:
        # Narrow on purpose — only the clearest self-initiated dangers fire on
        # a LEGACY (non-metadata) action, so the inert default never perturbs
        # existing behaviour. Workflow / data-use-tagged actions are assessed
        # in full regardless (they pass `_has_metadata`).
        has_socio = any(f in text for f in _SOCIO_FIELDS)
        has_diff = any(c in text for c in _DIFFERENTIAL_CUES)
        has_pii = any(f in text for f in _PUBLIC_PII)
        publishing = "publish" in op or "send" in op or "submit" in op
        return (has_socio and has_diff) or (has_pii and publishing)

    # ----------------------------------------------------------------- assess
    def assess(self, action) -> dict:
        md = getattr(action, "metadata", {}) or {}
        op = _normalize(getattr(action, "operation", ""))
        # Inspect the agent's intended data use: the action itself PLUS the
        # task intent threaded into metadata (so a planner that produced a
        # generic action still surfaces the goal's data-use intent to 101D).
        text = _normalize(" ".join((
            str(getattr(action, "purpose", "")),
            str(getattr(action, "expected_effect", "")),
            str(getattr(action, "target", "")),
            str(md.get("data_use_purpose", "")),
            str(md.get("user_intent", "")),
            _norm_list(md.get("data_categories")),
            _norm_list(md.get("allowed_data")),
        )))
        scope = str(md.get("output_scope", "")).lower()
        approval_boundary = str(md.get("approval_boundary", "")).lower()

        # ── §F inert default: nothing to govern → change nothing ──
        if not self._has_metadata(action) and not self._obvious_sensitive_use(text, op):
            return {"decision": "NO_OVERRIDE", "reasons": [],
                    "features": {"inert_default": True}}

        has_socio = any(f in text for f in _SOCIO_FIELDS)
        has_diff = any(c in text for c in _DIFFERENTIAL_CUES)
        has_pii = any(f in text for f in _PUBLIC_PII)
        has_health = any(f in text for f in _HEALTH_FIELDS)
        is_public = (
            scope in _PUBLIC_SCOPES
            or "publish" in op or "send" in op or "submit" in op
            or approval_boundary.startswith("human_required")
        )

        features = {
            "output_scope": scope or None,
            "approval_boundary": approval_boundary or None,
            "is_public_or_external": is_public,
            "socioeconomic_data": has_socio,
            "differential_intent": has_diff,
            "public_pii": has_pii,
            "health_discipline_special": has_health,
            "workflow_id": md.get("workflow_id"),
        }

        # ── RED 1 (flagship §I): socioeconomic data → differential treatment ─
        if has_socio and has_diff:
            return {"decision": "RED",
                    "reasons": [_RED_REASON_SOCIO,
                                f"safe_alternative: {_SAFE_ALT_SOCIO}"],
                    "features": features}

        # ── RED 2: publish personal identifiers to a public/external surface ─
        if has_pii and is_public:
            return {"decision": "RED",
                    "reasons": ["Personal identifiers (IC/MyKid/passport/phone/"
                                "home address) cannot be published to a public "
                                "or external surface.",
                                "safe_alternative: Remove all personal "
                                "identifiers before any public release."],
                    "features": features}

        # ── RED 3: health / discipline / special-needs in public content ──
        if has_health and is_public:
            return {"decision": "RED",
                    "reasons": ["Health, discipline, or special-needs details "
                                "cannot be placed in public content.",
                                "safe_alternative: Keep these records internal "
                                "and access-controlled."],
                    "features": features}

        # ── GREEN: external publish/send/submit needs human approval ──
        if is_public and scope != "public_draft":
            return {"decision": "GREEN",
                    "reasons": ["External release requires human approval "
                                "before any outside action."],
                    "features": features}

        # ── GREEN: a sensitive mention we can't fully classify → ask a human ─
        if (has_socio or has_pii or has_health) and scope != "internal":
            return {"decision": "GREEN",
                    "reasons": ["Unclear sensitive data use — human approval "
                                "required (fail toward asking a human)."],
                    "features": features}

        # ── NO_OVERRIDE: internal report / public DRAFT with safe fields ──
        return {"decision": "NO_OVERRIDE", "reasons": [], "features": features}
