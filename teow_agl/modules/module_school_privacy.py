"""Shared deterministic privacy checks for broad school communications.

The semantic model may identify a privacy concern, but a missing model field
must never make a community/public draft disclose an individual pupil's
result, health, discipline, welfare or family-status information.  These
helpers are intentionally conservative and are used by compilation,
generation fallback and post-generation verification.
"""
from __future__ import annotations

import re
from typing import Iterable


_BROAD_ROLES = {"school_parent_notice", "public_communication_draft"}
_BROAD_AUDIENCES = {"school_community", "public"}
_RESTRICTED_CONCEPTS = {
    "public_pii", "health_or_discipline", "student_sensitive_data",
    "individual_marks", "individual_weakness_reasons", "socioeconomic_data",
}

_AGGREGATE = re.compile(
    r"\b(?:class|cohort|school|grade|year)\s+(?:average|aggregate|overall)\b|"
    r"\b(?:anonymous|anonymised|anonymized|aggregate)\s+"
    r"(?:results?|marks?|scores?|grades?|grade\s+distribution|data|pass\s+rate)\b|"
    r"\boverall\s+(?:pass\s+rate|grade\s+distribution|results?)\b|"
    r"\b(?:school|programme|program|initiative|project)\b[^.!?;]{0,120}"
    r"\b(?:student|pupil)s?\s+behavio(?:u)?r\b[^.!?;]{0,80}"
    r"\b(?:by\s+)?\d+(?:\.\d+)?\s*%|"
    r"\bnot\s+(?:student\s+names?|individual\s+(?:marks?|scores?|results?))\b|"
    r"\b(?:purata|agregat|keseluruhan)\s+(?:kelas|kohort|sekolah|tahun)\b|"
    r"班级平均|班級平均|整体平均|整體平均|汇总数据|彙總數據",
    re.IGNORECASE,
)
_SENSITIVE = re.compile(
    r"\b(?:marks?|scores?|grades?|weak(?:ness|nesses)?|attendance|"
    r"disciplin(?:e|ary)|behavio(?:u)?r|injur(?:y|ed|ies)|diagnos(?:is|ed)|"
    r"medical|medicat(?:e|ed|ion|ions)?|medicines?|prescriptions?|"
    r"psycholog(?:y|ical|ically)?|psychiatr(?:y|ic)?|therap(?:y|ist|ies)|"
    r"counsell?(?:ing|or|ors)?|mental\s+health|"
    r"adhd|autis(?:m|tic)|special\s+needs?|family\s+background|"
    r"household\s+income|socioeconomic|financial\s+status|poor\s+family|"
    r"low[- ]income\s+(?:family|household)|needs?\s+financial\s+aid|"
    r"medicat(?:e|ed|ion|ions)?|medicines?|prescriptions?|"
    r"psycholog(?:y|ical|ically)?|psychiatr(?:y|ic)?|therap(?:y|ist|ies)|"
    r"counsell?(?:ing|or|ors)?|mental\s+health|"
    r"welfare|public\s+housing|ppr|"
    r"bully(?:ing|ied)|"
    r"ic\s+(?:number|no\.? )?|mykid|passport|phone\s+(?:number|no\.? )?|"
    r"home\s+address|email\s+address|contact\s+(?:number|details?))\b|"
    r"\b(?:markah|gagal|lemah|kehadiran|disiplin|tingkah\s+laku|cedera|"
    r"diagnosis|kesihatan|latar\s+belakang\s+keluarga|pendapatan|"
    r"nombor\s+(?:kad\s+pengenalan|mykid|telefon)|alamat\s+rumah)\b|"
    r"成绩|成績|分数|分數|\d+\s*分|不及格|弱点|弱點|出席|纪律|紀律|行为|行為|"
    r"伤势|傷勢|诊断|診斷|医疗|醫療|家庭背景|家庭收入|霸凌|"
    r"身份证|身份證|护照|護照|电话号码|電話號碼|住址|家庭地址",
    re.IGNORECASE,
)
_ACADEMIC_FAILURE = re.compile(
    r"\b(?:student|pupil|child|learner|murid|pelajar)\b"
    r"[^.!?;\n]{0,100}\b(?:fail(?:ed|s|ing)?|gagal)\b|"
    r"\b(?:fail(?:ed|s|ing)?|gagal)\b[^.!?;\n]{0,45}\b"
    r"(?:bm|bi|bahasa\s+(?:melayu|inggeris)|mathematics?|maths?|"
    r"english|malay|science|history|geography|mandarin|exam(?:ination)?|"
    r"paper|marks?|scores?|grades?)\b|"
    r"\b(?:bm|bi|bahasa\s+(?:melayu|inggeris)|mathematics?|maths?|"
    r"english|malay|science|history|geography|mandarin|exam(?:ination)?|"
    r"paper|marks?|scores?|grades?)\b[^.!?;\n]{0,45}\b"
    r"(?:fail(?:ed|s|ing)?|gagal)\b",
    re.IGNORECASE,
)
_NAMED_ACADEMIC_FAILURE = re.compile(
    r"\b(?:student|pupil|child|learner|murid|pelajar)"
    r"(?:\s+named)?\s+(?P<student_name>[A-Z][A-Za-z'’-]{1,40})\b"
    r"[^.!?;\n]{0,80}\b(?i:fail(?:ed|s|ing)?|gagal)\b|"
    r"\b(?P<name>[A-Z][A-Za-z'’-]{1,40})\b[^.!?;\n]{0,55}\b"
    r"(?i:fail(?:ed|s|ing)?|gagal)\b[^.!?;\n]{0,40}\b"
    r"(?i:bm|bi|bahasa\s+(?:melayu|inggeris)|mathematics?|maths?|"
    r"english|malay|science|history|geography|mandarin|exam(?:ination)?|"
    r"paper|marks?|scores?|grades?)\b",
)
_EXPLICIT_NAMED_STUDENT_FAILURE = re.compile(
    r"(?i:\b(?:student|pupil|child|learner|murid|pelajar)"
    r"(?:\s+named)?\s+)(?P<name>[A-Z][A-Za-z'’-]{1,40})\b"
    r"[^.!?;\n]{0,80}\b(?i:fail(?:ed|s|ing)?|gagal)\b",
)
_NON_PERSON_NAME_TOKENS = {
    "a", "an", "the", "this", "that", "these", "those",
    "tell", "send", "post", "share", "publish", "inform", "notify",
    "show", "list", "prepare", "draft", "write", "create", "sediakan",
    "memo", "notice", "report", "hantar", "maklumkan", "school", "parent",
    "student", "year", "frozen", "food", "canteen", "freezer",
    "fridge", "refrigerator", "server", "system", "device", "alarm",
    "pump", "vehicle", "bus", "van", "roof", "door", "gate",
    "camera", "network", "internet", "power", "electricity",
}
_IDENTITY_CUE = re.compile(
    r"\b(?:full\s+names?|student(?:s)?['’]?\s+names?|named|naming|"
    r"each\s+student)\b|"
    r"\b(?:list(?:ed|ing)?|include(?:d|s|ing)?|show(?:n|ing)?)\b"
    r"[^.!?;\n]{0,35}\b(?:student(?:s)?|pupil(?:s)?|names?)\b|"
    r"\b(?:nama|senarai\s+nama|setiap\s+murid|setiap\s+pelajar)\b|"
    r"学生姓名|學生姓名|全名|列出学生|列出學生|每个学生|每個學生",
    re.IGNORECASE,
)
_SINGULAR_STUDENT = re.compile(
    r"\b(?:a|the|year\s+\d+)?\s*(?:student|pupil|child|murid|pelajar)\b"
    r"[^.!?;\n]{0,120}",
    re.IGNORECASE,
)
_NAMED_STUDENT = re.compile(
    r"(?:\b(?:student|pupil|child|murid|pelajar)\s+|\bthat\s+|[:：]\s*)"
    r"([A-Z][A-Za-z'’-]{1,40})\b[^.!?;\n]{0,120}",
)
_CHINESE_DIRECT = re.compile(
    r"(?:[A-Za-z][A-Za-z'’-]{1,40}|学生|學生|该生|該生)"
    r"[^。！？；\n]{0,45}(?:不及格|分数|分數|诊断|診斷|纪律|紀律|伤势|傷勢|"
    r"家庭背景|家庭收入|霸凌)",
    re.IGNORECASE,
)
_CHINESE_NAMED_SENSITIVE = re.compile(
    r"(?:^|[：:,，；;。！？\s])"
    r"([\u3400-\u9fff]{2,4}?)(?=(?:的)?(?:数学|數學|国文|國文|"
    r"华文|華文|英文|英语|英語|科学|科學|历史|歷史|地理)?"
    r"(?:不及格|\d+(?:\.\d+)?\s*分|成绩|成績|分数|分數|"
    r"纪律|紀律|受伤|受傷|诊断|診斷))"
)
_DIRECT_PII = re.compile(
    r"\b(?:ic\s+(?:number|no\.?)|mykid|passport\s+(?:number|no\.? )?|"
    r"phone\s+(?:number|no\.?)|home\s+address|email\s+address|"
    r"contact\s+(?:number|details?)|lives?\s+at)\b|"
    r"\b(?:nombor\s+(?:kad\s+pengenalan|mykid|telefon)|alamat\s+rumah)\b|"
    r"身份证|身份證|护照号码|護照號碼|电话号码|電話號碼|住址|家庭地址",
    re.IGNORECASE,
)


def is_broad_output(*, audience: str = "", role: str = "") -> bool:
    return audience.strip().lower() in _BROAD_AUDIENCES \
        or role.strip().lower() in _BROAD_ROLES


def _contains_person_sensitive_term(value: str) -> bool:
    """Distinguish academic failure from failure of equipment or services."""
    return bool(
        _SENSITIVE.search(value or "")
        or _ACADEMIC_FAILURE.search(value or "")
    )


def _named_academic_failure_identifiers(value: str) -> set[str]:
    found = {
        match.group("name")
        for match in _EXPLICIT_NAMED_STUDENT_FAILURE.finditer(value or "")
        if match.group("name").casefold() not in _NON_PERSON_NAME_TOKENS
    }
    for match in _NAMED_ACADEMIC_FAILURE.finditer(value or ""):
        candidate = match.group("student_name") or match.group("name") or ""
        if candidate and candidate.casefold() not in _NON_PERSON_NAME_TOKENS:
            found.add(candidate)
    return found


def source_has_individual_sensitive_detail(source: str) -> bool:
    """Return true only for person-level, not aggregate, sensitive content."""
    value = str(source or "")
    if not value:
        return False
    # Remove explicit exclusions before looking for positive disclosure cues.
    # This keeps "no student names or individual marks" safe while a mixed
    # sentence such as "class average 65, and Ali scored 12/100" still leaves
    # the person-level clause available to the checks below.
    scan_value = re.sub(
        r"\b(?:without|not|no|exclude(?:d|s|ing)?|omit(?:ted|s|ting)?|"
        r"do\s+not\s+(?:include|show|list))\b"
        r"[^.!?;\n]{0,55}\b(?:student|pupil)?\s*names?\b"
        r"(?:\s+or\s+individual\s+(?:marks?|scores?|results?))?|"
        r"\b(?:without|not|no|exclude(?:d|s|ing)?|omit(?:ted|s|ting)?)\b"
        r"[^.!?;\n]{0,45}\bindividual\s+(?:marks?|scores?|results?)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    if _DIRECT_PII.search(scan_value):
        return True
    if not _contains_person_sensitive_term(scan_value):
        return False
    if (
        _AGGREGATE.search(scan_value)
        and not _IDENTITY_CUE.search(scan_value)
        and not _NAMED_STUDENT.search(scan_value)
        and not _CHINESE_NAMED_SENSITIVE.search(scan_value)
        and not source_identifiers(scan_value)
    ):
        return False
    if (
        _IDENTITY_CUE.search(scan_value)
        or _CHINESE_DIRECT.search(scan_value)
        or _CHINESE_NAMED_SENSITIVE.search(scan_value)
    ):
        return True
    if any(
        _contains_person_sensitive_term(match.group(0))
        for match in _SINGULAR_STUDENT.finditer(scan_value)
    ):
        return True
    if any(
        _contains_person_sensitive_term(match.group(0))
        for match in _NAMED_STUDENT.finditer(scan_value)
    ):
        return True
    if _named_academic_failure_identifiers(scan_value):
        return True
    # Natural Malay/English often puts the name first: "Ali gagal" or
    # "Ali failed".  Limit the proper-name cue to a short sensitive clause.
    named_sensitive = re.compile(
        r"\b([A-Z][A-Za-z'’-]{1,40})\b(?=[^.!?;\n]{0,55}"
        r"(?i:weak|mark(?:s|ah)?|score|grade|gagal|lemah|diagnos|"
        r"adhd|disciplin|disiplin|injur|cedera|medical|kesihatan|"
        r"medicat|medicine|prescription|psycholog|psychiatr|therap|"
        r"counsell|mental\s+health|"
        r"medicat|medicine|prescription|psycholog|psychiatr|therap|"
        r"counsell|mental\s+health|"
        r"poor\s+family|low[- ]income|financial\s+aid|family\s+background|"
        r"household\s+income|welfare|ppr|public\s+housing))",
    )
    return any(
        match.group(1).casefold() not in _NON_PERSON_NAME_TOKENS
        for match in named_sensitive.finditer(scan_value)
    )


def requires_broad_redaction(
    source: str,
    *,
    audience: str = "",
    role: str = "",
    excluded_concepts: Iterable[str] = (),
) -> bool:
    if not is_broad_output(audience=audience, role=role):
        return False
    excluded = {str(item).strip().lower() for item in excluded_concepts}
    return bool(excluded.intersection(_RESTRICTED_CONCEPTS)) \
        or source_has_individual_sensitive_detail(source)


def source_identifiers(source: str) -> set[str]:
    """Extract likely pupil identifiers for broad-output leak checks."""
    found = {match.group(1) for match in _NAMED_STUDENT.finditer(source or "")}
    found.update(match.group(1) for match in re.finditer(
        r"\b([A-Z][A-Za-z'’-]{1,40})['’]s\s+"
        r"(?:ic|mykid|passport|phone|address|email|mark|score|grade|"
        r"diagnosis|medical)",
        source or "",
        re.IGNORECASE,
    ))
    named_sensitive = re.compile(
        r"\b([A-Z][A-Za-z'’-]{1,40})\b(?=[^.!?;\n]{0,55}"
        r"(?i:weak|mark(?:s|ah)?|score|grade|gagal|lemah|diagnos|"
        r"adhd|disciplin|disiplin|injur|cedera|medical|kesihatan|"
        r"poor\s+family|low[- ]income|financial\s+aid|family\s+background|"
        r"household\s+income|lives?\s+at|ic\s+number|phone\s+number|"
        r"不及格|分数|分數|诊断|診斷|纪律|紀律|伤势|傷勢))"
    )
    found.update(
        match.group(1) for match in named_sensitive.finditer(source or "")
        if match.group(1).casefold() not in _NON_PERSON_NAME_TOKENS
    )
    found.update(_named_academic_failure_identifiers(source or ""))
    chinese_non_names = {
        "家长群", "家長群", "数学", "數學", "国文", "國文", "英语",
        "英語", "华文", "華文", "学生", "學生", "学校", "學校",
    }
    found.update(
        match.group(1)
        for match in _CHINESE_NAMED_SENSITIVE.finditer(source or "")
        if match.group(1) not in chinese_non_names
    )
    for match in re.finditer(
        r"(?:^|[:：,;]\s*|\b(?:that|bahawa)\s+)"
        r"([A-Z][A-Za-z'’-]{1,40})\b[^.!?;\n]{0,55}"
        r"(?:weak|mark(?:s|ah)?|score|grade|gagal|lemah|diagnos|"
        r"adhd|disciplin|disiplin|injur|cedera|medical|kesihatan|"
        r"不及格|分数|分數|诊断|診斷|纪律|紀律)",
        source or "",
        re.IGNORECASE,
    ):
        found.add(match.group(1))
    # Capitalisation and proximity are only candidate signals. School subject
    # acronyms, channel names, pronouns and drafting verbs are not pupils.
    # Filtering them here prevents a privacy-safe fallback from rejecting its
    # own generic wording (for example, source text containing "WhatsApp",
    # "BM" or "their full names").
    non_identifiers = _NON_PERSON_NAME_TOKENS.union({
        "all", "and", "bahasa", "bm", "child", "children", "each",
        "every", "facebook", "full", "guardian", "guardians", "his",
        "her", "include", "including", "list", "listed", "listing",
        "mark", "marks", "message", "name", "names", "notice", "parent",
        "parents", "please", "prepare", "pupil", "pupils", "reason",
        "report", "school", "score", "scores", "send", "student",
        "students", "subject", "the", "their", "this", "weak",
        "weakness", "whatsapp", "who", "year",
    })
    return {
        item for item in found
        if item.casefold() not in non_identifiers
    }


def source_individual_mark_values(source: str) -> set[str]:
    if not source_has_individual_sensitive_detail(source):
        return set()
    pairs = re.findall(
        r"(?:\b(?:mark|score|grade)s?\b|\bmarkah\b|分数|分數)"
        r"[^.!?;\n]{0,25}\b(\d+(?:\.\d+)?)\b|"
        r"\b(\d+(?:\.\d+)?)\b[^.!?;\n]{0,25}"
        r"(?:\b(?:mark|score|grade)s?\b|\bmarkah\b|分|分数|分數)",
        source or "",
        flags=re.IGNORECASE,
    )
    values = {value for pair in pairs for value in pair if value}
    values.update(re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*分", source or ""))
    return values


def source_direct_pii_values(source: str) -> set[str]:
    """Extract exact direct identifiers so the verifier can reject leakage."""
    if not _DIRECT_PII.search(source or ""):
        return set()
    values: set[str] = set()
    for match in re.finditer(
        r"(?:\b(?:ic|mykid|passport|phone|contact)\s*"
        r"(?:number|no\.?|details?)?\s*(?:is|:)?\s*)"
        r"([A-Z0-9+][A-Z0-9+\- ]{5,30})",
        source or "",
        re.IGNORECASE,
    ):
        values.add(match.group(1).strip(" ,.;"))
    for match in re.finditer(
        r"\b(?:lives?\s+at|home\s+address\s*(?:is|:)?|alamat\s+rumah\s*(?:ialah|:)?)"
        r"\s*([^.!?;\n]{5,100})",
        source or "",
        re.IGNORECASE,
    ):
        values.add(match.group(1).strip(" ,.;"))
    return {item for item in values if item}
