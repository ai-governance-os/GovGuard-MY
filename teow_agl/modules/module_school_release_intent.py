"""Deterministic parsing for explicit school external-release intent.

The semantic LLM remains the primary natural-language interpreter. These
helpers are a narrow authorisation backstop shared by the situation compiler
and Markdown-plan normaliser, so no explicit send/call/publish can lose its
human gate merely because one model field was omitted.

This parser deliberately separates *creating communication content* from
*releasing that content*.  "Draft a circular to distribute to parents" is a
draft request.  "Draft the circular and send it to parents" contains a real
release instruction and therefore needs an approval gate.
"""
from __future__ import annotations

import re
from typing import Iterable


# Keep the public collection compatible with existing callers.  Pattern
# matching below uses ordered tuples so classification does not depend on set
# or dict iteration order.
EXTERNAL_RECIPIENTS = {
    "guardian", "medical_services", "malaysia_emergency_services_999",
    "fire_and_rescue", "police", "education_authority", "local_authority",
    "event_organizer", "vendor", "transport_provider", "public_media",
    "school_community", "external_stakeholder",
}

_RECIPIENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "education_authority",
        r"\b(?:pejabat\s+pendidikan(?:\s+daerah)?|"
        r"kementerian\s+pendidikan)\b",
    ),
    (
        "school_community",
        r"\b(?:all\s+(?:school\s+)?parents|parents\s+group|parent\s+group|school\s+community|"
        r"all\s+staff|whole\s+school|(?:year\s+\d+\s+)?class\s+"
        r"(?:whatsapp\s+)?group)\b|"
        r"全体家长|全體家長|家长群|家長群|semua\s+ibu\s+bapa",
    ),
    (
        "education_authority",
        r"\b(?:district\s+education\s+office|education\s+authority|"
        r"education\s+office|district\s+office|ministry\s+(?:portal|of\s+education)|"
        r"ppd|jpn|moe)\b|教育局|教育部",
    ),
    (
        "malaysia_emergency_services_999",
        r"\b(?:999|emergency\s+services?)\b",
    ),
    (
        "fire_and_rescue",
        r"\b(?:fire\s+and\s+rescue|fire\s+department|bomba)\b|消防",
    ),
    ("police", r"\bpolice\b|警方|警察|polis"),
    (
        "medical_services",
        r"\b(?:hospital|medical\s+services?|clinic|doctor)\b|医院|醫院|诊所|診所",
    ),
    (
        "local_authority",
        r"\b(?:local\s+authority|municipal(?:ity)?|council)\b|"
        r"地方政府|市议会|市議會",
    ),
    (
        "event_organizer",
        r"\b(?:event\s+organizer|event\s+organiser|organizer|organiser|guest)\b",
    ),
    (
        "vendor",
        r"\b(?:vendor|supplier|contractor)\b|供应商|供應商|pembekal",
    ),
    (
        "transport_provider",
        r"\b(?:transport\s+provider|bus\s+operator|bus\s+company)\b",
    ),
    (
        "public_media",
        r"\b(?:facebook|public|publicly|media|website|social\s+media)\b|"
        r"公开|公開|脸书|臉書",
    ),
    (
        "guardian",
        r"\b(?:parents?|guardians?|family)\b|家长|家長|监护人|監護人|"
        r"ibu\s+bapa|penjaga",
    ),
)

_RECIPIENT_WORDS = (
    r"all\s+(?:school\s+)?parents|parents?|guardians?|family|all\s+staff|school\s+community|"
    r"(?:year\s+\d+\s+)?class\s+(?:whatsapp\s+)?group|"
    r"semua\s+ibu\s+bapa|ibu\s+bapa|penjaga|"
    r"district\s+education\s+office|education\s+authority|education\s+office|"
    r"district\s+office|ministry(?:\s+of\s+education)?|ppd|jpn|moe|"
    r"pejabat\s+pendidikan(?:\s+daerah)?|kementerian\s+pendidikan|"
    r"hospital|medical\s+services?|clinic|doctor|emergency\s+services?|999|"
    r"fire\s+and\s+rescue|fire\s+department|bomba|police|local\s+authority|"
    r"municipal(?:ity)?|council|event\s+organizer|event\s+organiser|organizer|"
    r"organiser|guest|vendor|supplier|contractor|transport\s+provider|"
    r"bus\s+operator|bus\s+company|public|media|facebook|website|anyone|anybody"
)
_RECIPIENT_REFERENCE = (
    rf"(?:the\s+)?(?:[a-z][a-z'-]{{0,40}}'s\s+)?(?:{_RECIPIENT_WORDS})\b"
)

# Verbs that, when used as actions, unambiguously cross the internal/external
# boundary.  The more ambiguous communication verbs are handled separately
# and require either a recipient or a release object.
_DIRECT_RELEASE_VERB = (
    r"send|publish|submit|release|upload|hantar|menghantar|"
    r"serahkan|kemukakan|mengemukakan|siarkan"
)
_RELEASE_OBJECT = (
    r"(?:(?:this|it|them|a|an|the)\s+)?(?:message|report|notice|letter|document|file|"
    r"email|draft|circular|announcement|request|form|application|results?|"
    r"evidence|information|records?|content|mesej|laporan|notis|surat|dokumen|"
    r"fail|pengumuman)\b"
)

_DIRECT_ACTION_PATTERN = re.compile(
    rf"\b(?:{_DIRECT_RELEASE_VERB})\b|"
    rf"(?:^|\band(?:\s+then)?\s+)(?:please\s+)?post(?!-)\b|"
    rf"\b(?:share|forward)\s+{_RELEASE_OBJECT}(?:\s+(?:to|with)\s+{_RECIPIENT_REFERENCE})?|"
    rf"\b(?:share|forward)\b[^.!?;\n]{{1,140}}\b(?:to|with)\s+"
    rf"{_RECIPIENT_REFERENCE}|"
    rf"\b(?:share|forward)\s+(?:to|with)\s+{_RECIPIENT_REFERENCE}|"
    rf"\bemail\s+(?!(?:address|details?|account)\b)(?:{_RECIPIENT_REFERENCE}|{_RELEASE_OBJECT})|"
    rf"\b(?:email|whatsapp|text|sms|dm)\s+(?:it|this|that|them)"
    rf"\s+(?:to\s+)?{_RECIPIENT_REFERENCE}|"
    rf"\b(?:notify|contact|call|message|inform|tell)\s+{_RECIPIENT_REFERENCE}|"
    rf"\b(?:announce|issue)\s+(?:{_RELEASE_OBJECT}\s+)?(?:to\s+)?{_RECIPIENT_REFERENCE}|"
    rf"\b(?:reply|respond)\s+to\s+{_RECIPIENT_REFERENCE}|"
    rf"\b(?:whatsapp|text|sms|dm)\s+{_RECIPIENT_REFERENCE}|"
    rf"\b(?:distribute|circulate|broadcast)\s+{_RELEASE_OBJECT}(?:\s+(?:to|with)\s+{_RECIPIENT_REFERENCE})?|"
    rf"\bdeliver\s+{_RELEASE_OBJECT}\s+to\s+{_RECIPIENT_REFERENCE}|"
    rf"\brequest\b[^.!?;\n]{{0,100}}\b(?:from|to)\s+{_RECIPIENT_REFERENCE}|"
    rf"\bemel\s+(?:{_RECIPIENT_REFERENCE}|{_RELEASE_OBJECT})|"
    rf"\b(?:hubungi|maklumkan|beritahu)\s+(?:kepada\s+)?(?:pihak\s+)?{_RECIPIENT_REFERENCE}|"
    r"\bmessage\s+(?:the\s+)?(?:parents?|guardians?|family|office|authority|"
    r"hospital|police|organizer|organiser|vendor)\b|"
    r"发送|發送|发给|發給|寄给|寄給|发布|發佈|提交|联系|聯絡|通知|转发|轉發|上传|上傳|分享",
)

_NEGATION_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|never|not\s+to|must\s+not|should\s+not|"
    r"cannot|can't|no\s+one\s+should)\b|"
    r"\b(?:draft\s+only|nothing\s+should\s+be\s+sent|not\s+sent|"
    r"without\s+sending|without\s+publishing)\b|"
    r"\b(?:jangan|tidak\s+boleh)\b|"
    r"不要|不得|禁止|只(?:做|要)草稿"
)

# Negative clauses also need to recognise passive "sent" forms.  Passive
# status is never treated as a positive instruction.
_NEGATED_PASSIVE_RELEASE_ACTION = re.compile(
    r"\b(?:sent|sending|published|publishing|posted|posting|shared|sharing|"
    r"uploaded|uploading|forwarded|forwarding|submitted|submitting|released|"
    r"releasing)\b"
)

_NEGATED_RELEASE_OBJECT = re.compile(
    r"\b(?:send|publish|submit|release|upload|post|share|forward|"
    r"contact|notify|call|message|email|whatsapp|text)\s+"
    r"(?:absolutely\s+)?(?:nothing|nobody|no\s+one|no\s+messages?|"
    r"no\s+notices?|no\s+reports?|no\s+documents?)\b|"
    r"\b(?:hantar|siarkan|serahkan|hubungi|maklumkan)\s+"
    r"(?:tiada\s+apa-apa|tiada\s+sesiapa|jangan\s+apa-apa)\b",
)

_DRAFT_LEAD = re.compile(
    r"^(?:please\s+)?(?:draft|prepare|write|create|compose|generate)\b"
)

# A readiness instruction creates content; it does not authorise its release.
# Without this mask, phrases such as ``make it ready to publish`` are mistaken
# for an imperative ``publish`` merely because both contain the same verb.
_READINESS_ONLY_RELEASE = re.compile(
    r"\b(?:make|keep|format|finali[sz]e|prepare)\b"
    r"[^.!?;\n]{0,70}\b(?:ready|suitable|fit)\b\s+"
    r"(?:for\s+)?(?:immediate\s+)?(?:publication|submission|release)\b|"
    r"\b(?:make|keep|format|finali[sz]e|prepare)\b"
    r"[^.!?;\n]{0,70}\b(?:ready|prepared|formatted|suitable)\b\s+to\s+"
    r"(?:send|publish|submit|release|upload|post|share|forward)\b|"
    r"\b(?:ready|prepared|formatted|suitable)\b\s+to\s+"
    r"(?:send|publish|submit|release|upload|post|share|forward)\b"
)

_INTERNAL_SCHOOL_REPOSITORY_TARGET = re.compile(
    r"\b(?:school\s+staff\s+guide|staff\s+(?:guide|handbook|manual)|"
    r"school\s+(?:handbook|manual|intranet|shared\s+drive)|"
    r"internal\s+(?:staff\s+)?(?:portal|intranet|knowledge\s+base|wiki)|"
    r"staff\s+(?:portal|intranet|knowledge\s+base|wiki))\b|"
    r"校内(?:手册|指南|内联网|知识库)|校內(?:手冊|指南|內聯網|知識庫)|"
    r"panduan\s+kakitangan\s+sekolah|portal\s+dalaman\s+sekolah",
)
_INTERNAL_REPOSITORY_WRITE_ACTION = re.compile(
    r"\b(?:upload|publish|post|add|insert|save|put)\b|"
    r"上传|上傳|发布|發佈|加入|保存|"
    r"\b(?:muat\s+naik|terbitkan|simpan)\b",
)


def _split_clauses(text: str) -> list[str]:
    """Return ordered, stable clauses without losing ``and send`` intent."""

    value = (text or "").casefold()
    value = re.sub(
        r",\s*(?=(?:just|only|rather\s+than)\b)",
        ". ",
        value,
    )
    return [
        chunk.strip(" ,:")
        for chunk in re.split(
            r"[.!?;；。！？\n]+|\b(?:but|however|instead|then|tetapi)\b|"
            r"(?:但是|然而|而是|然后|然後|但)",
            value,
        )
        if chunk.strip(" ,:")
    ]


def _execution_tail_from_draft(chunk: str) -> str:
    """Extract an independently commanded release after a draft request.

    Infinitives such as ``a circular to distribute`` describe the requested
    content and are intentionally ignored.  Coordination such as ``and send``
    or ``, notify`` is an additional instruction and is retained.
    """

    if not _DRAFT_LEAD.match(chunk):
        return chunk
    # A draft-only instruction may carry its negation in the same clause:
    # "Prepare the report without sending it" or "Draft it and do not send".
    # Preserve that explicit boundary instead of discarding the whole clause.
    negation = _NEGATION_PATTERN.search(chunk)
    if negation and (
        _DIRECT_ACTION_PATTERN.search(chunk[negation.start():])
        or _NEGATED_PASSIVE_RELEASE_ACTION.search(chunk[negation.start():])
    ):
        return chunk[negation.start():]
    connector = re.search(
        rf"(?:,\s*|\band(?:\s+then)?\s+)(?:please\s+)?"
        rf"(?=(?:{_DIRECT_RELEASE_VERB})\b|post\b|email\b|notify\b|contact\b|call\b|"
        rf"share\b|forward\b|message\b|inform\b|tell\b|announce\b|issue\b|reply\b|respond\b|"
        rf"whatsapp\b|text\b|sms\b|dm\b|distribute\b|circulate\b|broadcast\b|"
        rf"deliver\b|request\b|emel\b|hubungi\b|maklumkan\b|"
        rf"发送|發送|发给|發給|寄给|寄給|发布|發佈|提交|通知)",
        chunk,
    )
    return chunk[connector.end():] if connector else ""


def _internal_repository_clause(value: str) -> bool:
    """Return true for an internal school repository write, not a release.

    A school staff guide or intranet is a persistent system destination.  It
    needs its own governed system-action gate, but it must not be converted
    into an unknown external recipient merely because the verb is ``upload``.
    Explicit public/parent/agency destinations still win and remain releases.
    """

    if not (
        _INTERNAL_SCHOOL_REPOSITORY_TARGET.search(value)
        and _INTERNAL_REPOSITORY_WRITE_ACTION.search(value)
    ):
        return False
    return not any(
        re.search(pattern, value)
        for recipient, pattern in _RECIPIENT_PATTERNS
        if recipient != "school_community"
    )


def internal_school_repository_write_requested(text: str) -> bool:
    """Detect an unnegated write to a named internal school repository."""

    for original_chunk in _split_clauses(text):
        chunk = _execution_tail_from_draft(original_chunk)
        if not chunk or not _internal_repository_clause(chunk):
            continue
        action = _INTERNAL_REPOSITORY_WRITE_ACTION.search(chunk)
        if action is None:
            continue
        prefix = chunk[max(0, action.start() - 64):action.start()]
        if _NEGATION_PATTERN.search(prefix):
            continue
        return True
    return False


def _is_conditional_approval(chunk: str) -> bool:
    """Treat a future release that is conditional on approval as gated."""

    return bool(
        re.search(
            r"\b(?:request|seek|obtain|get)\s+(?:human\s+)?"
            r"(?:approval|authorisation|authorization|review)\s+to\s+"
            r"(?:send|email|publish|post|submit|release|share|forward|"
            r"notify|contact|call|whatsapp)\b",
            chunk,
        )
        or
        re.search(
            r"\b(?:without|until|unless|only\s+after)\b[^.!?;]{0,80}"
            r"\b(?:approval|approve(?:d|s)?|authorisation|authorization|review)\b",
            chunk,
        )
        or re.search(
            r"\b(?:(?:request|require|obtain|seek|get|need)\b[^.!?;]{0,70}"
            r"|(?:approval|authorisation|authorization|review)\s+is\s+required[^.!?;]{0,40})"
            r"\b(?:approval|authorisation|authorization|review)?\b[^.!?;]{0,50}"
            r"\bbefore\b[^.!?;]{0,80}"
            r"\b(?:sent|send|published|publish|posted|post|submitted|submit|"
            r"released|release|shared|share|forwarded|forward|contacted|contact|"
            r"notified|notify|called|call|emailed|email|whatsapped|whatsapp)\b",
            chunk,
        )
    )

def release_clauses(text: str) -> tuple[list[str], list[str]]:
    """Classify ordered clauses as positive or negated release intent."""

    positive: list[str] = []
    negative: list[str] = []
    for original_chunk in _split_clauses(text):
        chunk = _execution_tail_from_draft(original_chunk)
        if not chunk:
            continue
        # Remove draft-readiness language before testing for an execution
        # verb. A second, independently commanded release in the same clause
        # remains visible after the narrow phrase is removed.
        action_chunk = _READINESS_ONLY_RELEASE.sub("", chunk)
        # Internal repository writes are governed as system-level changes by
        # the Situation Compiler, not invented as external stakeholders here.
        if _internal_repository_clause(action_chunk):
            continue
        if _NEGATED_RELEASE_OBJECT.search(action_chunk):
            negative.append(original_chunk)
            continue

        # An explicit approval-before-send instruction is a future external
        # action with a gate even when the release verb is passive.
        if _is_conditional_approval(action_chunk):
            positive.append(original_chunk)
            continue
        negated = bool(_NEGATION_PATTERN.search(action_chunk))
        if negated and (
            _DIRECT_ACTION_PATTERN.search(action_chunk)
            or _NEGATED_PASSIVE_RELEASE_ACTION.search(action_chunk)
        ):
            if _is_conditional_approval(action_chunk):
                positive.append(original_chunk)
            else:
                negative.append(original_chunk)
            continue

        if _DIRECT_ACTION_PATTERN.search(action_chunk):
            positive.append(original_chunk)
    return positive, negative


def recipients_in_release_text(value: str) -> set[str]:
    """Map release text to canonical external recipients."""

    recipients: set[str] = set()
    for recipient, pattern in _RECIPIENT_PATTERNS:
        if re.search(pattern, value):
            recipients.add(recipient)

    if re.search(r"\b(?:publish|post)\b|发布|發佈", value):
        recipients.add("public_media")

    # "all parents" is one broad community recipient, not both the broad
    # group and an invented one-family private recipient.
    if "school_community" in recipients:
        recipients.discard("guardian")

    # A public publication destination controls the release route even when
    # the text being published happens to mention parents.
    if "public_media" in recipients and re.search(
        r"\b(?:publish|post|upload)\b|发布|發佈|上传|上傳", value
    ):
        recipients.discard("guardian")
        recipients.discard("school_community")
    return recipients


def _named_recipients_in_text(value: str) -> set[str]:
    """Return destinations named in text, without action-based inference."""

    recipients: set[str] = set()
    for recipient, pattern in _RECIPIENT_PATTERNS:
        if re.search(pattern, value):
            recipients.add(recipient)
    if "school_community" in recipients:
        recipients.discard("guardian")
    return recipients


def release_is_globally_negated(text: str) -> bool:
    """Return true only when release is negated and no positive route remains."""

    positive, negative = release_clauses(text)
    return bool(negative and not positive)


def negated_external_recipients(text: str) -> set[str]:
    """Return specifically negated routes, or all routes for global negation."""

    _positive, negative = release_clauses(text)
    recipients: set[str] = set()
    for chunk in negative:
        named = _named_recipients_in_text(chunk)
        if named:
            recipients.update(named)
            continue
        if re.search(
            r"\b(?:anything|anyone|anybody|nothing|no\s+one)\b|"
            r"任何(?:人|内容|東西|东西)",
            chunk,
        ):
            recipients.update(EXTERNAL_RECIPIENTS)
        else:
            recipients.update(
                recipients_in_release_text(chunk) or {"external_stakeholder"}
            )
    return recipients


def infer_explicit_external_recipients(
    text: str,
    *,
    requested_audience: str = "unknown",
    requested_outputs: Iterable[dict] = (),
) -> set[str]:
    """Infer canonical recipients only from positive release clauses."""

    positive, _negative = release_clauses(text)
    if not positive:
        return set()
    recipients: set[str] = set()
    for chunk in positive:
        recipients.update(recipients_in_release_text(chunk))
    if not recipients:
        # Sort model-provided data before inspection so equivalent semantic
        # payloads cannot produce order-dependent fallback behaviour.
        output_rows = sorted(
            (output for output in requested_outputs if isinstance(output, dict)),
            key=lambda output: (
                str(output.get("recipient_type") or "").strip().lower(),
                str(output.get("role") or output.get("artifact_role") or ""),
            ),
        )
        for output in output_rows:
            recipient = str(output.get("recipient_type") or "").strip().lower()
            if recipient in EXTERNAL_RECIPIENTS:
                recipients.add(recipient)
    audience = requested_audience.casefold()
    if not recipients and audience == "public":
        recipients.add("public_media")
    elif not recipients and audience == "school_community":
        recipients.add("school_community")
    elif not recipients:
        recipients.add("external_stakeholder")
    if "school_community" in recipients:
        # "All parents" is one community release, not a second private-family
        # release merely because a semantic output used the guardian role.
        recipients.discard("guardian")
    return recipients


def requests_external_release(semantics: dict, user_intent: str) -> bool:
    """Resolve explicit text first; use semantic actions only when not negated."""

    positive, _negative = release_clauses(user_intent)
    if positive:
        return True
    if release_is_globally_negated(user_intent):
        return False
    situation = semantics.get("situation") or {}
    return bool(situation.get("explicit_external_actions"))
