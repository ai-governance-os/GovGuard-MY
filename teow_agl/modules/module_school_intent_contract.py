"""Ordered user-intent contract for open school-administration requests.

The response-pack compiler is allowed to recommend extra work, but it must not
silently merge or forget work that the user explicitly requested.  This module
therefore gives every requested output its own stable obligation identity.

The module deliberately does *not* decide governance colours or whether an
external action is authorised.  It is a small, dependency-free data contract
that can sit between semantic intake and the deterministic policy runtime.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import re
from typing import Any

from .module_deliverable_mentions import is_requested_output_mention


# A custom (user-added, unlabeled-role) deliverable gets its role defaulted
# independently in two places that never see each other's choice: this
# module's own _normalise_role() falls back to "school_document" when the
# caller sends no artifact_role, while module_school_situation.py's response-
# pack builder falls back to "user_titled_document" for the same "no role
# given" case. Both mean the same thing — "a generic document with no
# catalog role" — so evaluate_deliverable_coverage() must treat them as one
# bucket when matching an obligation to a deliverable, or a legitimately
# fulfilled custom request reads as MISSING purely from a naming mismatch
# (2026-08-18 fix; see CLAUDE_HANDOFF_MAIN_DEMO_DETERMINISTIC_PROBE_FIX_20260817.md §10).
_GENERIC_DOCUMENT_ROLES = frozenset({"school_document", "user_titled_document"})


def _roles_match(obligation_role: str, deliverable_role: str) -> bool:
    if obligation_role == deliverable_role:
        return True
    return (
        obligation_role in _GENERIC_DOCUMENT_ROLES
        and deliverable_role in _GENERIC_DOCUMENT_ROLES
    )


_DEFAULT_AUDIENCE_BY_ROLE = {
    "private_parent_notice": "private_recipient",
    "school_parent_notice": "school_community",
    "public_communication_draft": "public",
    "education_authority_report": "external_agency",
    "education_authority_request": "external_agency",
    "emergency_contact_script": "external_agency",
    "fire_rescue_contact_script": "external_agency",
    "medical_handover_script": "external_agency",
    "external_stakeholder_message": "external_agency",
    "speech_or_address": "school_community",
}

_DEFAULT_RECIPIENT_BY_ROLE = {
    "private_parent_notice": "guardian",
    "school_parent_notice": "school_community",
    "public_communication_draft": "public_media",
    "education_authority_report": "education_authority",
    "education_authority_request": "education_authority",
    "emergency_contact_script": "malaysia_emergency_services_999",
    "fire_rescue_contact_script": "fire_and_rescue",
    "medical_handover_script": "medical_services",
    "external_stakeholder_message": "external_stakeholder",
    "speech_or_address": "school_community",
}

_DEFAULT_RECIPIENT_BY_AUDIENCE = {
    "internal": "school_staff",
    "private_recipient": "guardian",
    "school_community": "school_community",
    "external_agency": "education_authority",
    "public": "public_media",
    "unknown": "unknown",
}

_LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ms": "ms",
    "bm": "ms",
    "malay": "ms",
    "malaysian": "ms",
    "bahasa": "ms",
    "bahasa malaysia": "ms",
    "zh": "zh",
    "cn": "zh",
    "chinese": "zh",
    "mandarin": "zh",
}

# These are policy/runtime controls, not user-facing artifacts.  They should
# not be reported as surprise deliverables when an action list is audited.
_GOVERNANCE_ONLY_ROLES = {
    "external_release_gate",
    "official_record_change_gate",
    "internal_repository_publish_gate",
    "human_approval_gate",
    "clarification_question",
}

_DECLARED_COUNT_WORDS = {
    "one": 1,
    "satu": 1,
    "both": 2,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_DECLARED_COUNT_TOKEN = (
    r"(?:1[0-2]|[1-9]|one|satu|both|two|three|four|five|six|seven|eight|nine|"
    r"ten|eleven|twelve)"
)
_OUTPUT_UNIT = (
    r"(?:files?|documents?|outputs?|artifacts?|drafts?|reports?|records?|"
    r"logs?|forms?|templates?|notices?|messages?|letters?|scripts?|plans?|"
    r"checklists?|summar(?:y|ies)|surat(?:\s+(?:makluman|peribadi))?|"
    r"notis|mesej|laporan|pelan|rekod)"
)
_SEPARATION_QUALIFIER = (
    r"(?:separate(?:d)?|distinct|individual|different|standalone|stand[- ]alone)"
)

_OPEN_ENDED_OUTPUT_DELEGATION = re.compile(
    r"\b(?:whatever|anything|everything)\b[^.!?;\n]{0,80}"
    r"\b(?:appropriate|needed|necessary|suitable|relevant)\b|"
    r"\b(?:what|which)\s+(?:documents?|files?|materials?|outputs?)\b"
    r"[^.!?;\n]{0,80}\b(?:need|needed|prepare|create|produce)\b|"
    r"\b(?:handle|manage|respond\s+to)\s+(?:this|the)\s+(?:case|situation|matter)\b|"
    r"\b(?:response|follow[- ]?up)\s+pack(?:age)?\b",
    re.IGNORECASE,
)

def declared_output_cardinality(text: str, *, maximum: int = 12) -> int:
    """Return a conservative first-party floor for explicitly separate work.

    The semantic model remains responsible for understanding *what* each file
    is.  This parser only preserves a structural fact that should never depend
    on a provider: ``four separate logs`` and ``4 Markdown files`` mean at
    least four distinct output obligations.  Counts without either a file-like
    container or an explicit separation qualifier are intentionally ignored so
    ordinary facts such as "four pupils" cannot become artifact requests.
    """
    value = _clean_text(text, limit=4000).casefold()
    patterns = (
        rf"(?<!\w)(?P<count>{_DECLARED_COUNT_TOKEN})(?!\w)\s+"
        rf"(?:{_SEPARATION_QUALIFIER})\s+(?:markdown\s+)?{_OUTPUT_UNIT}(?!\w)",
        rf"(?<!\w)(?P<count>{_DECLARED_COUNT_TOKEN})(?!\w)\s+"
        rf"(?:markdown\s+)?{_OUTPUT_UNIT}(?!\w)",
    )
    counts: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, value, re.IGNORECASE):
            token = match.group("count").casefold()
            try:
                count = int(token)
            except ValueError:
                count = _DECLARED_COUNT_WORDS.get(token, 0)
            if 1 <= count <= maximum:
                counts.append(count)
    return max(counts, default=0)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_items(value: Any) -> list[Mapping[str, Any]]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    if not isinstance(value, Iterable):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _clean_text(value: Any, *, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _slug(value: Any, *, fallback: str = "output") -> str:
    text = _clean_text(value, limit=120).casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return (text or fallback)[:56]


def _normalise_role(item: Mapping[str, Any]) -> str:
    role = _clean_text(
        item.get("artifact_role")
        or item.get("role")
        or item.get("artifact_type"),
        limit=100,
    ).casefold().replace("-", "_").replace(" ", "_")
    return role or "school_document"


def _normalise_languages(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Sequence[Any] = re.split(r"[,/;+&]|\band\b|\bdan\b", value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = value
    else:
        values = []
    result: list[str] = []
    for raw in values:
        language = _clean_text(raw, limit=40).casefold()
        canonical = _LANGUAGE_ALIASES.get(language)
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def _languages_from_request(text: str) -> list[str]:
    """Recover only language choices explicitly named in the raw request."""
    value = _clean_text(text, limit=4000).casefold()
    result: list[str] = []
    patterns = (
        ("en", r"(?<!\w)(?:english|eng)(?!\w)"),
        ("ms", r"(?<!\w)(?:malay|bahasa malaysia|bahasa|bm)(?!\w)"),
        ("zh", r"(?<!\w)(?:chinese|mandarin|zh)(?!\w)"),
    )
    for language, pattern in patterns:
        if re.search(pattern, value) and language not in result:
            result.append(language)
    return result


def _extract_situation(semantics: Mapping[str, Any]) -> Mapping[str, Any]:
    situation = semantics.get("situation")
    if isinstance(situation, Mapping):
        return situation
    # Accept a situation object directly.  This keeps the function useful for
    # callers at either side of SchoolInputSemantics._normalise().
    if "requested_outputs" in semantics:
        return semantics
    return {}


def _semantic_output_is_explicit(
    user_text: str,
    item: Mapping[str, Any],
) -> bool:
    """Distinguish a named user deliverable from a model recommendation.

    A semantic model may sensibly propose an incident report when the user
    asks for "whatever response package is appropriate". That proposal must
    not become an *explicit user obligation* and suppress policy-backed pack
    completion. Source-recovered catalog roles carry ``source_named``. For an
    unfamiliar generic school document, require its artifact phrase to appear
    in the source request (for example three separately named logs).
    """
    if item.get("source_named") is True:
        return True
    role = _normalise_role(item)
    raw_source = _clean_text(user_text, limit=4000).casefold()
    source = re.sub(r"[^a-z0-9]+", " ", raw_source).strip()
    label = re.sub(
        r"[^a-z0-9]+",
        " ",
        _clean_text(
            item.get("label") or item.get("title") or item.get("name"),
            limit=160,
        ).casefold(),
    ).strip()
    role_noun_patterns = {
        "private_parent_notice": r"\b(?:school|parent|guardian|community)?\s*(?:notice|notification|message|letter|circular|email)\b|\b(?:parent|guardian|famil(?:y|ies))\s+communication\b|\bcommunication\s+(?:for|to)\s+(?:parents?|guardians?|families)\b",
        "school_parent_notice": r"\b(?:school|parent|guardian|community)?\s*(?:notice|notification|message|letter|circular|email)\b|\b(?:parent|guardian|famil(?:y|ies))\s+communication\b|\bcommunication\s+(?:for|to)\s+(?:parents?|guardians?|families)\b|\bfamilies\s+need\s+to\s+know\b[^.!?;\n]{0,90}\bcommunication\b",
        "staff_internal_notice": r"\b(?:staff|teacher|internal|school)?\s*(?:notice|briefing|message|memo(?:randum)?)\b|\binternal\s+communication\b|\b(?:memo|notis)\s+(?:dalaman\s+)?(?:kepada\s+)?(?:semua\s+)?(?:staf|guru)\b",
        "public_communication_draft": r"\b(?:public|media|facebook)?\s*(?:statement|post|notice|message|announcement)\b",
        "internal_incident_report": r"\b(?:incident|accident)\s+report\b",
        "education_authority_report": r"\b(?:authority|district|education|official)\s+report\b",
        "education_authority_request": r"\b(?:authority|district|education|official)\s+request\b",
        "external_stakeholder_message": r"\b(?:external|stakeholder|vendor|supplier|organiser|organizer|transport|bus\s+company)?\s*(?:message|email|letter)\b",
        "student_support_plan": r"\b(?:student|pupil|learning|support|intervention)\s+plan\b",
        "safeguarding_action_plan": r"\b(?:safeguarding|protection|welfare)\s+(?:action\s+)?plan\b",
        "event_action_plan": r"\b(?:event\s+)?action\s+plan\b",
        "site_safety_checklist": r"\b(?:site|facility|safety)\s+(?:inspection\s+)?checklist\b",
        "evidence_preservation_log": r"\b(?:evidence|chain of custody)\s+(?:preservation\s+)?log\b",
        "regulatory_notification_assessment": r"\bregulatory\s+(?:notification\s+)?assessment\b",
        "speech_or_address": r"\b(?:speech|address|remarks?|emcee\s+script)\b",
        "meeting_minutes": r"\b(?:meeting|committee|pta|staff)\s+minutes\b|\bminutes\s+of\s+(?:the\s+)?(?:pta|committee|staff)?\s*meeting\b|\b(?:prepare|draft|write|create|produce|generate)\b[^.\n]{0,50}\bminutes\b[^.\n]{0,50}\bmeeting\b",
        "duty_roster": r"\b(?:duty|teacher|staff)\s+roster\b|\broster\s+of\s+duties\b",
        "timetable_or_schedule": r"\b(?:timetable|schedule)\b",
        "curriculum_continuity_plan": r"\b(?:curriculum|teaching|learning|lesson|class)\s+continuity\s+plan\b",
        "user_titled_document": r"\b(?:document|file|draft|template|form|record)\b",
    }
    if (
        role in {"private_parent_notice", "school_parent_notice"}
        and re.search(r"\b(?:parents?|guardians?|famil(?:y|ies))\b", raw_source)
        and is_requested_output_mention(raw_source, r"\bcommunication\b")
    ):
        return True
    role_pattern = role_noun_patterns.get(role)
    if role_pattern and is_requested_output_mention(raw_source, role_pattern):
        return True
    if role not in {"school_document", "user_titled_document"}:
        return False
    # Generic catalog roles still need first-party evidence. Permit a semantic
    # label to recover the role only when the same high-precision artifact noun
    # is present in both the label and the user's request. This covers
    # paraphrases such as "Headmaster's speech" without allowing a provider-
    # added "event action plan" to claim explicit status via explicit=true.
    artifact_nouns = (
        r"\b(?:speech|address|remarks?|roster|timetable|schedule|minutes|"
        r"memo(?:randum)?|checklist|form|log|report|notice|notification|"
        r"message|letter|plan|record|agenda|briefing)\b",
        r"\b(?:ucapan|jadual|minit|memo|notis|surat|laporan|pelan|rekod|"
        r"borang|senarai\s+semak|taklimat)\b",
    )
    for noun_pattern in artifact_nouns:
        source_nouns = {
            match.group(0).casefold()
            for match in re.finditer(noun_pattern, source, re.IGNORECASE)
        }
        label_nouns = {
            match.group(0).casefold()
            for match in re.finditer(noun_pattern, label, re.IGNORECASE)
        }
        for noun in source_nouns.intersection(label_nouns):
            if is_requested_output_mention(
                raw_source, rf"(?<!\w){re.escape(noun)}(?!\w)"
            ):
                return True
    if not label or len(label.split()) < 2:
        return False
    return is_requested_output_mention(
        raw_source, rf"(?<!\w){re.escape(label)}(?!\w)"
    )


def _single_semantic_output_is_explicit(
    user_text: str,
    semantics: Mapping[str, Any],
    item: Mapping[str, Any],
) -> bool:
    """Return true only when a single candidate has first-party grounding.

    Cardinality is not provenance. A provider returning one recommended
    artifact with ``explicit=true`` must not turn that proposal into a
    protected user obligation.
    """
    if item.get("source_named") is True:
        return True
    if _OPEN_ENDED_OUTPUT_DELEGATION.search(str(user_text or "")):
        return False
    return _semantic_output_is_explicit(user_text, item)


def _normalise_obligation_seed(
    item: Mapping[str, Any],
    *,
    source: str,
    default_audience: str,
    request_languages: Sequence[str],
) -> dict[str, Any]:
    role = _normalise_role(item)
    label = _clean_text(
        item.get("label")
        or item.get("title")
        or item.get("name")
        or role.replace("_", " ").title(),
        limit=160,
    )
    purpose = _clean_text(
        item.get("purpose")
        or item.get("description")
        or item.get("requested_action"),
        limit=500,
    )
    audience = _clean_text(
        item.get("audience")
        or default_audience
        or _DEFAULT_AUDIENCE_BY_ROLE.get(role)
        or "internal",
        limit=80,
    ).casefold().replace(" ", "_")
    if not audience:
        audience = "internal"
    recipient = _clean_text(
        item.get("recipient_type")
        or item.get("recipient")
        or item.get("stakeholder")
        or _DEFAULT_RECIPIENT_BY_ROLE.get(role)
        or _DEFAULT_RECIPIENT_BY_AUDIENCE.get(audience)
        or "unknown",
        limit=120,
    ).casefold().replace(" ", "_")
    languages = _normalise_languages(
        item.get("languages") or item.get("language")
    )
    if not languages:
        languages = list(request_languages)
    fact_ids = [
        _clean_text(fact_id, limit=100)
        for fact_id in (item.get("source_fact_ids") or [])
        if _clean_text(fact_id, limit=100)
    ]
    return {
        "artifact_role": role,
        "label": label,
        "purpose": purpose,
        "audience": audience,
        "recipient_type": recipient,
        "languages": languages,
        "source_fact_ids": fact_ids,
        "explicit": item.get("explicit") is not False,
        "source": _clean_text(item.get("source") or source, limit=80),
    }


def _fingerprint(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("artifact_role"),
        _clean_text(item.get("label"), limit=160).casefold(),
        _clean_text(item.get("purpose"), limit=500).casefold(),
        item.get("audience"),
        item.get("recipient_type"),
        tuple(item.get("languages") or []),
    )


def build_user_intent_contract(
    user_text: str,
    semantics: Mapping[str, Any] | None = None,
    *,
    source_outputs: Iterable[Mapping[str, Any]] | None = None,
    custom_deliverables: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an ordered, one-obligation-per-output request contract.

    ``semantics`` may be either the full normalised semantic result or its
    nested ``situation`` object.  Semantic requested outputs have precedence.
    Exact copies supplied by a secondary source parser are skipped, but two
    outputs from the *same* source are never merged merely because their roles
    match.  Custom deliverables are always retained as separate obligations.
    """
    raw_text = str(user_text or "")
    semantic_map = _as_mapping(semantics)
    situation = _extract_situation(semantic_map)
    default_audience = _clean_text(
        semantic_map.get("audience") or situation.get("audience") or "",
        limit=80,
    ).casefold()
    request_languages = _languages_from_request(raw_text)
    declared_count = declared_output_cardinality(raw_text)

    semantic_outputs = _as_items(situation.get("requested_outputs"))
    groups = (
        (
            "semantic_requested_output",
            semantic_outputs,
            True,
        ),
        ("source_output", _as_items(source_outputs), True),
        ("custom_deliverable", _as_items(custom_deliverables), False),
    )
    seeds: list[dict[str, Any]] = []
    fingerprints_by_earlier_source: set[tuple[Any, ...]] = set()
    for source, items, deduplicate_against_earlier in groups:
        source_fingerprints: set[tuple[Any, ...]] = set()
        for item_index, item in enumerate(items):
            normalised_item = dict(item)
            if source == "semantic_requested_output":
                # requested_outputs is defined as an explicit-only semantic
                # field.  When the source text independently declares N
                # separate outputs, the first N semantic slots are therefore
                # explicit even if the provider paraphrases their labels.
                normalised_item["explicit"] = bool(
                    item.get("declared_slot_explicit") is True
                    or (declared_count and item_index < declared_count)
                    or _semantic_output_is_explicit(raw_text, item)
                    or (
                        len(semantic_outputs) == 1
                        and _single_semantic_output_is_explicit(
                            raw_text, semantic_map, item
                        )
                    )
                )
            seed = _normalise_obligation_seed(
                normalised_item,
                source=source,
                default_audience=default_audience,
                request_languages=request_languages,
            )
            fingerprint = _fingerprint(seed)
            if deduplicate_against_earlier and fingerprint in fingerprints_by_earlier_source:
                continue
            # Do not deduplicate within one source: three explicit logs with
            # the same canonical role still mean three requested artifacts.
            seeds.append(seed)
            source_fingerprints.add(fingerprint)
        fingerprints_by_earlier_source.update(source_fingerprints)

    obligations: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds, start=1):
        obligation = dict(seed)
        obligation["obligation_id"] = (
            f"intent_{index:02d}_{_slug(seed.get('label') or seed.get('artifact_role'))}"
        )
        obligation["order"] = index
        obligations.append(obligation)

    digest_input = "\n".join([
        raw_text,
        *[
            "|".join((
                str(item["order"]),
                str(item["artifact_role"]),
                str(item["label"]),
                str(item["audience"]),
                str(item["recipient_type"]),
                ",".join(item["languages"]),
            ))
            for item in obligations
        ],
    ])
    explicit_count = sum(1 for item in obligations if item["explicit"])
    cardinality_complete = (
        declared_count == 0 or explicit_count >= declared_count
    )
    return {
        "schema_version": "school_intent_contract.v1",
        "contract_id": "intent_" + hashlib.sha256(
            digest_input.encode("utf-8")
        ).hexdigest()[:16],
        "source_request": raw_text,
        "obligations": obligations,
        "explicit_count": explicit_count,
        "declared_output_count": declared_count,
        "cardinality_complete": cardinality_complete,
        "cardinality_gap": max(0, declared_count - explicit_count),
    }


def _deliverable_id(deliverable: Mapping[str, Any], index: int) -> str:
    value = (
        deliverable.get("deliverable_id")
        or deliverable.get("artifact_id")
        or deliverable.get("filename")
        or deliverable.get("file_name")
    )
    return _clean_text(value, limit=180) or f"deliverable_{index:02d}"


def _linked_obligation_id(deliverable: Mapping[str, Any]) -> str:
    return _clean_text(
        deliverable.get("obligation_id")
        or deliverable.get("intent_obligation_id")
        or deliverable.get("source_obligation_id"),
        limit=180,
    )


def _label_tokens(value: Any) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", _clean_text(value).casefold())
        if len(token) > 2
    }


def evaluate_deliverable_coverage(
    contract: Mapping[str, Any] | None,
    deliverables: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Align final artifacts to obligations without role-based collapsing.

    Matching prefers an explicit obligation link, then a same-role label match,
    then the next unmatched obligation with that role.  One artifact covers at
    most one obligation, so a single generic document cannot accidentally make
    three same-role requests appear complete.
    """
    contract_map = _as_mapping(contract)
    # Only first-party obligations belong in goal coverage. Semantic
    # recommendations remain visible in the contract for audit, but they must
    # be treated as unrequested deliverables so a concrete user request can
    # deselect them instead of protecting them as required work.
    obligations = [
        dict(item)
        for item in _as_items(contract_map.get("obligations"))
        if item.get("explicit") is True
    ]
    obligation_by_id = {
        str(item.get("obligation_id") or ""): item
        for item in obligations
        if item.get("obligation_id")
    }
    remaining_ids = list(obligation_by_id)
    covered: list[dict[str, Any]] = []
    unrequested: list[dict[str, Any]] = []
    ignored_governance: list[dict[str, Any]] = []

    for index, deliverable in enumerate(_as_items(deliverables), start=1):
        item = dict(deliverable)
        deliverable_id = _deliverable_id(item, index)
        role = _normalise_role(item)
        if role in _GOVERNANCE_ONLY_ROLES or item.get("governance_only") is True:
            ignored_governance.append({
                "deliverable_id": deliverable_id,
                "artifact_role": role,
            })
            continue

        match_id = ""
        match_reason = ""
        linked_id = _linked_obligation_id(item)
        if linked_id in remaining_ids:
            match_id = linked_id
            match_reason = "explicit_obligation_link"
        elif linked_id:
            # A stale/unknown explicit link must not be silently reassigned.
            unrequested.append({
                "deliverable_id": deliverable_id,
                "artifact_role": role,
                "reason": "unknown_or_already_covered_obligation_link",
                "linked_obligation_id": linked_id,
            })
            continue
        else:
            same_role = [
                oid for oid in remaining_ids
                if _roles_match(
                    str(obligation_by_id[oid].get("artifact_role") or ""), role,
                )
            ]
            if same_role:
                deliverable_label = (
                    item.get("label") or item.get("title") or item.get("filename")
                    or item.get("file_name") or deliverable_id
                )
                d_tokens = _label_tokens(deliverable_label)
                scored = []
                for oid in same_role:
                    o_tokens = _label_tokens(obligation_by_id[oid].get("label"))
                    score = len(d_tokens.intersection(o_tokens))
                    scored.append((score, -int(obligation_by_id[oid].get("order") or 0), oid))
                best_score, _, best_id = max(scored)
                match_id = best_id if best_score > 0 else same_role[0]
                match_reason = "role_and_label" if best_score > 0 else "role_order"

        if not match_id:
            unrequested.append({
                "deliverable_id": deliverable_id,
                "artifact_role": role,
                "reason": "no_matching_obligation",
            })
            continue
        remaining_ids.remove(match_id)
        covered.append({
            "obligation_id": match_id,
            "deliverable_id": deliverable_id,
            "artifact_role": role,
            "match_reason": match_reason,
            "obligation": obligation_by_id[match_id],
        })

    missing = [obligation_by_id[oid] for oid in remaining_ids]
    cardinality_complete = contract_map.get("cardinality_complete") is not False
    return {
        "pass": not missing and cardinality_complete,
        "cardinality_complete": cardinality_complete,
        "cardinality_gap": int(contract_map.get("cardinality_gap") or 0),
        "covered": covered,
        "missing": missing,
        "unrequested": unrequested,
        "ignored_governance": ignored_governance,
        "covered_obligation_ids": [item["obligation_id"] for item in covered],
        "missing_obligation_ids": [item["obligation_id"] for item in missing],
        "unrequested_deliverable_ids": [
            item["deliverable_id"] for item in unrequested
        ],
    }


# Clear aliases for callers that prefer the shorter names.
build_intent_contract = build_user_intent_contract
align_deliverables_to_contract = evaluate_deliverable_coverage


__all__ = [
    "align_deliverables_to_contract",
    "build_intent_contract",
    "build_user_intent_contract",
    "declared_output_cardinality",
    "evaluate_deliverable_coverage",
]
