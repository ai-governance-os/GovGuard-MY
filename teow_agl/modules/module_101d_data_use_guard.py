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


# Sensitive socioeconomic / social-status fields that must never drive
# differential treatment (income, wealth, AND social standing — a Dato'/Datuk
# title, PIBG/PTA committee position, or donation potential are status signals,
# not legitimate communication factors).
_SOCIO_FIELDS = (
    "guardian income", "household income", "family income", "parent income",
    "parents income", "income", "salary", "wage", "socioeconomic",
    "socio economic", "family background", "household background",
    "guardian occupation", "parent occupation", "occupation",
    # social status / standing (Malaysian public-school context)
    "dato", "datuk", "datin", "social status", "social title", "social standing",
    "pibg", "家协", "committee member", "committee position", "committee role",
    "donation potential", "donation", "donor", "donate",
    "家庭收入", "家长收入", "父母收入", "家长薪水", "薪水", "收入",
    "家庭背景", "家境", "收入水平", "父母职业", "家长职业", "社会经济",
    "拿督", "拿汀", "社会地位", "家协地位", "捐款", "捐献", "捐款潜力",
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
    # differential favour / softening honesty by status (the PIBG/Dato' test)
    "more strongly", "stronger praise", "praise one", "more attention",
    "special attention", "favour", "favor", "preferential", "soften the",
    "go easier", "less honest",
    # CJK kept narrow — only phrases that clearly mean differential treatment
    # or contact-ordering, not generic task-priority words like 优先/排序/针对.
    "区别对待", "差别对待", "按收入", "依收入", "按家庭", "个性化对待",
    "先打电话", "先联系", "优先联系", "优先打给", "特别关照", "偏袒", "优待",
)
_PUBLIC_SCOPES = ("public", "public_release", "external")

# Route B §7.1 — a pupil's learning difficulty / support need is student-
# sensitive: legitimate INTERNALLY (report) and in a PRIVATE parent notice,
# but it must never be named or disclosed in PUBLIC content (a Facebook post).
_STUDENT_SENSITIVE = (
    "memorisation difficulty", "memorization difficulty",
    "could not finish memoris", "could not finish memoriz",
    "cannot memoris", "cannot memoriz", "unable to memoris", "unable to memoriz",
    "did not finish memoris", "did not finish memoriz",
    "struggling pupil", "struggling student", "struggling speaker",
    "student weakness", "pupil weakness", "weak speaker", "weak student",
    "named struggling student", "memorisation problem", "memorization problem",
    "student support observation", "student-support observation",
    "背不出", "记不住讲稿", "背诵困难", "学生弱点",
)

_RED_REASON_SOCIO = (
    "Sensitive socioeconomic data cannot be used for differential treatment "
    "in parent communication."
)
_SAFE_ALT_SOCIO = (
    "Use the pupil's actual competition performance, training attendance, coach "
    "observations, and the parent's recorded communication style instead — not "
    "social title, committee role, household income or donation potential."
)
_RED_REASON_STUDENT_PUBLIC = (
    "A pupil's learning difficulty or support plan is student-sensitive and "
    "cannot be named or disclosed in public content."
)
_SAFE_ALT_STUDENT_PUBLIC = (
    "Celebrate the winners and congratulate all participants; keep any named "
    "pupil's difficulty and support plan to the internal report and the private "
    "parent notice."
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

    # ----------------------------------------- C-tier understanding (gated LLM)
    def lexicon_signals(self, text: str) -> dict:
        """Deterministic A-tier read of a free-text goal — used to GATE the
        C-tier LLM call: if the lexicon already resolves the data-use intent,
        no LLM call is spent."""
        t = _normalize(text)
        return {
            "socio": any(f in t for f in _SOCIO_FIELDS),
            "diff": any(c in t for c in _DIFFERENTIAL_CUES),
            "pii": any(f in t for f in _PUBLIC_PII),
            "health": any(f in t for f in _HEALTH_FIELDS),
        }

    @staticmethod
    def understand(llm, text: str) -> list[str]:
        """C-tier understanding: ask an LLM to LABEL a free-text request with
        closed-vocabulary data-use concepts. The LLM never decides the route —
        101D's deterministic rules map the concepts to BLUE/GREEN/RED. Returns
        [] on no key / mock backend / any failure, so the A-tier lexicon and the
        fail-safe default still apply offline."""
        if llm is None or getattr(llm, "backend", "mock") == "mock":
            return []
        system = (
            "You are a data-use LABELLER for a school governance system. You do "
            "NOT decide whether anything is allowed — you only label WHAT data a "
            "request intends to use and HOW. Judge by meaning, not exact words. "
            "Output a JSON object only."
        )
        user = (
            f"Request: {text}\n\n"
            "Return JSON with boolean fields:\n"
            '{"socioeconomic_data": uses guardian/household/family income, '
            "salary, occupation, wealth, or family background; "
            '"differential_treatment": treats, prioritises, ranks, or '
            "personalises people differently based on who they are; "
            '"public_pii": exposes IC/MyKid/passport/phone/home address to a '
            "public or external surface; "
            '"health_or_discipline": uses health, discipline, or special-needs '
            "records}.\nIf unsure, use false."
        )
        try:
            data = llm.chat_json(system, user, max_tokens=200)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        out = []
        for key in ("socioeconomic_data", "differential_treatment",
                    "public_pii", "health_or_discipline"):
            if data.get(key) is True:
                out.append(key)
        return out

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
        # The student-sensitive-in-public check keys on the STEP's OWN data use
        # (its purpose, declared data-use, and allowed fields) — NOT the shared
        # user_intent. For Route B the user's goal legitimately narrates the
        # pupils' difficulty, which would otherwise contaminate every step and
        # wrongly RED the safe public post. The socio/pii/health signals keep
        # using the full text (they rely on user_intent for NL probes).
        own_text = _normalize(" ".join((
            str(getattr(action, "purpose", "")),
            str(getattr(action, "expected_effect", "")),
            str(md.get("data_use_purpose", "")),
            _norm_list(md.get("data_categories")),
            _norm_list(md.get("allowed_data")),
        )))
        scope = str(md.get("output_scope", "")).lower()
        approval_boundary = str(md.get("approval_boundary", "")).lower()
        # C-tier concepts: closed-vocabulary tags the LLM understanding layer
        # (101D.understand) may have attached for this task. They AUGMENT the
        # deterministic A-tier signals — the route is still decided by the rules
        # below, never by the LLM.
        concepts = md.get("data_use_concepts") or []

        # ── §F inert default: nothing to govern → change nothing ──
        if (not self._has_metadata(action) and not concepts
                and not self._obvious_sensitive_use(text, op)):
            return {"decision": "NO_OVERRIDE", "reasons": [],
                    "features": {"inert_default": True}}

        has_socio = any(f in text for f in _SOCIO_FIELDS) or "socioeconomic_data" in concepts
        has_diff = any(c in text for c in _DIFFERENTIAL_CUES) or "differential_treatment" in concepts
        has_pii = any(f in text for f in _PUBLIC_PII) or "public_pii" in concepts
        has_health = any(f in text for f in _HEALTH_FIELDS) or "health_or_discipline" in concepts
        has_student_sensitive = (
            any(f in own_text for f in _STUDENT_SENSITIVE)
            or "student_sensitive_public" in concepts
        )
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
            "student_sensitive": has_student_sensitive,
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

        # ── RED 4 (Route B §7.1): a pupil's learning difficulty / support need
        # exposed in PUBLIC content. Fine internally + in a private parent
        # notice; never named or disclosed publicly. `public_draft` counts —
        # the exposure is going INTO a public post — while a public-draft that
        # carries only safe achievement fields never trips this. ──
        if has_student_sensitive and (is_public or scope == "public_draft"):
            return {"decision": "RED",
                    "reasons": [_RED_REASON_STUDENT_PUBLIC,
                                f"safe_alternative: {_SAFE_ALT_STUDENT_PUBLIC}"],
                    "features": features}

        # ── GREEN: writing/updating an OFFICIAL record is high-impact → verify ─
        # Stronger than ordinary external release: this is the agent reaching a
        # formal administrative write inside its own autonomous workflow and
        # pausing for a human, not a generic publish gate.
        if (scope in ("official_record", "official_write")
                or "official" in approval_boundary):
            return {"decision": "GREEN",
                    "reasons": ["Writing or updating an official school "
                                "achievement record is a high-impact "
                                "administrative action — an educator must verify "
                                "the official result before it is written.",
                                "safe_alternative: Prepare the proposed update; a "
                                "human verifies the official result sheet and "
                                "approves before the record is written."],
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
