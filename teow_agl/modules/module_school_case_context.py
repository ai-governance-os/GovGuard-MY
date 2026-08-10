"""Bounded, task-local case continuity for open school inputs.

The semantic model may propose that a message is a follow-up, but an opaque
``task_id`` is not case evidence.  This module compares the current request
with a compact snapshot of the candidate parent case, validates the relation,
and merges only already-grounded facts after continuity is confirmed.

It never chooses a governance route and never writes to persistent memory.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


_GENERIC_TOPIC_TOKENS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "do",
    "for", "from", "has", "have", "help", "how", "i", "in", "is",
    "it", "of", "on", "or", "our", "please", "prepare", "school",
    "student", "students", "pupil", "pupils", "parent", "parents",
    "teacher", "teachers", "the", "their", "them", "they", "this",
    "that", "to", "us", "we", "what", "when", "with", "write",
    "draft", "document", "report", "notice", "post", "plan", "message",
    "day", "days",
    "internal", "public", "private", "send", "publish", "make",
    "revise", "update", "edit", "shorten", "rewrite", "translate",
    "reword", "correct", "remove", "replace", "keep", "new",
    "pendekkan", "ringkaskan", "kekalkan", "ubah", "terjemah",
    "about", "tomorrow", "today", "yesterday", "now", "next", "later",
    "morning", "afternoon", "evening",
    "was", "were", "will", "would", "could", "should", "can", "must",
    "add", "again", "all", "also", "any", "anything", "but", "continue",
    "continuing", "earlier", "follow", "followup", "may", "might", "not",
    "nothing", "previous", "remaining", "said", "same", "say", "says",
    "still",
}

# Continuity words are useful as *cues*, never as proof that two cases are the
# same. Keep this second guard because a candidate snapshot may have been
# created before a newly-added topic stopword existed.
_CONTINUITY_EVIDENCE_STOPWORDS = {
    "add", "again", "all", "also", "any", "anything", "but", "continue",
    "continuing", "earlier", "follow", "followup", "may", "might", "not",
    "nothing", "previous", "remaining", "said", "same", "say", "says",
    "still",
}

_EXPLICIT_NEW = re.compile(
    r"\b(?:new|another|separate|different|unrelated)\s+(?:case|matter|incident|request)\b",
    re.IGNORECASE,
)
_CLEAR_CONTEXT_SHIFT = re.compile(
    r"\b(?:another|different|unrelated|separate)\s+"
    r"(?:school|site|campus|building|block|case|matter|incident|event|"
    r"request|issue|situation)\b",
    re.IGNORECASE,
)
_STRONG_EDIT = re.compile(
    r"\b(?:revise|update|edit|shorten|rewrite|translate|reword|correct|"
    r"add\s+to|remove\s+from|replace|keep|make\s+it|send\s+it|publish\s+it|"
    r"do\s+not\s+use|don['\u2019]?t\s+use|"
    r"do\s+not\s+(?:repeat|recreate|regenerate)|"
    r"don['\u2019]?t\s+(?:repeat|recreate|regenerate)|"
    r"only\s+add|add\s+only)\b|"
    r"\bmake\s+[^.!?\r\n]{0,80}\b(?:report|notice|post|draft|plan|message)"
    r"\s+(?:shorter|longer|clearer|simpler)\b|"
    r"\b(?:pendekkan|ringkaskan|kekalkan|kemas\s+kini|ubah|terjemah)\b|"
    r"(?:\u4fee\u6539|\u66f4\u65b0|\u6539\u77ed|\u52a0\u4e0a|\u5220\u9664|\u522a\u9664|\u7ffb\u8bd1|\u7ffb\u8b6f)",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"\b(?:still|again|continue|continuing|remaining|follow[- ]?up|"
    r"as\s+above|previous|earlier|after\s+that|subsequently)\b|"
    r"\b(?:was|were|is|are|has|have|had)\s+later\b|"
    r"(?:^|[.!?]\s+)(?:then|later)\b|"
    r"\b(?:the\s+)?same\s+(?:case|matter|incident|issue|request|report|notice|"
    r"post|draft|plan|message)\b|"
    r"(?:\u7ee7\u7eed|\u7e7c\u7e8c|\u63a5\u7740|\u63a5\u8457|\u4ecd\u7136|\u4f9d\u7136|\u8fd8\u662f|\u9084\u662f|\u540c\u6837|\u540c\u6a23)",
    re.IGNORECASE,
)
_WEAK_REFERENCE = re.compile(
    r"\b(?:this|it|these|those|them|the\s+(?:report|notice|post|draft|"
    r"plan|message)|all\s+the\s+details)\b|"
    r"(?:\u8fd9\u4e2a|\u9019\u500b|\u90a3\u4e2a|\u90a3\u500b|\u5b83|\u4ed6\u4eec|\u4ed6\u5011)",
    re.IGNORECASE,
)
_ACTION_DEICTIC_REFERENCE = re.compile(
    r"\b(?:do|send|publish|share|forward|email|revise|update|edit|change|"
    r"shorten|translate|use)\s+(?:this|that)\b",
    re.IGNORECASE,
)
_COMPACT_PRONOUN_REFERENCE = re.compile(
    r"^\s*(?:he|she|him|his|her|they|their)\b",
    re.IGNORECASE,
)
_ATTRIBUTED_PARENT_ANAPHORA = re.compile(
    r"^\s*the\s+(?P<entity>caller|witness|driver)\s+"
    r"(?:said|says|reported|reports|stated|states|confirmed|confirms|added|adds)"
    r"\s+(?:that\s+)?(?:it|he|she|they)\b",
    re.IGNORECASE,
)
_DEFINITE_OBJECT_REFERENCE = re.compile(
    r"^\s*the\s+(?P<entity>bus|vehicle|snake|animal)\b",
    re.IGNORECASE,
)
_INTRODUCED_ARTIFACT_SUBJECT = re.compile(
    r"\b(?:report|notice|post|plan|message|draft|checklist)\s+"
    r"(?:about|for|regarding|concerning|on)\s+"
    r"(?:a|an|another|new|different|unrelated)\b",
    re.IGNORECASE,
)
_LOCAL_ARTIFACT_CREATION = re.compile(
    r"\b(?:draft|prepare|write|create|produce|generate|sediakan|hasilkan|buat)\b"
    r"[^.!?\r\n]{0,140}\b"
    r"(?:report|notice|post|plan|message|draft|checklist|laporan|notis|mesej)\b",
    re.IGNORECASE,
)
_LOCAL_DRAFT_ONLY_PRONOUN = re.compile(
    r"\b(?:do\s+not|don't|never|jangan)\s+"
    r"(?:send|publish|share|forward|email|hantar|siarkan|kongsi)\s+"
    r"(?:it|this|that|ini|itu)\b",
    re.IGNORECASE,
)
_LOCAL_NOMINAL_UPDATE = re.compile(
    r"\b(?:(?:a|an|the)\s+)?(?:(?:private|public|internal)\s+)?"
    r"(?:parent|guardian|staff|stakeholder|school|status|progress|incident)"
    r"\s+update\b",
    re.IGNORECASE,
)
_NONREFERENTIAL_IT_COPULA = re.compile(
    r"\bit\s+(?:is|was|has\s+been|will\s+be)\s+"
    r"(?:"
    r"(?:currently\s+|now\s+)?(?:raining|pouring|storming|snowing)\b|"
    r"(?:very\s+|too\s+)?(?:rainy|windy|humid|hot|cold|dark|late|early)\b|"
    r"getting\s+(?:dark|late|hot|cold)\b|"
    r"(?:very\s+|too\s+)?(?:hard|difficult|impossible|unsafe|dangerous|"
    r"urgent|important|necessary)\s+"
    r"(?:(?:for|of)\s+[^.!?;\r\n]{1,80}\s+)?to\s+[a-z]+\b"
    r")",
    re.IGNORECASE,
)
_REFERENTIAL_ARTIFACT = re.compile(
    r"\b(?:this|that|the|same|previous|earlier|original|existing|current)\s+"
    r"(?:(?:internal|incident|parent|staff|school|public)\s+)?"
    r"(?:report|notice|post|draft|plan|message|checklist)\b",
    re.IGNORECASE,
)
_PRIOR_ARTIFACT_EXCLUSION = re.compile(
    r"\b(?:do\s+not|don['\u2019]?t|never)\s+"
    r"(?:repeat|recreate|regenerate|duplicate)\s+"
    r"(?:(?:this|that|the)\s+)?"
    r"(?:(?:same|previous|earlier|original|existing|current)\s+)?"
    r"(?:(?:internal|incident|parent|staff|school|public)\s+)?"
    r"(?:report|notice|post|draft|plan|message|checklist)\b",
    re.IGNORECASE,
)
_ADDITIVE_ONLY = re.compile(r"\b(?:only\s+add|add\s+only)\b", re.IGNORECASE)
_COLON_SCOPED_THIS_ARTIFACT = re.compile(
    r"^\s*(?P<verb>send|publish|email|forward|share)\s+this\s+"
    r"(?P<artifact>message|notice|email|letter|request|report)\s+to\b"
    r"(?P<recipient>[^:\r\n]{1,200}):(?=\s*\S)",
    re.IGNORECASE,
)
_FACT_STATUS_EDIT = re.compile(
    r"\b(?:tbc|to[- ]?be[- ]?confirmed|unverified|confirmed|unknown|"
    r"placeholder)\b|(?:\u5f85\u786e\u8ba4|\u5f85\u78ba\u8a8d|\u672a\u786e\u8ba4|\u672a\u78ba\u8a8d)",
    re.IGNORECASE,
)
_GENERIC_FOLLOWUP = re.compile(
    r"\b(?:what\s+next|what\s+(?:should|do)\s+we\s+do(?:\s+now)?|"
    r"tell\s+(?:the\s+)?parents\s+now|apa\s+(?:langkah\s+)?seterusnya|"
    r"beritahu\s+ibu\s+bapa\s+sekarang)\b|"
    r"(?:\u63a5\u4e0b\u6765\u600e\u4e48\u529e|\u63a5\u4e0b\u4f86\u600e\u9ebc\u8fa6|\u4e0b\u4e00\u6b65(?:\u600e\u4e48\u529e|\u600e\u9ebc\u8fa6)?)",
    re.IGNORECASE,
)
_GENERIC_CONFIRM_ALL = re.compile(
    r"\b(?:all|everything|every\s+detail)\s+(?:is\s+|are\s+|as\s+)?confirmed\b|"
    r"(?:\u5168\u90e8|\u6240\u6709\u8d44\u6599|\u6240\u6709\u8cc7\u6599).{0,8}(?:\u5df2\u786e\u8ba4|\u5df2\u78ba\u8a8d)",
    re.IGNORECASE,
)

_ARTIFACT_TERMS = {
    "report": re.compile(r"\breport\b|\u62a5\u544a|\u5831\u544a", re.IGNORECASE),
    "notice": re.compile(
        r"\b(?:notice|circular|whatsapp\s+message|notis|makluman)\b|\u901a\u77e5",
        re.IGNORECASE,
    ),
    "post": re.compile(r"\b(?:post|facebook|social\s+media)\b|\u8d34\u6587|\u8cbc\u6587|\u8138\u4e66|\u81c9\u66f8", re.IGNORECASE),
    "plan": re.compile(r"\b(?:plan|checklist)\b|\u8ba1\u5212|\u8a08\u5283|\u6e05\u5355|\u6e05\u55ae", re.IGNORECASE),
    "message": re.compile(r"\b(?:message|email|letter)\b|\u4fe1\u606f|\u8a0a\u606f|\u4fe1\u4ef6", re.IGNORECASE),
}


def _topic_tokens(value: str) -> set[str]:
    # Canonicalise a very small set of high-confidence bilingual case labels.
    # This is relation evidence only; it does not translate or interpret the
    # user's task. Keeping the aliases phrase-bounded prevents generic Malay
    # words such as hari from linking otherwise unrelated school cases.
    canonical = value or ""
    canonical = re.sub(
        r"\bhari\s+kitar\s+semula\b",
        "recycling day",
        canonical,
        flags=re.IGNORECASE,
    )
    canonical = re.sub(
        r"\bbahasa\s+inggeris\b",
        "english",
        canonical,
        flags=re.IGNORECASE,
    )
    canonical = re.sub(
        r"\bbahasa\s+melayu\b",
        "malay",
        canonical,
        flags=re.IGNORECASE,
    )
    return {
        token for token in re.findall(r"[^\W_]+", canonical.casefold(), re.UNICODE)
        if len(token) > 1 and token not in _GENERIC_TOPIC_TOKENS
    }


def _artifact_kind(role: str) -> str:
    value = (role or "").casefold()
    if "report" in value or "assessment" in value or "memo" in value:
        return "report"
    if "notice" in value:
        return "notice"
    if "public_communication" in value or "post" in value:
        return "post"
    if "plan" in value or "checklist" in value or "response" in value:
        return "plan"
    if "message" in value or "request" in value or "script" in value:
        return "message"
    return ""


def build_case_context(
    *,
    case_context_id: str | None,
    source_task_id: str | None,
    raw_goal: str,
    school_situation: dict | None,
    response_pack: dict | None,
) -> dict:
    """Create a compact snapshot from an already-governed task state."""
    situation = deepcopy(school_situation or {})
    source_task_ids = sorted({
        str(item).strip()
        for item in [
            *(situation.get("case_source_task_ids") or []),
            source_task_id,
        ]
        if str(item or "").strip()
    })
    for fact in situation.get("known_facts") or []:
        if not isinstance(fact, dict):
            continue
        fact.setdefault("source_type", "task_grounded")
        if source_task_id:
            fact.setdefault("source_task_id", source_task_id)
        if case_context_id:
            fact.setdefault("source_case_context_id", case_context_id)
    situation["case_source_task_ids"] = source_task_ids
    pack = deepcopy(response_pack or {})
    deliverables = [
        {
            "deliverable_id": str(item.get("deliverable_id") or "")[:120],
            "artifact_role": str(item.get("artifact_role") or "")[:120],
            "label": str(item.get("label") or "")[:160],
            "kind": str(item.get("kind") or "artifact")[:40],
            "audience": str(item.get("audience") or "")[:40],
            "recipient_type": str(item.get("recipient_type") or "")[:80],
            "channel": str(item.get("channel") or "")[:40],
            "requested_languages": [
                str(language).strip().lower()
                for language in (item.get("requested_languages") or [])
                if str(language).strip().lower() in {"en", "ms", "zh"}
            ],
        }
        for item in (pack.get("deliverables") or [])
        if isinstance(item, dict) and item.get("kind", "artifact") == "artifact"
    ]
    evidence_parts = [
        str(situation.get("case_summary") or ""),
        str(raw_goal or ""),
        *[
            str(item.get("value") or "")
            for item in (situation.get("known_facts") or [])
            if isinstance(item, dict)
        ],
    ]
    return {
        "case_context_id": case_context_id,
        "source_task_id": source_task_id,
        "raw_goal": str(raw_goal or "")[:4000],
        "situation": situation,
        "response_pack": pack,
        "deliverables": deliverables,
        "topic_tokens": sorted(_topic_tokens(" ".join(evidence_parts))),
        "source_task_ids": source_task_ids,
    }


def resolve_case_relation(
    text: str,
    semantics: dict,
    active_case_context: dict | None,
) -> dict:
    """Validate the model-proposed case relation against bounded evidence.

    A same-domain, self-contained request is a new case.  A follow-up requires
    an actual referent: shared case-specific terms, a uniquely referenced prior
    artifact, or a compact edit instruction.  Unresolved deixis is ambiguous
    and therefore receives no prior facts.
    """
    result = deepcopy(semantics or {})
    proposed = str(result.get("case_relation") or "ambiguous").lower()
    if result.get("school_domain") is not True:
        result["case_relation"] = "unrelated"
        return result

    if not active_case_context:
        result["case_relation"] = (
            "ambiguous" if proposed == "follow_up" else "new_case"
        )
        result["case_relation_evidence"] = {
            "proposed": proposed,
            "validated": result["case_relation"],
            "reason": "no_parent_case_snapshot",
            "shared_topic_tokens": [],
            "referenced_artifacts": [],
        }
        return result

    if _EXPLICIT_NEW.search(text or "") or _CLEAR_CONTEXT_SHIFT.search(text or ""):
        validated = "new_case"
        reason = "explicit_new_case"
        shared: list[str] = []
        referenced: list[str] = []
        referenced_entities: list[str] = []
    else:
        # In "Send this message to X: <content>", "this message" points to
        # the colon-scoped content in the current request, not to an artifact
        # from the candidate parent case. Remove only that local determiner;
        # the artifact kind and the substantive text remain available for all
        # other case-evidence checks.
        relation_text = _COLON_SCOPED_THIS_ARTIFACT.sub(
            lambda match: (
                f"{match.group('verb')} {match.group('artifact')} to"
                f"{match.group('recipient')}:"
            ),
            text or "",
        )
        current_tokens = _topic_tokens(text)
        parent_tokens = set(active_case_context.get("topic_tokens") or [])
        shared = sorted(
            current_tokens.intersection(parent_tokens)
            - _CONTINUITY_EVIDENCE_STOPWORDS
        )

        requested_kinds = {
            kind for kind, pattern in _ARTIFACT_TERMS.items()
            if pattern.search(text or "")
        }
        prior_roles = [
            str(item.get("artifact_role") or "")
            for item in (active_case_context.get("deliverables") or [])
            if isinstance(item, dict)
        ]
        prior_kinds = [_artifact_kind(role) for role in prior_roles]
        referenced = sorted({
            kind for kind in requested_kinds if prior_kinds.count(kind) == 1
        })
        # A complete request can create a new artifact and then say "do not
        # send it". That pronoun points to the artifact introduced in this
        # request, not to the previous case. Mask only this narrow draft-only
        # qualifier; a standalone "do not send it" remains a genuine compact
        # follow-up to the prior artifact.
        relation_cue_text = relation_text
        if _LOCAL_ARTIFACT_CREATION.search(relation_text):
            relation_cue_text = _LOCAL_DRAFT_ONLY_PRONOUN.sub(
                "", relation_cue_text,
            )
            # "update" can be a newly requested artifact noun (for example,
            # "prepare a private parent update"), not an instruction to edit
            # the previous case. Keep the imperative "update the report"
            # signal intact while masking only these bounded noun phrases.
            relation_cue_text = _LOCAL_NOMINAL_UPDATE.sub(
                "local notice", relation_cue_text,
            )
        strong_edit = bool(_STRONG_EDIT.search(relation_cue_text))
        referential_artifact = bool(
            _REFERENTIAL_ARTIFACT.search(relation_cue_text)
        )
        prior_artifact_exclusion = bool(
            _PRIOR_ARTIFACT_EXCLUSION.search(relation_cue_text)
        )
        additive_only = bool(_ADDITIVE_ONLY.search(relation_cue_text))
        unresolved_artifact = bool(
            requested_kinds
            and not referenced
            and (strong_edit or referential_artifact)
        )
        continuation = bool(_CONTINUATION.search(text or ""))
        reference_text = re.sub(
            r"\bthis\s+(?:year|term|week|month|morning|afternoon|evening|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"weekend|date|time)\b",
            "", relation_cue_text, flags=re.IGNORECASE,
        )
        reference_text = re.sub(
            r"\b(?:this|it)\s+(?:is|was)\s+(?:only\s+)?(?:a\s+)?"
            r"(?:drill|simulation|exercise|test)\b",
            "",
            reference_text,
            flags=re.IGNORECASE,
        )
        # English dummy/expletive "it" does not point to the prior case.
        # Weather ("it is raining") and extraposition ("it is difficult for
        # the pupil to breathe") can occur inside a complete new incident.
        # Mask only these bounded copular forms: referential compact edits
        # such as "It is too long. Make it shorter" remain intact.
        reference_text = _NONREFERENTIAL_IT_COPULA.sub("", reference_text)
        # Bare "that" is commonly a complementizer ("inform parents that
        # Recycling Day...") rather than a reference to the previous case.
        # Count it as deixis only when an action actually takes "this/that"
        # as its object. This prevents two self-contained cases that merely
        # share a topic word from being fused or marked ambiguous.
        weak_reference = bool(
            _WEAK_REFERENCE.search(reference_text)
            or _ACTION_DEICTIC_REFERENCE.search(reference_text)
        )
        compact_pronoun_reference = bool(
            _COMPACT_PRONOUN_REFERENCE.search(reference_text)
        )
        generic_followup = bool(_GENERIC_FOLLOWUP.search(text or ""))
        fact_status_edit = bool(_FACT_STATUS_EDIT.search(text or ""))
        introduced_artifact_subject = bool(
            _INTRODUCED_ARTIFACT_SUBJECT.search(text or "")
        ) and not shared

        attributed_match = _ATTRIBUTED_PARENT_ANAPHORA.search(text or "")
        attributed_entity = (
            attributed_match.group("entity").casefold()
            if attributed_match
            and attributed_match.group("entity").casefold() in parent_tokens
            else ""
        )
        object_match = _DEFINITE_OBJECT_REFERENCE.search(text or "")
        object_entity = (
            object_match.group("entity").casefold()
            if object_match
            and object_match.group("entity").casefold() in parent_tokens
            else ""
        )
        novel_tokens = (
            current_tokens - set(shared) - _CONTINUITY_EVIDENCE_STOPWORDS
        )
        # A unique prior artifact *kind* is not enough to bind a newly named
        # artifact. For example, after a Recycling Day notice, "shorten the
        # Sports Day notice" names a different subject even though both files
        # are notices. A genuine "Recycling Day notice" edit still has
        # positive topic overlap, while "shorten the notice" remains a safe
        # compact reference to the unique prior file.
        novel_artifact_subject = bool(
            strong_edit
            and referenced
            and novel_tokens
            and not shared
            and not weak_reference
            and not continuation
            and not prior_artifact_exclusion
        )
        bounded_object_reference = bool(
            object_entity
            and (weak_reference or continuation)
            and (weak_reference or len(novel_tokens) <= 1)
        )
        referenced_entities = sorted({
            entity for entity in (attributed_entity, object_entity)
            if entity
        })

        if introduced_artifact_subject or novel_artifact_subject:
            validated, reason = "new_case", "new_subject_for_prior_artifact"
        elif (
            fact_status_edit
            and "report" in referenced
            and (weak_reference or strong_edit or continuation)
        ):
            validated, reason = "follow_up", "fact_status_edit_of_prior_report"
        elif (
            prior_artifact_exclusion
            and referenced
            and (additive_only or continuation or bool(shared))
        ):
            validated, reason = (
                "follow_up", "bounded_additive_update_of_prior_artifact"
            )
        elif attributed_entity:
            validated, reason = "follow_up", "attributed_pronoun_to_parent_entity"
        elif bounded_object_reference:
            validated, reason = "follow_up", "bounded_object_reference"
        elif continuation and compact_pronoun_reference and shared:
            validated, reason = "follow_up", "pronoun_continuation_with_topic_overlap"
        elif continuation and len(shared) >= 2:
            validated, reason = "follow_up", "continuation_cue_with_topic_overlap"
        elif strong_edit and referenced and shared:
            validated, reason = "follow_up", "bounded_edit_with_case_overlap"
        elif strong_edit and referenced and len(current_tokens - set(shared)) <= 4:
            validated, reason = "follow_up", "bounded_edit_of_prior_artifact"
        elif (
            strong_edit
            and weak_reference
            and len(prior_roles) == 1
            and len(current_tokens - set(shared)) <= 3
        ):
            # A compact "make it shorter" / "send it" instruction has a
            # single safe referent when the parent produced exactly one file.
            # Multiple prior files remain ambiguous rather than guessing.
            validated, reason = "follow_up", "compact_edit_of_only_prior_artifact"
        elif generic_followup:
            validated, reason = "ambiguous", "underspecified_follow_up"
        elif (
            weak_reference
            or compact_pronoun_reference
            or continuation
            or strong_edit
            or unresolved_artifact
        ):
            validated, reason = "ambiguous", "unresolved_case_reference"
        else:
            # Shared names, dates or locations are not continuity evidence by
            # themselves. A complete request opens a new case unless it refers
            # back to the parent or asks to edit one of its artifacts.
            validated, reason = "new_case", "self_contained_school_matter"

    result["case_relation"] = validated
    result["case_relation_evidence"] = {
        "proposed": proposed,
        "validated": validated,
        "reason": reason,
        "shared_topic_tokens": shared[:12],
        "referenced_artifacts": referenced,
        "referenced_case_entities": referenced_entities,
        "candidate_case_context_id": active_case_context.get("case_context_id"),
        "candidate_source_task_id": active_case_context.get("source_task_id"),
    }
    if validated != proposed:
        result["source"] = f"{result.get('source') or 'unknown'}+case_context_guard"
    return result


def resolve_case_aware_semantics(
    text: str,
    semantics: dict,
    active_case_context: dict | None,
) -> dict:
    """Recover school-domain status only from a verified prior-case referent.

    Short edits such as "make the Recycling Day notice shorter" can omit
    generic school words. A semantic classifier may therefore call them
    out-of-domain. The deterministic continuity guard gets one bounded retry
    with school-domain enabled; its result is accepted only when it proves a
    follow-up against the candidate parent. All other out-of-domain requests
    stay unrelated and inherit nothing.
    """
    resolved = resolve_case_relation(text, semantics, active_case_context)
    if semantics.get("school_domain") is True or not active_case_context:
        return resolved

    candidate = deepcopy(semantics or {})
    candidate["school_domain"] = True
    recovered = resolve_case_relation(text, candidate, active_case_context)
    if recovered.get("case_relation") != "follow_up":
        return resolved

    recovered["boundary_confirmed"] = False
    recovered["source"] = (
        f"{recovered.get('source') or 'unknown'}"
        "+verified_case_reference_domain_recovery"
    )
    evidence = dict(recovered.get("case_relation_evidence") or {})
    evidence["domain_recovered_from_verified_case_reference"] = True
    recovered["case_relation_evidence"] = evidence
    return recovered


def confirm_case_binding(
    *,
    relation: str,
    candidate_parent_task_id: str | None,
    candidate_case_context: dict | None,
    new_case_context_id: str,
) -> dict:
    """Separate a candidate parent from confirmed lineage."""
    if (
        relation == "follow_up"
        and candidate_parent_task_id
        and candidate_case_context
        and candidate_case_context.get("case_context_id")
    ):
        return {
            "parent_task_id": candidate_parent_task_id,
            "case_context_id": candidate_case_context["case_context_id"],
            "prior_case_context": candidate_case_context,
        }
    return {
        "parent_task_id": None,
        "case_context_id": new_case_context_id,
        "prior_case_context": None,
    }


def _merge_unique_strings(left: Any, right: Any) -> list[str]:
    return sorted({
        str(item).strip().lower()
        for item in [*(left or []), *(right or [])]
        if str(item).strip()
    })


def _normalise_fact_id(value: Any) -> str:
    return re.sub(
        r"[^\w]+", "_", str(value or "").strip().casefold(), flags=re.UNICODE,
    ).strip("_")


def _normalise_grounding_text(value: Any) -> str:
    return re.sub(
        r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE,
    ).strip()


_GROUNDING_NEGATIONS = {
    "no", "not", "never", "without", "none", "neither", "nobody",
    "nothing", "cannot", "can't", "didn't", "doesn't", "isn't", "wasn't",
    "weren't", "tidak", "bukan", "tiada", "belum", "jangan", "tak",
}
_GROUNDING_ARTICLES = {"a", "an", "the"}


def grounding_negation_consistent(value: Any, source: Any) -> bool:
    """Reject a fact when the matching source span has opposite polarity."""
    fact_tokens = _normalise_grounding_text(value).split()
    if not fact_tokens:
        return True
    fact_negative = any(
        token in _GROUNDING_NEGATIONS for token in fact_tokens
    )
    core = [
        token for token in fact_tokens
        if token not in _GROUNDING_NEGATIONS
        and token not in _GROUNDING_ARTICLES
    ]
    if len(core) < 2:
        return True

    scored_scopes: list[tuple[float, bool]] = []
    for raw_segment in re.split(r"[.!?;\r\n]+", str(source or "")):
        tokens = _normalise_grounding_text(raw_segment).split()
        if not tokens:
            continue
        positions: list[int] = []
        cursor = 0
        for wanted in core:
            try:
                position = tokens.index(wanted, cursor)
            except ValueError:
                positions = []
                break
            positions.append(position)
            cursor = position + 1
        if positions:
            score = 1.0
        else:
            common = set(core).intersection(tokens)
            score = len(common) / len(set(core))
            positions = [
                index for index, token in enumerate(tokens) if token in common
            ]
        if score < 0.5 or not positions:
            continue
        start = max(0, min(positions) - 3)
        end = max(positions) + 1
        source_negative = any(
            token in _GROUNDING_NEGATIONS for token in tokens[start:end]
        )
        scored_scopes.append((score, source_negative))

    if not scored_scopes:
        return True
    best = max(score for score, _negative in scored_scopes)
    return any(
        source_negative == fact_negative
        for score, source_negative in scored_scopes
        if score == best
    )


_CASE_DRAFTING_COMMAND = re.compile(
    r"(?i)(?:^|(?<=[.!?;])\s+)(?:also\s+)?(?:please\s+)?"
    r"(?:prepare|draft|write|create|produce|make|add|send|contact|publish|"
    r"help|do\s+not)\b"
)
_LEADING_COMMAND_CONTENT = re.compile(
    r"(?i):\s+(?=\S)|"
    r"\b(?:saying|stating|announcing|noting|confirming)\s+(?:that\s+)?"
)
_CHAINED_LEADING_COMMAND = re.compile(
    r"(?i)^(?:and|then)\s+(?:prepare|draft|write|create|produce|make|add|"
    r"send|contact|publish|help)\b"
)
_CONJUNCTION_ACTION_COMMAND = re.compile(
    r"(?i)\s+(?:and|then|but)\s+(?:also\s+)?(?:please\s+)?"
    r"(?:prepare|draft|write|create|produce|make|add|send|contact|publish|"
    r"help|do\s+not)\b"
)


def _reported_case_narrative(value: Any) -> str:
    """Keep reported context while excluding drafting/action instructions."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    command = _CASE_DRAFTING_COMMAND.search(text)
    if not command:
        return text
    if command.start():
        return text[:command.start()].strip()

    # A parent request may begin with an action verb but still contain the
    # only user-reported narrative (for example, "Send this message: the
    # canteen reopens Monday"). Remove the command, not the payload. Prefer
    # an explicit content delimiter; otherwise retain the non-imperative
    # remainder. This prevents an old Draft/Send/Publish instruction from
    # being fed back into a later generation turn.
    remainder = text[command.end():].strip(" \t:-")
    if not remainder:
        return ""
    delimiters = list(_LEADING_COMMAND_CONTENT.finditer(remainder))
    if delimiters:
        delimiter = delimiters[0]
        payload = remainder[delimiter.end():].strip(" \t:-")
        if payload:
            remainder = payload
    while True:
        chained = _CHAINED_LEADING_COMMAND.search(remainder)
        if not chained:
            break
        remainder = remainder[chained.end():].strip(" \t:-")
    later_command = _CASE_DRAFTING_COMMAND.search(remainder)
    conjunction_command = _CONJUNCTION_ACTION_COMMAND.search(remainder)
    command_positions = [
        item.start() for item in (later_command, conjunction_command) if item
    ]
    if command_positions:
        remainder = remainder[:min(command_positions)].strip()
    if not remainder:
        return ""
    if re.fullmatch(
        r"(?i)(?:it|this|that|the\s+(?:report|notice|post|draft|plan|message))"
        r"(?:\s+(?:now|again))?[.!]?",
        remainder,
    ):
        return ""
    return remainder


def _fact_is_grounded(value: str, current_text: str) -> bool:
    """Accept a current fact only when the user supplied its substance."""
    if not grounding_negation_consistent(value, current_text):
        return False
    fact_text = _normalise_grounding_text(value)
    source_text = _normalise_grounding_text(current_text)
    if not fact_text or not source_text:
        return False
    if fact_text in source_text:
        return True
    fact_tokens = {token for token in fact_text.split() if len(token) > 1}
    source_tokens = {token for token in source_text.split() if len(token) > 1}
    return bool(
        len(fact_tokens) >= 2
        and len(fact_tokens.intersection(source_tokens)) / len(fact_tokens) >= 0.75
    )


def _clean_fact(raw: dict) -> dict | None:
    item = deepcopy(raw)
    value = re.sub(r"\s+", " ", str(item.get("value") or "")).strip()
    if not value:
        return None
    item["value"] = value
    status = str(item.get("status") or "reported").lower()
    if status not in {"reported", "confirmed", "unverified", "unknown"}:
        status = "reported"
    item["status"] = status
    return item


def _fact_key(item: dict) -> tuple[str, str]:
    fact_id = _normalise_fact_id(item.get("fact_id"))
    value = _normalise_grounding_text(item.get("value"))
    return ("id", fact_id) if fact_id else ("value", value)


def _merge_facts(
    parent: list,
    current: list,
    context: dict,
    *,
    current_text: str,
) -> list[dict]:
    merged: list[dict] = []
    index: dict[tuple[str, str], int] = {}
    value_index: dict[str, int] = {}

    for raw in parent or []:
        if not isinstance(raw, dict):
            continue
        item = _clean_fact(raw)
        if item is None:
            continue
        item.setdefault("source_type", "prior_case_grounded")
        if context.get("source_task_id"):
            item.setdefault("source_task_id", context.get("source_task_id"))
        if context.get("case_context_id"):
            item.setdefault(
                "source_case_context_id", context.get("case_context_id"),
            )
        key = _fact_key(item)
        value_key = _normalise_grounding_text(item["value"])
        if key in index or value_key in value_index:
            continue
        index[key] = len(merged)
        value_index[value_key] = len(merged)
        merged.append(item)

    for raw in current or []:
        if not isinstance(raw, dict):
            continue
        item = _clean_fact(raw)
        if item is None or not _fact_is_grounded(item["value"], current_text):
            continue
        for field in list(item):
            if field.startswith("source_"):
                item.pop(field, None)
        item["source_type"] = "current_request_grounded"
        key = _fact_key(item)
        value_key = _normalise_grounding_text(item["value"])
        existing_position = index.get(key)
        if existing_position is None:
            existing_position = value_index.get(value_key)
        if existing_position is not None:
            existing = merged[existing_position]
            # Repeating the same value cannot silently upgrade a prior status.
            if _normalise_grounding_text(existing.get("value")) == value_key:
                continue
            old_value_key = _normalise_grounding_text(existing.get("value"))
            value_index.pop(old_value_key, None)
            merged[existing_position] = item
            index[key] = existing_position
            value_index[value_key] = existing_position
            continue
        index[key] = len(merged)
        value_index[value_key] = len(merged)
        merged.append(item)
    return merged[:40]


def _resolve_generic_report_role(situation: dict, context: dict) -> None:
    outputs = situation.get("requested_outputs") or []
    report_roles = {
        str(item.get("artifact_role") or "")
        for item in (context.get("deliverables") or [])
        if isinstance(item, dict)
        and _artifact_kind(str(item.get("artifact_role") or "")) == "report"
    }
    if len(report_roles) != 1:
        return
    prior_role = next(iter(report_roles))
    for output in outputs:
        if not isinstance(output, dict):
            continue
        role = str(output.get("artifact_role") or "")
        label = " ".join((
            str(output.get("label") or ""), str(output.get("purpose") or ""),
        ))
        if role == "school_document" and _ARTIFACT_TERMS["report"].search(label):
            output["artifact_role"] = prior_role


def _artifact_reference_is_negated(text: str, pattern: re.Pattern) -> bool:
    """Return true when a mentioned artifact is explicitly excluded now."""
    value = str(text or "")
    for match in pattern.finditer(value):
        prefix = value[max(0, match.start() - 90):match.start()]
        if re.search(
            r"\b(?:do\s+not|don't|never)\s+(?:repeat|recreate|revise|update|"
            r"include|prepare|draft|write|produce|generate)\b[^.!?;\n]{0,60}$|"
            r"\b(?:without|exclude|omit)\b[^.!?;\n]{0,45}$",
            prefix,
            re.IGNORECASE,
        ):
            return True
    return False


def _restore_referenced_artifact_outputs(
    situation: dict,
    context: dict,
    *,
    current_text: str,
) -> None:
    """Carry only the prior file contract for a confirmed, unique edit target."""
    outputs = situation.setdefault("requested_outputs", [])
    existing_roles = {
        str(item.get("artifact_role") or "")
        for item in outputs
        if isinstance(item, dict)
    }
    existing_kinds = {
        kind
        for item in outputs
        if isinstance(item, dict)
        for kind, pattern in _ARTIFACT_TERMS.items()
        if (
            _artifact_kind(str(item.get("artifact_role") or "")) == kind
            or pattern.search(" ".join((
                str(item.get("label") or ""),
                str(item.get("purpose") or ""),
            )))
        )
    }
    requested_kinds = {
        kind for kind, pattern in _ARTIFACT_TERMS.items()
        if pattern.search(current_text or "")
        and not _artifact_reference_is_negated(current_text, pattern)
        and kind not in existing_kinds
    }
    prior = [
        item for item in (context.get("deliverables") or [])
        if isinstance(item, dict) and item.get("kind", "artifact") == "artifact"
    ]
    for kind in sorted(requested_kinds):
        matches = [
            item for item in prior
            if _artifact_kind(str(item.get("artifact_role") or "")) == kind
        ]
        if len(matches) != 1:
            continue
        item = matches[0]
        role = str(item.get("artifact_role") or "")
        if not role or role in existing_roles:
            continue
        output = {
            "artifact_role": role,
            "label": str(item.get("label") or "")[:160],
            "audience": str(item.get("audience") or "")[:40],
            "recipient_type": str(item.get("recipient_type") or "")[:80],
            "channel": str(item.get("channel") or "")[:40],
            "languages": [
                str(language).strip().lower()
                for language in (item.get("requested_languages") or [])
                if str(language).strip().lower() in {"en", "ms", "zh"}
            ],
            "source_named": True,
            "purpose": "Revise the uniquely referenced prior case artifact.",
        }
        outputs.append({
            key: value for key, value in output.items()
            if value is not None and value != "" and value != []
        })
        existing_roles.add(role)


def merge_followup_situation(
    current_situation: dict,
    active_case_context: dict,
    *,
    current_text: str,
) -> dict:
    """Merge a confirmed follow-up without upgrading or re-extracting facts."""
    current = deepcopy(current_situation or {})
    parent = deepcopy((active_case_context or {}).get("situation") or {})
    if not parent:
        return current

    parent_family = str(parent.get("family") or "").strip().lower()
    current_family = str(current.get("family") or "").strip().lower()
    secondaries = _merge_unique_strings(
        parent.get("secondary_families"), current.get("secondary_families"),
    )
    if current_family and parent_family and current_family != parent_family:
        secondaries.append(current_family)
    if parent_family:
        current["family"] = parent_family
    current["secondary_families"] = sorted({
        item for item in secondaries if item and item != current.get("family")
    })

    severity_rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    parent_severity = str(parent.get("severity") or "unknown").lower()
    current_severity = str(current.get("severity") or "unknown").lower()
    current["severity"] = max(
        (parent_severity, current_severity),
        key=lambda item: severity_rank.get(item, 0),
    )
    if str(current.get("phase") or "") in {"", "unknown", "follow_up"}:
        current["phase"] = parent.get("phase") or "follow_up"

    for field in ("signals", "affected_people_types", "stakeholder_candidates"):
        current[field] = _merge_unique_strings(parent.get(field), current.get(field))
    current["known_facts"] = _merge_facts(
        parent.get("known_facts") or [],
        current.get("known_facts") or [],
        active_case_context,
        current_text=current_text,
    )
    known_fact_ids = {
        _normalise_fact_id(item.get("fact_id"))
        for item in current["known_facts"]
        if isinstance(item, dict) and _normalise_fact_id(item.get("fact_id"))
    }
    unknowns: dict[str, dict] = {}
    for item in [
        *(parent.get("unknowns") or []),
        *(current.get("unknowns") or []),
    ]:
        if not isinstance(item, dict):
            continue
        fact_id = _normalise_fact_id(item.get("fact_id"))
        if not fact_id or fact_id in known_fact_ids or fact_id in unknowns:
            continue
        unknowns[fact_id] = {
            "fact_id": fact_id,
            "impact": str(item.get("impact") or "content_only"),
        }
    current["unknowns"] = [unknowns[key] for key in sorted(unknowns)]

    _resolve_generic_report_role(current, active_case_context)
    _restore_referenced_artifact_outputs(
        current,
        active_case_context,
        current_text=current_text,
    )
    parent_summary = str(parent.get("case_summary") or "").strip()
    instruction = str(current_text or "").strip()
    parent_case_narrative = (
        _reported_case_narrative(parent_summary)
        or _reported_case_narrative(active_case_context.get("raw_goal"))
    )
    current_case_update = _reported_case_narrative(instruction)
    summary_parts = [parent_case_narrative] if parent_case_narrative else []
    if current_case_update:
        summary_parts.append(f"Follow-up: {current_case_update}")
    current["case_summary"] = (
        " ".join(summary_parts)
        or parent_summary
        or instruction
    )[:1000]
    parent_narrative = (
        _reported_case_narrative(active_case_context.get("raw_goal"))
        or _reported_case_narrative(parent.get("source_request"))
        or parent_case_narrative
    )
    evidence_lines = [
        f"- {item.get('value')} ({item.get('status', 'reported')})"
        for item in current["known_facts"]
        if item.get("value")
    ]
    current["source_request"] = (
        "Prior user-reported case narrative "
        "(not independently verified by this system):\n"
        f"{parent_narrative or 'TBC'}\n\n"
        f"Current follow-up instruction:\n{instruction}\n\n"
        "Prior case facts with preserved source status:\n"
        + (
            "\n".join(evidence_lines)
            if evidence_lines
            else (
                "- No structured facts were extracted; use only the prior "
                "user-reported narrative above."
            )
        )
    )[:8000]
    current["case_context_id"] = active_case_context.get("case_context_id")
    current["case_source_task_ids"] = sorted({
        str(item).strip()
        for item in [
            *(active_case_context.get("source_task_ids") or []),
            *(parent.get("case_source_task_ids") or []),
            active_case_context.get("source_task_id"),
        ]
        if str(item or "").strip()
    })
    current["context_merge"] = "confirmed_follow_up"
    return current
