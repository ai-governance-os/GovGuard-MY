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
# Bahasa Malaysia terms belong in this last deterministic boundary rather
# than depending on an upstream model to emit a `public_pii` concept.
_PUBLIC_PII = (
    "ic number", "mykid number", "mykid", "passport number",
    "phone number", "home address", "contact number",
    "nombor telefon", "no telefon", "alamat rumah", "nombor ic",
    "kad pengenalan", "nombor kad pengenalan", "nombor pasport",
    "身份证", "证件号码", "电话号码", "联络号码", "家庭住址", "住家地址",
)
# A real contact value, as opposed to the name of a contact field. Matching
# runs on normalized text, so separators may already have become spaces.
_CONTACT_VALUE = re.compile(
    r"(?<!\w)(?:\+?60\s*|0)(?:1\d|[3-9])(?:[- ]?\d){7,8}(?!\w)"
    r"|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
)
# Categories that must not appear in public content by default.
_HEALTH_FIELDS = (
    "health data", "health record", "medical record", "discipline record",
    "disciplinary record", "special needs", "special needs detail",
    "injured", "injury", "bitten", "bite wound", "hospitalised", "hospitalized",
    "admitted to hospital", "diagnosis", "medical condition", "unconscious",
    "bleeding", "poisoned", "food poisoning", "difficulty breathing",
    "student misconduct", "student was disciplined", "disciplinary action",
    "受伤", "伤势", "咬伤", "送院", "住院", "诊断", "中毒", "呼吸困难", "纪律处分",
    "cedera", "digigit", "dimasukkan ke hospital", "diagnosis", "keracunan",
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


# Product-wide alternatives. These intentionally replace the earlier Route-B
# example copy so an arbitrary discipline, health or learning case never leaks
# athletics-specific wording into its governance explanation.
_SAFE_ALT_SOCIO = (
    "Use relevant observed conduct or performance, verified evidence, applicable "
    "school rules and proportionate support instead; exclude social title, family "
    "background, household income and unrelated social or financial status."
)
_SAFE_ALT_STUDENT_PUBLIC = (
    "Use anonymous or aggregate school-community wording; keep person-level "
    "difficulty, marks, conduct and support details inside authorised internal "
    "records or a separate one-to-one guardian communication."
)


def _norm_list(values: Any) -> str:
    if isinstance(values, (list, tuple, set)):
        return " ".join(str(v) for v in values).lower()
    return str(values or "").lower()


def _semantic_target(value: Any) -> str:
    """Keep a logical target, but never scan an absolute host path as data.

    Generated artifacts are rewritten to an absolute path below ``outputs``.
    Date-stamped extraction folders can accidentally resemble phone numbers,
    even though that host path is not part of the proposed document or data
    use. Preserve only the filename for absolute Windows/UNC targets; relative
    logical targets remain available to the deterministic guard.
    """
    target = str(value or "").strip()
    if re.match(r"(?i)^[a-z]:[\\/]", target) or target.startswith("\\\\"):
        return re.split(r"[\\/]", target)[-1]
    return target


def _contains_asserted_sensitive_term(text: str, terms: tuple[str, ...]) -> bool:
    """True only when a sensitive term is asserted, not explicitly excluded.

    Privacy-safe replacement drafts often state their boundary in plain text,
    for example ``No pupil diagnosis is shared`` or ``exclude health data``.
    Treating those negative controls as a disclosure makes the action guard
    block the very anonymous alternative produced by the input-governance
    layer.  This remains conservative: only a bounded, same-clause exclusion
    suppresses the match; any positive or ambiguous mention still fires.
    """
    value = _normalize(text)
    for clause in re.split(r"[.!?;\n]+", value):
        for term in terms:
            start = clause.find(term)
            while start >= 0:
                before = clause[max(0, start - 140):start]
                after = clause[start + len(term):start + len(term) + 90]
                excluded_before = bool(re.search(
                    r"\b(?:no|without|exclude(?:s|d)?|excluding|omit(?:s|ted)?|"
                    r"omitting|remove(?:s|d)?|removing|withhold(?:s|held)?|"
                    r"do\s+not\s+(?:include|share|disclose)|must\s+not\s+"
                    r"(?:include|share|disclose)|not\s+(?:include|share|disclose))\b"
                    r"[^.!?;\n]{0,120}$",
                    before,
                ))
                excluded_after = bool(re.match(
                    r"[^.!?;\n]{0,60}\b(?:is|are|must\s+be)\s+"
                    r"(?:not\s+(?:included|shared|disclosed)|excluded|omitted|"
                    r"removed|withheld)\b",
                    after,
                ))
                if not excluded_before and not excluded_after:
                    return True
                start = clause.find(term, start + len(term))
    return False


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
            "student_sensitive": any(f in t for f in _STUDENT_SENSITIVE),
            "persistent": any(c in t for c in (
                "remember", "store for future", "long term", "long-term",
                "permanent profile", "future profile", "learn for future",
            )),
            "public": any(c in t for c in (
                "public", "facebook", "publish", "announcement", "post",
            )),
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
            "Return JSON with boolean fields. Label meaning, not exact words:\n"
            '{"socioeconomic_data": uses guardian/household/family income, '
            "salary, occupation, wealth, or family background; "
            '"differential_treatment": treats, prioritises, ranks, or '
            "personalises people differently based on who they are; "
            '"public_pii": exposes IC/MyKid/passport/phone/home address to a '
            "public or external surface; "
            '"health_or_discipline": uses health, discipline, or special-needs '
            "records; "
            '"student_sensitive_data": uses a named pupil\'s learning '
            "difficulty, weakness, support need, behaviour, or private progress; "
            '"public_disclosure": asks to put person-level information into a '
            "public post, announcement, website, social channel, or broad audience; "
            '"persistent_sensitive_learning": asks the system to remember, '
            "profile, train on, or reuse person-level sensitive information in future; "
            '"external_release": asks to send, publish, submit, message, or '
            "otherwise release something outside the internal draft workspace; "
            '"official_record_change": asks to write, update, or alter an '
            "official student, attendance, discipline, finance, or school record; "
            '"financial_value_change": asks to issue, reprint, change the price '
            "of, or authorise cash-equivalent coupons/tokens/payments; "
            '"unsupported_fact_invention": asks to guess, fabricate, or make up '
            "a fact that was not provided or verified. This MUST be false when "
            "the request says to mark unknown facts TBC/to-be-confirmed, leave "
            "them blank, clarify, investigate, or use only available evidence}."
            "\nIf unsure, use false."
        )
        try:
            data = llm.chat_json(system, user, max_tokens=200)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        out = []
        for key in (
            "socioeconomic_data", "differential_treatment", "public_pii",
            "health_or_discipline", "student_sensitive_data",
            "public_disclosure", "persistent_sensitive_learning",
            "external_release", "official_record_change",
            "financial_value_change", "unsupported_fact_invention",
        ):
            if data.get(key) is True:
                out.append(key)
        return out

    # ----------------------------------------------------------------- assess
    def assess(self, action) -> dict:
        md = getattr(action, "metadata", {}) or {}
        op = _normalize(getattr(action, "operation", ""))
        tool = _normalize(getattr(action, "tool", ""))
        # Inspect the agent's intended data use: the action itself PLUS the
        # task intent threaded into metadata (so a planner that produced a
        # generic action still surfaces the goal's data-use intent to 101D).
        contract_scoped = bool(
            md.get("coverage_source") == "school_response_pack"
            or md.get("action_data_contract") is True
        )
        shared_user_intent = "" if contract_scoped else str(md.get("user_intent", ""))
        text = _normalize(" ".join((
            str(getattr(action, "purpose", "")),
            str(getattr(action, "expected_effect", "")),
            _semantic_target(getattr(action, "target", "")),
            str(md.get("data_use_purpose", "")),
            shared_user_intent,
            _norm_list(md.get("data_categories")),
            _norm_list(md.get("allowed_data")),
            _norm_list(md.get("uses_fields")),
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
            str(md.get("content", "")),
            str(md.get("body", "")),
            _norm_list(md.get("data_categories")),
            _norm_list(md.get("allowed_data")),
            _norm_list(md.get("uses_fields")),
        )))
        # The DECLARED allowed fields alone (no goal prose) — used by the
        # "unclear sensitive → GREEN" fail-safe so it fires on what a step
        # actually USES, not a goal that merely NAMES a sensitive field to
        # forbid it (e.g. "do NOT infer wealth from occupation"), and not the
        # shared user_intent (a donor-outreach workflow's goal says "donor").
        allowed_text = _normalize(" ".join((
            _norm_list(md.get("data_categories")),
            _norm_list(md.get("allowed_data")),
        )))
        scope = str(md.get("output_scope", "")).lower()
        audience = str(
            md.get("audience") or md.get("semantic_audience") or ""
        ).lower()
        approval_boundary = str(md.get("approval_boundary", "")).lower()
        release_state = str(md.get("release_state", "")).lower()
        explicit_release_action = md.get("external_release_action") is True
        # C-tier concepts: closed-vocabulary tags the LLM understanding layer
        # (101D.understand) may have attached for this task. They AUGMENT the
        # deterministic A-tier signals — the route is still decided by the rules
        # below, never by the LLM.
        concepts = {
            str(c).strip().lower()
            for c in (md.get("data_use_concepts") or [])
            if str(c).strip()
        }

        # The contract companion is a short operator-only delivery note. It
        # contains no case body and performs no release, so task-level concepts
        # must never turn this UI cover into the governed action itself.
        if md.get("school_content_role") == "chat_companion":
            return {
                "decision": "NO_OVERRIDE",
                "reasons": [],
                "features": {
                    "output_scope": "internal",
                    "audience": "operator",
                    "is_public": False,
                    "is_external": False,
                    "is_private_recipient": False,
                    "is_public_or_external": False,
                    "contract_companion": True,
                },
            }

        # ── §F inert default: nothing to govern → change nothing ──
        if (not self._has_metadata(action) and not concepts
                and not self._obvious_sensitive_use(text, op)):
            return {"decision": "NO_OVERRIDE", "reasons": [],
                    "features": {"inert_default": True}}

        has_socio = any(f in text for f in _SOCIO_FIELDS) or "socioeconomic_data" in concepts
        has_diff = any(c in text for c in _DIFFERENTIAL_CUES) or "differential_treatment" in concepts
        pii_scan = f"{text} {own_text}"
        institutional_contact = bool(re.search(
            r"\b(?:school office|official school|school's official|main office|pejabat sekolah)\b",
            pii_scan,
        ))
        contact_value = bool(
            re.search(r"\b(?:\+?60|0)1\d[- ]?\d{3,4}[- ]?\d{4}\b", pii_scan)
            or re.search(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", pii_scan)
        )
        own_pii_terms = any(f in own_text for f in _PUBLIC_PII)
        own_pii_negated = bool(re.search(
            r"\b(?:no|without|exclude(?:s|d)?|do not include|does not include)\b"
            r"[^.!?\n]{0,100}\b(?:ic|mykid|passport|phone|address|contact)\b",
            own_text,
        ))
        # A public school draft may safely NAME its own contact field while the
        # value remains TBC. Keep this exemption sentence-scoped and narrow:
        # only phone-shaped institutional fields qualify; IC/MyKid/passport/
        # home-address terms, real values, and person-scoped fields never do.
        institutional_eligible = (
            "contact number", "phone number", "contact details",
            "nombor telefon", "no telefon",
        )
        sentences = [
            sentence
            for sentence in re.split(r"(?:[;!?\n]+|\.\s+|\.$)", own_text)
            if sentence.strip()
        ]

        def _person_scoped(sentence: str) -> bool:
            person_before_field = re.search(
                r"(?:\b(?!school|office|department|ministry|academy|college|"
                r"institution|organisation|organization|company|vendor|operator)"
                r"[a-z]+(?:'s|s')"
                r"|\bpupils?'|\bstudents?'|\bparents?'"
                r"|\bguardians?'|\bchildren'?s|\bhis\b|\bher\b|\btheir\b"
                r"|\beach\s+(?:pupil|student|parent|guardian|child)"
                r"|\bthe\s+(?:pupil|student|parent|guardian|child)\b"
                r"|\bibu\s+bapa\b|\bpenjaga\b|\bmurid\b|\bpelajar\b)"
                r"[^\n]{0,40}?"
                r"\b(?:ic|mykid|passport|phone|contact|home address|address|"
                r"telefon|alamat)\b",
                sentence,
            )
            field_before_person = re.search(
                r"\b(?:ic|mykid|passport|phone|contact|home address|address|"
                r"telefon|alamat)\b"
                r"[^\n]{0,40}?"
                r"\b(?:pupil|student|parent|guardian|child|penjaga|murid|"
                r"pelajar|ibu\s+bapa)\b",
                sentence,
            )
            return bool(person_before_field or field_before_person)

        def _has_value(sentence: str) -> bool:
            return bool(
                _CONTACT_VALUE.search(sentence)
                or re.search(r"\b\d{6}[- ]?\d{2}[- ]?\d{4}\b", sentence)
            )

        def _term_sentence_is_school_channel(sentence: str) -> bool:
            if any(
                field in sentence
                for field in _PUBLIC_PII
                if field not in institutional_eligible
            ):
                return False
            return not _has_value(sentence) and not _person_scoped(sentence)

        own_pii_institutional_only = (
            bool(own_pii_terms)
            and institutional_contact
            and all(
                _term_sentence_is_school_channel(sentence)
                for sentence in sentences
                if any(field in sentence for field in _PUBLIC_PII)
            )
        )
        contact_value_unshielded = any(
            _has_value(sentence) for sentence in sentences
        )
        has_pii = (
            any(f in text for f in _PUBLIC_PII)
            or (
                own_pii_terms
                and not own_pii_negated
                and not own_pii_institutional_only
            )
            or bool(re.search(r"\b\d{6}[- ]?\d{2}[- ]?\d{4}\b", pii_scan))
            or contact_value_unshielded
            or (contact_value and not institutional_contact)
            or "public_pii" in concepts
        )
        has_health = (
            _contains_asserted_sensitive_term(own_text, _HEALTH_FIELDS)
            or "health_or_discipline" in concepts
        )
        has_student_sensitive = (
            any(f in own_text for f in _STUDENT_SENSITIVE)
            or "student_sensitive_data" in concepts
            or "student_sensitive_public" in concepts
        )
        # Public/broad disclosure and private external delivery are different
        # governance cases.  The old code folded every `external_release` and
        # every send into `is_public`, so a private health notice to the named
        # parent was RED instead of GREEN.  Public exposure remains RED; a
        # private intended-recipient send is external and therefore GREEN.
        private_audience = audience in {
            "private_recipient", "parent", "guardian", "family",
            "named_recipient", "individual_recipient",
        }
        broad_school_audience = audience in {
            "school_community", "all_parents", "all_staff", "class_group",
        } or scope == "community_draft"
        operation_public = "publish" in op or tool == "publish"
        operation_external = (
            operation_public
            or any(verb in op for verb in ("send", "submit", "message", "release"))
            or tool in {"email", "publish"}
        )
        # Public-facing CONTENT is governed even while it is only a draft.
        # A task-level `public_disclosure` concept, however, must not contaminate
        # the private operator cover or an unrelated internal artifact.
        is_public = (
            scope in ("public", "public_release", "public_draft")
            or (scope == "external" and not private_audience)
            or audience == "public"
            or operation_public
            or (
                "public_disclosure" in concepts
                and (explicit_release_action or operation_public or audience == "public")
            )
        )
        # External is an ACTION property, not a task property. Draft files and
        # the short UI cover remain internal even when the user's overall goal
        # eventually asks to send/publish them. The explicit last gate owns that
        # release intent and is the only step elevated to GREEN.
        is_external = (
            release_state != "draft_only"
            and (
                operation_external
                or explicit_release_action
                or scope in ("external", "public_release")
                or approval_boundary.startswith("human_required")
            )
        )
        is_private_recipient = private_audience and not is_public

        features = {
            "output_scope": scope or None,
            "approval_boundary": approval_boundary or None,
            "release_state": release_state or None,
            "external_release_action": explicit_release_action,
            "audience": audience or None,
            "is_public": is_public,
            "is_external": is_external,
            "is_private_recipient": is_private_recipient,
            "is_broad_school_audience": broad_school_audience,
            # Backward-compatible audit field; new decisions use the split
            # fields above.
            "is_public_or_external": is_public or is_external,
            "socioeconomic_data": has_socio,
            "differential_intent": has_diff,
            "public_pii": has_pii,
            "health_discipline_special": has_health,
            "student_sensitive": has_student_sensitive,
            "persistent_sensitive_learning": (
                "persistent_sensitive_learning" in concepts
            ),
            "official_record_change": "official_record_change" in concepts,
            "financial_value_change": "financial_value_change" in concepts,
            "unsupported_fact_invention": (
                "unsupported_fact_invention" in concepts
            ),
            "workflow_id": md.get("workflow_id"),
        }

        # A workflow may carry its own domain-specific safe alternative for the
        # self-block (config `governance_copy` → step `safe_alternative`), so a
        # charity-bazaar RED card is not described in athletics/student terms.
        step_safe_alt = str(md.get("safe_alternative") or "").strip()

        if tool == "web search" and has_pii:
            return {
                "decision": "RED",
                "reasons": [
                    "Personal identifiers or contact details cannot be sent in an external web-search query.",
                    "safe_alternative: use a case-free official-policy query and keep case facts TBC or ask the authorised school user.",
                ],
                "features": features,
            }

        # ── RED 1 (flagship §I): socioeconomic data → differential treatment ─
        if has_socio and has_diff:
            return {"decision": "RED",
                    "reasons": [_RED_REASON_SOCIO,
                                f"safe_alternative: {step_safe_alt or _SAFE_ALT_SOCIO}"],
                    "features": features}

        # ── RED: personal sensitive facts must not become durable memory ──
        if "persistent_sensitive_learning" in concepts:
            return {
                "decision": "RED",
                "reasons": [
                    "Person-level student or stakeholder-sensitive facts "
                    "cannot be persisted as reusable learning or a durable profile.",
                    "safe_alternative: Store only a non-personal procedure or "
                    "case-local note; keep the sensitive fact inside the current "
                    "access-controlled case.",
                ],
                "features": features,
            }

        # ── RED 2: publish personal identifiers to a public/external surface ─
        if has_pii and (is_public or broad_school_audience):
            return {"decision": "RED",
                    "reasons": ["Personal identifiers (IC/MyKid/passport/phone/"
                                "home address) cannot be published to a public "
                                "or external surface.",
                                "safe_alternative: Remove all personal "
                                "identifiers before any public release."],
                    "features": features}

        # ── RED 3: health / discipline / special-needs in public content ──
        if has_health and (is_public or broad_school_audience):
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
        if has_student_sensitive and (
            is_public or broad_school_audience or scope == "public_draft"
        ):
            return {"decision": "RED",
                    "reasons": [_RED_REASON_STUDENT_PUBLIC,
                                f"safe_alternative: {step_safe_alt or _SAFE_ALT_STUDENT_PUBLIC}"],
                    "features": features}

        # ── INFEASIBLE: missing facts stay unknown rather than invented ──
        if "unsupported_fact_invention" in concepts:
            return {
                "decision": "INFEASIBLE",
                "reasons": [
                    "The requested fact is not supported by the available "
                    "case evidence and must be marked TBC instead of invented."
                ],
                "features": features,
            }

        # ── GREEN: writing/updating an OFFICIAL record is high-impact → verify ─
        # Stronger than ordinary external release: this is the agent reaching a
        # formal administrative write inside its own autonomous workflow and
        # pausing for a human, not a generic publish gate.
        if (scope in ("official_record", "official_write")
                or "official" in approval_boundary
                or "official_record_change" in concepts):
            return {"decision": "GREEN",
                    "reasons": ["Writing or updating an official school "
                                "achievement record is a high-impact "
                                "administrative action — an educator must verify "
                                "the official result before it is written.",
                                "safe_alternative: Prepare the proposed update; a "
                                "human verifies the official result sheet and "
                                "approves before the record is written."],
                    "features": features}

        # ── GREEN: issuing or changing money-like value needs verification ──
        if "financial_value_change" in concepts:
            return {
                "decision": "GREEN",
                "reasons": [
                    "Issuing or changing cash-equivalent coupons, prices, or "
                    "payment arrangements requires an authorised human decision."
                ],
                "features": features,
            }

        # ── GREEN: external publish/send/submit needs human approval ──
        if is_external and scope != "public_draft":
            return {"decision": "GREEN",
                    "reasons": ["External release requires human approval "
                                "before any outside action."],
                    "features": features}

        # ── GREEN: a sensitive mention we can't fully classify → ask a human ─
        # Key on the DECLARED allowed fields, not the user_intent-contaminated
        # text: a step that only USES safe fields (a public event post, a
        # respectful non-pressuring outreach) is not an "unclear sensitive use"
        # just because the workflow goal elsewhere names donors or occupation.
        has_socio_allowed = any(f in allowed_text for f in _SOCIO_FIELDS)
        has_pii_allowed = any(f in allowed_text for f in _PUBLIC_PII)
        has_health_allowed = any(f in allowed_text for f in _HEALTH_FIELDS)
        if ((has_socio_allowed or has_pii_allowed or has_health_allowed)
                and scope != "internal"):
            return {"decision": "GREEN",
                    "reasons": ["Unclear sensitive data use — human approval "
                                "required (fail toward asking a human)."],
                    "features": features}

        # ── NO_OVERRIDE: internal report / public DRAFT with safe fields ──
        return {"decision": "NO_OVERRIDE", "reasons": [], "features": features}
