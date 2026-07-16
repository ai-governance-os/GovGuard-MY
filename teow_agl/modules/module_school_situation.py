"""School situation compiler and deterministic response-pack coverage.

The LLM-facing semantic intake may suggest a situation family and signals, but
it never chooses a governance route.  This module closes that suggestion onto
an allow-listed schema, derives a response pack from configuration, and then
reconciles the pack with the planner's proposed actions.  Every inserted action
continues through the ordinary 101D -> 101B -> 103 -> 105 -> 107 pipeline.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re
import uuid
from typing import Any

from ..models import CandidateAction, CandidatePlan, TaskEnvelope
from .module_school_artifact_guard import infer_artifact_role
from .module_school_release_intent import (
    infer_explicit_external_recipients as _shared_infer_external_recipients,
    negated_external_recipients as _shared_negated_external_recipients,
    release_is_globally_negated as _shared_release_is_negated,
)
from .module_school_privacy import source_has_individual_sensitive_detail


_SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_EXTERNAL_RECIPIENTS = {
    "guardian", "medical_services", "malaysia_emergency_services_999",
    "fire_and_rescue", "police", "education_authority", "local_authority",
    "event_organizer", "vendor", "transport_provider", "public_media",
    "school_community", "external_stakeholder",
}
_OFFICIAL_DOMAINS = (
    "moe.gov.my", "moh.gov.my", "malaysia.gov.my", "bomba.gov.my",
    "rmp.gov.my", "nadma.gov.my", "agc.gov.my", "pdp.gov.my",
    "cybersecurity.my", "mcmc.gov.my",
)
_OFFICIAL_RESEARCH_TOPICS = {
    "safety_emergency": "Malaysia school safety emergency guidance",
    "health_medical": "Malaysia school health medical incident guidance",
    "safeguarding_welfare": "Malaysia school safeguarding reporting guidance",
    "facilities_environment": "Malaysia school premises safety guidance",
    "transport_travel": "Malaysia school transport incident guidance",
    "food_hygiene": "Malaysia school food safety guidance",
    "cyber_data": "Malaysia school cyber data incident guidance",
    "records_regulatory": "Malaysia school incident reporting requirements",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    value = (text or "").casefold()
    return any(item.casefold() in value for item in values)


def _logical_clauses(text: str) -> list[str]:
    """Return short clauses for high-precision deterministic predicates."""
    return [
        clause.strip(" ,:")
        for clause in re.split(
            r"[.!?;。！？；\n]+|\b(?:but|however|whereas|nevertheless|instead|"
            r"tetapi|namun|sebaliknya)\b|(?:但是|然而|不过|不過)",
            (text or "").casefold(),
        )
        if clause.strip(" ,:")
    ]


def _planned_hypothetical_clause(clause: str) -> bool:
    """True for a planned/simulated scenario, not an incident during a drill."""
    planned = bool(re.search(
        r"\b(?:plan(?:ned|ning)?|prepare|scheduled|next\s+(?:week|month)|"
        r"tomorrow|awareness|campaign|training|simulation|simulate|scenario|"
        r"tabletop|prevention|workshop|lesson|exercise|drill)\b",
        clause,
    ) or re.search(
        r"\b(?:rancang(?:an)?|dirancang|dijadualkan|minggu\s+depan|"
        r"bulan\s+depan|esok|kesedaran|kempen|latihan|simulasi|senario|"
        r"pencegahan|bengkel|pelajaran)\b|"
        r"(?:计划|計劃|安排|下周|下週|下个月|下個月|明天|意识|意識|宣传|宣傳|"
        r"培训|培訓|模拟|模擬|情境|预防|預防|讲座|講座|课程|課程|演习|演習)",
        clause,
    ))
    if not planned:
        return False
    if re.search(
        r"\b(?:will|would|should)\s+(?:pretend|simulate|act|role[- ]play)|"
        r"\b(?:pretend|hypothetical|mock)\b",
        clause,
    ):
        return True
    if (
        re.search(
            r"\b(?:was\s+injured|were\s+injured|collapsed|fainted|fell|"
            r"was\s+bitten|were\s+bitten|broke|fractured)\b",
            clause,
        )
        and not re.search(
            r"\b(?:plan|prepare|simulate|scenario|next\s+(?:week|month)|"
            r"tomorrow|will|would|hypothetical|mock)\b",
            clause,
        )
    ):
        return False
    if re.search(
        r"\b(?:during|while)\b[^.!?;]{0,100}\b(?:actually|unexpectedly|"
        r"suddenly|really|in\s+fact|collapsed|fainted|fell|was\s+injured|"
        r"was\s+bitten|broke|fractured)\b",
        clause,
    ):
        return False
    return True


_SCHOOL_LEVEL_PREFIX = (
    r"(?:(?:(?:year|form|standard|grade)\s+[a-z0-9]+|"
    r"\d{1,2}[- ]year[- ]old)\s+)?"
)
_PERSON = (
    rf"(?:{_SCHOOL_LEVEL_PREFIX}(?:students?|pupils?|child(?:ren)?)|"
    r"teachers?|staff|visitors?|persons?|murid|pelajar|kanak-kanak)"
)
_MINOR = (
    rf"(?:{_SCHOOL_LEVEL_PREFIX}(?:students?|pupils?|child(?:ren)?)|"
    r"murid|pelajar|kanak-kanak)"
)
_ANIMAL = r"(?:snake|dog|wild\s+animal|animal|ular|anjing|haiwan\s+liar)"
_VEHICLE = r"(?:car|vehicle|school\s+bus|bus|motorbike|motorcycle|school\s+van|van)"
_VEHICLE_IMPACT = r"(?:hit|struck|knocked\s+down|knocked\s+over|ran\s+over)"
_BODY_PART = (
    r"(?:arm|leg|wrist|ankle|hand|foot|shoulder|rib|head|knee|elbow|"
    r"finger|toe|face)"
)


def _incident_clause_negated(clause: str) -> bool:
    """Return true when the same clause expressly denies the incident.

    This is deliberately clause-local: a later instruction such as "do not
    send" must not erase a real incident in an earlier clause.
    """
    return bool(re.search(
        r"\b(?:no|not|never|did\s+not|was\s+not|were\s+not|none|"
        r"tiada|tidak|bukan)\b[^.!?;。！？；]{0,55}"
        r"\b(?:bit|bitten|bite|injured|hurt|dipatuk|mematuk|digigit|"
        r"menggigit|cedera)\b|"
        r"(?:没有|沒有|并未|並未|无人|無人|未有)[^。！？；]{0,35}"
        r"(?:被?蛇咬|咬伤|咬傷|受伤|受傷)",
        clause,
    ))


def _multilingual_animal_bite_flags(clause: str) -> tuple[bool, bool]:
    """High-precision Malay/Chinese animal-bite source grammar."""
    if _incident_clause_negated(clause):
        return False, False
    malay = bool(re.search(
        r"\b(?:seekor\s+)?(?:ular|anjing|haiwan\s+liar)\b"
        r"[^.!?;。！？；]{0,55}\b(?:telah\s+|dilaporkan\s+)?"
        r"(?:mematuk|menggigit)\b[^.!?;。！？；]{0,35}"
        r"\b(?:seorang\s+|satu\s+)?(?:murid|pelajar|kanak-kanak)\b|"
        r"\b(?:murid|pelajar|kanak-kanak)\b[^.!?;。！？；]{0,55}"
        r"\b(?:telah\s+|dilaporkan\s+)?(?:dipatuk|digigit)\b"
        r"[^.!?;。！？；]{0,28}\b(?:oleh\s+)?(?:seekor\s+)?"
        r"(?:ular|anjing|haiwan\s+liar)\b",
        clause,
    ))
    chinese = bool(re.search(
        r"(?:蛇|狗|野生动物|野生動物)[^。！？；]{0,28}"
        r"(?:咬伤|咬傷|咬了|咬到)[^。！？；]{0,18}"
        r"(?:一名|一个|一個|该名|該名)?(?:学生|學生|孩子|儿童|兒童)|"
        r"(?:学生|學生|孩子|儿童|兒童)[^。！？；]{0,28}"
        r"(?:被|遭)(?:一条|一條|一只|一隻)?(?:蛇|狗|野生动物|野生動物)"
        r"(?:咬伤|咬傷|咬了|咬到)",
        clause,
    ))
    matched = malay or chinese
    return matched, matched


def _physical_incident_flags(text: str) -> tuple[bool, bool]:
    """Return ``(physical_incident, minor_physically_affected)``.

    Ambiguous words such as ``bit``, ``hurt`` and ``burned`` only count when
    their grammar proves bodily harm. This keeps educational metaphors and
    ordinary actions out of the emergency route.
    """
    incident = False
    minor_affected = False
    for clause in _logical_clauses(text):
        if _planned_hypothetical_clause(clause):
            continue
        clear_person_harm = bool(re.search(
            rf"\b{_PERSON}\b[^.!?;]{{0,90}}\b(?:was\s+|is\s+|has\s+been\s+|"
            rf"had\s+been\s+|reportedly\s+)?(?:injured|bleeding|unconscious|"
            rf"poisoned|collapsed|fainted|faints|vomited|vomiting|"
            rf"became\s+ill|fell\s+sick)\b|"
            rf"\b(?:injured|bleeding|unconscious|poisoned)\s+{_PERSON}\b",
            clause,
        ))
        minor_surface_injury = bool(re.search(
            rf"\b{_PERSON}\b[^.!?;]{{0,55}}\b(?:scraped|cut|bruised)\s+"
            rf"(?:an?\s+|the\s+|his\s+|her\s+|their\s+)?{_BODY_PART}\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,55}}\b(?:has|had|sustained|got)\s+"
            rf"(?:an?\s+)?(?:scrape|cut|bruise)\s+(?:on\s+)?"
            rf"(?:the\s+|his\s+|her\s+|their\s+)?{_BODY_PART}\b",
            clause,
        ))
        bite = bool(re.search(
            rf"\b{_ANIMAL}\b[^.!?;]{{0,45}}\b(?:bit|bites|has\s+bitten|"
            rf"reportedly\s+bit|reportedly\s+bitten)\s+(?:a\s+|the\s+|"
            rf"two\s+|three\s+|\d+\s+)?{_PERSON}\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,45}}\b(?:was\s+|has\s+been\s+|"
            rf"reportedly\s+)?(?:bit|bitten)\s+by\s+(?:a\s+|the\s+)?{_ANIMAL}\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,45}}\b(?:suffered|has|had)\s+"
            rf"(?:a\s+)?{_ANIMAL}\s+bite\b|"
            rf"\b{_ANIMAL}\s+bite\b[^.!?;]{{0,45}}\b{_PERSON}\b",
            clause,
        ))
        multilingual_bite, multilingual_minor = (
            _multilingual_animal_bite_flags(clause)
        )
        bite = bite or multilingual_bite
        vehicle = bool(re.search(
            rf"\b{_VEHICLE}\b[^.!?;]{{0,45}}\b"
            rf"{_VEHICLE_IMPACT}\s+(?:a\s+|the\s+|two\s+|"
            rf"three\s+|\d+\s+)?{_PERSON}\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,45}}\b(?:was\s+|got\s+|gets?\s+|"
            rf"has\s+been\s+)?"
            rf"{_VEHICLE_IMPACT}\s+by\s+(?:a\s+|the\s+)?{_VEHICLE}\b",
            clause,
        ))
        fracture_or_fall = bool(re.search(
            rf"\b{_PERSON}\b[^.!?;]{{0,80}}\b(?:fell|fallen|slipped|tumbled)\b"
            rf"[^.!?;]{{0,70}}\b(?:broke|broken|fractured)\s+(?:an?\s+|"
            rf"his\s+|her\s+|their\s+)?{_BODY_PART}\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,65}}\b(?:broke|fractured|has\s+"
            rf"(?:a\s+)?fractured|has\s+(?:a\s+)?broken|had\s+(?:a\s+)?"
            rf"broken|suffered\s+(?:a\s+)?fractured)\s+"
            rf"(?:an?\s+|his\s+|her\s+|their\s+)?{_BODY_PART}\b",
            clause,
        ))
        acute_medical = bool(re.search(
            rf"\b{_PERSON}\b[^.!?;]{{0,70}}\b(?:had|has|is\s+having|"
            rf"suffered|suffers)\s+(?:an?\s+)?(?:asthma\s+attack|seizure)\b|"
            rf"\b(?:asthma\s+attack|seizure)\b[^.!?;]{{0,50}}\b{_PERSON}\b",
            clause,
        ))
        physical_burn = bool(re.search(
            rf"\b{_PERSON}\b[^.!?;]{{0,65}}\b(?:was\s+|got\s+)?"
            rf"(?:burned|burnt|scalded)\s+by\s+(?:hot|boiling)\s+water\b|"
            rf"\b(?:hot|boiling)\s+water\b[^.!?;]{{0,70}}\b"
            rf"(?:burned|burnt|scalded)\s+(?:a\s+|the\s+)?{_PERSON}\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,50}}\b(?:suffered|has|had)\s+"
            rf"(?:a\s+)?(?:burn|burns|scald|scalds)\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,55}}\bburned\s+(?:his|her|their)\s+"
            rf"{_BODY_PART}\s+with\s+(?:hot|boiling)\s+water\b|"
            rf"\b{_PERSON}\b[^.!?;]{{0,55}}\b(?:sustained|suffered)\s+"
            rf"burns?\s+from\s+(?:hot|boiling)\s+water\b",
            clause,
        ))
        physical_hurt = bool(re.search(
            rf"\b{_PERSON}\b[^.!?;]{{0,55}}\b(?:was\s+|got\s+)?hurt\s+"
            rf"(?:physically|in\s+(?:an?\s+)?(?:accident|fall|collision)|"
            rf"(?:his|her|their)\s+{_BODY_PART})\b",
            clause,
        ))
        clause_incident = any((
            clear_person_harm, bite, vehicle, fracture_or_fall,
            acute_medical, physical_burn, physical_hurt,
            minor_surface_injury,
        ))
        if not clause_incident:
            continue
        incident = True
        minor_patterns = (
            rf"\b{_MINOR}\b[^.!?;]{{0,100}}\b(?:injured|bleeding|unconscious|"
            rf"poisoned|collapsed|fainted|faints|bit|bitten|hit|struck|"
            rf"knocked\s+down|knocked\s+over|ran\s+over|fell|"
            rf"slipped|broke|broken|fractured|asthma\s+attack|seizure|burned|"
            rf"burnt|scalded|hurt|vomited|vomiting|became\s+ill|fell\s+sick|"
            rf"scraped|cut|bruised)\b",
            rf"\b(?:injured|bleeding|unconscious|poisoned|bitten|hit|struck|"
            rf"burned|burnt|scalded)\b\s+(?:a\s+|the\s+|two\s+|three\s+|"
            rf"\d+\s+)?{_MINOR}\b",
            rf"\b{_ANIMAL}\b[^.!?;]{{0,50}}\b(?:bit|bites|bitten)\b"
            rf"[^.!?;]{{0,30}}\b{_MINOR}\b",
            rf"\b{_VEHICLE}\b[^.!?;]{{0,55}}\b{_VEHICLE_IMPACT}\b"
            rf"[^.!?;]{{0,35}}\b{_MINOR}\b",
            rf"\b{_MINOR}\b[^.!?;]{{0,55}}\b{_VEHICLE_IMPACT}\b"
            rf"[^.!?;]{{0,35}}\b(?:by\s+)?(?:a\s+|the\s+)?{_VEHICLE}\b",
        )
        if multilingual_minor or any(
            re.search(pattern, clause) for pattern in minor_patterns
        ):
            minor_affected = True
    return incident, minor_affected


def _active_hazard_present(text: str) -> bool:
    value = (text or "").casefold()
    status_unknown = re.compile(
        r"\b(?:do\s+not|don't|cannot|can't)\s+know\s+whether\b|"
        r"\b(?:unknown|unclear|not\s+sure|uncertain)\s+whether\b|"
        r"\b(?:tidak\s+tahu|belum\s+pasti|tidak\s+pasti)\s+sama\s+ada\b|"
        r"(?:不知道|不清楚|不确定|不確定|尚未确定|尚未確定)(?:是否|有无|有無)"
    )
    resolved = re.compile(
        r"\b(?:removed|captured|contained|cleared|resolved|extinguished|"
        r"neutralised|neutralized|safe\s+now|no\s+longer\s+present|"
        r"no\s+(?:immediate|active|continuing)\s+danger|"
        r"danger\s+(?:is|was)\s+not\s+(?:present|ongoing|immediate)|"
        r"telah\s+(?:ditangkap|dialihkan|dikawal|selamat)|"
        r"tiada\s+bahaya\s+(?:segera|aktif))\b|"
        r"(?:已捕获|已捕獲|已移走|已控制|危险已解除|危險已解除|没有即时危险|沒有即時危險)"
    )
    active = re.compile(
        r"\b(?:there\s+is|we\s+have|reported|detected|broke\s+out|"
        r"filling\s+(?:the\s+)?(?:room|building|school)|on\s+(?:the\s+)?"
        r"(?:campus|school\s+grounds))\b[^.!?;]{0,75}\b"
        r"(?:fire|smoke|gas\s+leak|chemical\s+spill|armed\s+intruder|"
        r"snake|wild\s+animal)\b|"
        r"\b(?:fire|smoke|gas\s+leak|chemical\s+spill|armed\s+intruder|"
        r"snake|wild\s+animal)\b[^.!?;]{0,100}\b(?:broke\s+out|"
        r"detected|reported|entered|on\s+(?:the\s+)?campus|"
        r"in\s+(?:the\s+)?school|still|may\s+still|currently|now|"
        r"ongoing|active|loose)\b|"
        r"\b(?:still|may\s+still|currently|right\s+now|ongoing|active|loose)\b"
        r"[^.!?;]{0,100}\b(?:fire|smoke|gas\s+leak|chemical\s+spill|"
        r"armed\s+intruder|snake|wild\s+animal)\b|"
        r"\b(?:there\s+is|reported|confirmed)\b[^.!?;]{0,35}"
        r"\b(?:active|immediate)\s+danger\b|"
        r"\b(?:ular|haiwan\s+liar)\b[^.!?;]{0,100}"
        r"\b(?:masih|mungkin\s+masih|sedang|berkeliaran|belum\s+ditangkap)\b|"
        r"\b(?:masih|mungkin\s+masih|sedang)\b[^.!?;]{0,100}"
        r"\b(?:ular|haiwan\s+liar)\b|"
        r"(?:蛇|野生动物|野生動物)[^。！？；]{0,70}"
        r"(?:仍|还在|還在|可能仍|游荡|遊蕩|未捕获|未捕獲)"
    )
    appeared_on_site = re.compile(
        r"\b(?:snake|wild\s+animal)\b[^.!?;]{0,30}\b"
        r"(?:has\s+|was\s+|is\s+)?(?:appeared|entered|seen|found|loose)\b"
        r"[^.!?;]{0,60}\b(?:at|in|on)\s+(?:the\s+)?"
        r"(?:school|campus|school\s+grounds|classroom|canteen)\b|"
        r"\b(?:at|in|on)\s+(?:the\s+)?(?:school|campus|school\s+grounds|"
        r"classroom|canteen)\b[^.!?;]{0,60}\b(?:snake|wild\s+animal)\b"
        r"[^.!?;]{0,30}\b(?:appeared|entered|seen|found|loose)\b|"
        r"\b(?:ular|haiwan\s+liar)\b[^.!?;]{0,70}"
        r"\b(?:di|berhampiran|dekat)\s+(?:kawasan\s+)?"
        r"(?:sekolah|kampus|kantin|bilik\s+darjah)\b[^.!?;]{0,45}"
        r"\b(?:masih|berkeliaran|belum\s+ditangkap)\b|"
        r"(?:蛇|野生动物|野生動物)[^。！？；]{0,55}"
        r"(?:学校|學校|校园|校園|食堂|课室|課室)[^。！？；]{0,30}"
        r"(?:仍|还在|還在|游荡|遊蕩|未捕获|未捕獲)"
    )
    for clause in _logical_clauses(value):
        if _planned_hypothetical_clause(clause):
            continue
        # "We do not know whether danger remains" is a question-triggering
        # unknown, not evidence that danger is present.
        if status_unknown.search(clause):
            continue
        if resolved.search(clause):
            continue
        if active.search(clause) or appeared_on_site.search(clause):
            return True
    return False


def _life_safety_status_unknown(text: str) -> bool:
    """Detect an expressly unresolved danger or urgent-care question.

    The model may be unavailable exactly when a school needs the safe path.
    This source-text floor asks one bounded human question; it never infers
    that emergency help is required or that a responder was contacted.
    """
    for clause in _logical_clauses(text):
        uncertain = bool(re.search(
            r"\b(?:do\s+not|don't|cannot|can't)\s+know\s+whether\b|"
            r"\b(?:unknown|unclear|not\s+sure|uncertain)\s+whether\b|"
            r"\b(?:tidak\s+tahu|belum\s+pasti|tidak\s+pasti)\s+sama\s+ada\b|"
            r"(?:不知道|不清楚|不确定|不確定|尚未确定|尚未確定)(?:是否|有无|有無)",
            clause,
        ))
        if not uncertain:
            continue
        life_status = bool(re.search(
            r"\b(?:urgent|emergency|immediate)\s+(?:medical\s+)?(?:help|care|"
            r"treatment|assistance)\b|\b(?:medical\s+help|ambulance|danger|"
            r"hazard)\b[^.!?;]{0,45}\b(?:still\s+)?(?:needed|present|ongoing)\b|"
            r"\b(?:bantuan|rawatan)\s+perubatan\s+kecemasan\b|"
            r"\b(?:bahaya|kecemasan)\b[^.!?;]{0,45}\b(?:masih|berterusan)\b|"
            r"(?:仍需|还需|還需|是否需要|是否仍有)(?:紧急|緊急)?(?:医疗|醫療|救护|救護|危险|危險)",
            clause,
        ))
        if life_status:
            return True
    return False


def _actual_safeguarding_concern(text: str) -> bool:
    """Return true only for a reported safeguarding case, not a topic."""
    for clause in _logical_clauses(text):
        if _planned_hypothetical_clause(clause):
            continue
        if re.search(
            r"\b(?:policy|agenda|guideline|procedure|curriculum|poster|"
            r"awareness|prevention|training|workshop|lesson|campaign|review)\b",
            clause,
        ) and not re.search(
            r"\b(?:reported|disclosed|complained|alleged|occurred|happened|"
            r"was\s+(?:bullied|abused|harassed|assaulted)|is\s+missing)\b",
            clause,
        ):
            continue
        if re.search(
            r"\b(?:student|pupil|child|murid|pelajar)\b[^.!?;]{0,90}\b"
            r"(?:was\s+|is\s+|has\s+been\s+|reported\s+being\s+)?"
            r"(?:bullied|abused|harassed|assaulted|groomed|missing)\b|"
            r"\b(?:reported|disclosed|complained\s+of|alleged)\b"
            r"[^.!?;]{0,80}\b(?:abuse|bullying|harassment|sexual\s+misconduct|"
            r"grooming|self[- ]harm)\b|"
            r"\b(?:self[- ]harm|suicide\s+attempt|suicidal\s+intent)\b",
            clause,
        ):
            return True
    return False


def _actual_cyber_incident(text: str) -> bool:
    for clause in _logical_clauses(text):
        if _planned_hypothetical_clause(clause):
            continue
        if re.search(
            r"\b(?:no|without)\s+(?:actual\s+)?(?:data\s+breach|cyber\s+"
            r"incident|ransomware|compromised\s+account)|\b(?:did\s+not|"
            r"didn't)\s+(?:click|open)|\bno\s+(?:data|account|system)s?\s+"
            r"(?:was|were)\s+(?:leaked|exposed|compromised)\b",
            clause,
        ):
            continue
        if re.search(
            r"\b(?:data\s+breach|cyber\s+incident|ransomware\s+attack)\b"
            r"[^.!?;]{0,80}\b(?:occurred|detected|confirmed|happened|"
            r"encrypted|locked|compromised)\b|"
            r"\b(?:suffered|detected|confirmed)\s+(?:a\s+)?(?:data\s+breach|"
            r"cyber\s+incident|ransomware\s+attack)\b|"
            r"\bransomware\b[^.!?;]{0,55}\b(?:encrypted|locked|compromised)\b|"
            r"\b(?:received|detected|reported)\s+(?:a\s+|the\s+)?"
            r"phishing\s+(?:email|message|link)\b|"
            r"\b(?:teacher|staff|student|user)\b[^.!?;]{0,60}\b(?:clicked|"
            r"opened)\s+(?:a\s+|the\s+)?(?:phishing\s+)?(?:link|attachment)\b|"
            r"\b(?:phishing\s+(?:email|message|link))\b[^.!?;]{0,60}\b"
            r"(?:was\s+)?(?:received|clicked|opened|reported|detected)\b|"
            r"\b(?:student|pupil|parent|personal|school)[-\s]+(?:data|records?|"
            r"information)\b[^.!?;]{0,80}\b(?:was\s+|were\s+|has\s+been\s+)?"
            r"(?:leaked|exposed|compromised|(?:sent|shared|forwarded|email(?:ed)?)"
            r"\s+(?:to\s+)?(?:an?\s+|the\s+)?(?:wrong|unintended|unauthori[sz]ed)"
            r"\s+(?:person|recipient|vendor|supplier|address|party))\b|"
            r"\b(?:account|system|portal|database)\b[^.!?;]{0,55}\b"
            r"(?:was\s+|has\s+been\s+)?(?:compromised|hacked)\b",
            clause,
        ):
            return True
    return False


def _corroborate_semantic_impact(
    text: str,
    family: str,
    severity: str,
    signals: set[str],
    stakeholders: set[str],
) -> tuple[str, str, set[str], set[str]]:
    """Ceiling high-impact semantic labels with deterministic source cues."""
    low = str(text or "").casefold()
    physical, physical_minor = _physical_incident_flags(low)
    active = _active_hazard_present(low)
    cyber = _actual_cyber_incident(low)
    missing = bool(re.search(
        r"\b(?:missing|unaccounted[- ]for)\s+(?:student|pupil|child)\b|"
        r"\b(?:student|pupil|child)\s+(?:is\s+)?missing\b|"
        r"学生失踪|學生失蹤|murid\s+hilang|pelajar\s+hilang", low,
    ))
    safeguarding = _actual_safeguarding_concern(low) or missing
    finance = bool(re.search(
        r"\brm\s*\d|\b(?:budget|money|funds?|payment|fee|cost|price|"
        r"purchase|procure|supplier|vendor|donation|fundrais(?:e|ing)|"
        r"sales?|cash|invoice|quotation|coupons?|coupon\s+books?|"
        r"charity\s+bazaar)\b|预算|預算|款项|款項|采购|採購|"
        r"捐款|义卖|義賣|bajet|wang|bayaran|pembelian|derma", low,
    ))
    regulatory = bool(re.search(
        r"\b(?:district\s+education\s+office|education\s+authority|ppd|jpn|"
        r"ministry\s+of\s+education|regulator|statutory|official\s+portal)\b|"
        r"教育局|教育部|监管|監管|pejabat\s+pendidikan", low,
    ))
    # A topic can retain the school domain family, but only an actual incident
    # may retain exposure, injury and guardian-impact signals.  This prevents
    # an allergy menu or meeting agenda from becoming an emergency pack.
    food_topic = bool(re.search(
        r"\b(?:food|menu|canteen|lunch|meal)\b[^.!?;]{0,60}\b"
        r"(?:allerg(?:y|ies|ic)|anaphylaxis)\b|"
        r"\b(?:allerg(?:y|ies|ic)|anaphylaxis)\b[^.!?;]{0,60}\b"
        r"(?:food|menu|canteen|lunch|meal|student|pupil|child)\b|"
        r"\b(?:food|menu|canteen|lunch|meal|allergen)\b|"
        r"alahan\s+makanan|alergi\s+makanan|食物过敏|食物過敏", low,
    ))
    food_incident = bool(re.search(
        r"\b(?:food\s+poisoning|contaminated\s+(?:food|water)|unsafe\s+food)\b|"
        r"\b(?:student|pupil|child|students|pupils|children)\b"
        r"[^.!?;]{0,55}\b(?:vomited|vomiting|fell\s+sick|became\s+ill)\b"
        r"[^.!?;]{0,55}\b(?:after|from|following)\b[^.!?;]{0,35}\b"
        r"(?:lunch|meal|food|eating|canteen)\b|"
        r"\b(?:after|from|following)\b[^.!?;]{0,35}\b"
        r"(?:lunch|meal|food|eating|canteen)\b[^.!?;]{0,55}\b"
        r"(?:student|pupil|child|students|pupils|children)\b"
        r"[^.!?;]{0,35}\b(?:vomited|vomiting|fell\s+sick|became\s+ill)\b|"
        r"\b(?:student|pupil|child|students|pupils|children)\b"
        r"[^.!?;]{0,70}\b(?:had|has|suffered|experienced|is\s+having)\b"
        r"[^.!?;]{0,25}\b(?:anaphylaxis|allergic\s+reaction)\b|"
        r"食物中毒|食水污染|食物污染|keracunan\s+makanan|air\s+tercemar", low,
    ))
    evacuation = any(
        not _planned_hypothetical_clause(clause)
        and bool(re.search(
            r"\b(?:school|students?|pupils?|children)\b[^.!?;]{0,60}\b"
            r"(?:was|were|have\s+been|had\s+been)?\s*evacuated\b|"
            r"\bevacuation\b[^.!?;]{0,45}\b(?:was\s+)?"
            r"(?:ordered|started|underway|completed|carried\s+out)\b|"
            r"\bmoved\s+(?:students?|pupils?|children)\s+to\s+(?:the\s+)?"
            r"assembly\s+(?:area|point)\b",
            clause,
        ))
        for clause in _logical_clauses(low)
    )
    transport = bool(re.search(
        r"\b(?:school\s+bus|bus\s+(?:crash|accident|breakdown)|transport\s+"
        r"incident|van\s+accident)\b|校车|校車|bas\s+sekolah|kemalangan\s+bas", low,
    ))
    facility = bool(re.search(
        r"\b(?:water\s+leak|power\s+outage|broken\s+(?:pipe|gate|roof)|"
        r"(?:water\s+)?pipe\s+burst|burst\s+(?:water\s+)?pipe|"
        r"classrooms?\s+(?:cannot|can\s+not)\s+be\s+used|"
        r"building\s+damage|toilet\s+fault|electrical\s+fault)\b|"
        r"漏水|停电|停電|设施故障|設施故障|kebocoran\s+air", low,
    ))
    public = bool(re.search(
        r"\b(?:facebook|public\s+(?:post|notice|statement)|website|social\s+media)\b|"
        r"脸书|臉書|公开通知|公開通知", low,
    ))
    student_mentioned = bool(re.search(
        r"\b(?:student|pupil|child|murid|pelajar)\b|学生|學生|孩子", low,
    ))
    parent_mentioned = bool(re.search(
        r"\b(?:parent|guardian|family|ibu\s+bapa|penjaga)\b|家长|家長|监护人|監護人", low,
    ))

    keep = set(signals)
    predicates = {
        "active_danger": active,
        "injury_or_illness": physical,
        "person_missing": missing,
        "safeguarding_concern": safeguarding,
        "external_help_may_be_required": active or missing or safeguarding,
        "possible_regulatory_trigger": regulatory or active or missing or cyber,
        "public_interest": public,
        "financial_value_involved": finance,
        "transport_operation": transport,
        "food_water_exposure": food_incident,
        "evacuation_accountability": active or missing or evacuation,
        "data_security_incident": cyber,
        "guardian_notification_relevant": (
            parent_mentioned or (physical and physical_minor) or safeguarding
            or (food_incident and physical_minor)
        ),
    }
    for signal, supported in predicates.items():
        if not supported:
            keep.discard(signal)
    if "minor_involved" in keep and not (
        (physical and physical_minor) or missing or safeguarding or student_mentioned
    ):
        keep.discard("minor_involved")
    if "evidence_preservation_needed" in keep and not (
        physical or safeguarding or cyber
        or bool(re.search(r"\b(?:incident|disciplin|investigat)\b", low))
    ):
        keep.discard("evidence_preservation_needed")

    category_supported = {
        "safety_emergency": active or physical or missing or safeguarding,
        "health_medical": physical,
        "safeguarding_welfare": safeguarding,
        "facilities_environment": facility or active,
        "transport_travel": transport,
        "food_hygiene": food_topic or food_incident,
        "cyber_data": cyber,
        "finance_procurement": finance,
        "records_regulatory": regulatory,
    }
    if family in category_supported and not category_supported[family]:
        family = "general_school_admin"

    uncertain_injury_status = bool(
        physical and re.search(
            r"\b(?:student|pupil|child|teacher|staff|visitor)\b"
            r"[^.!?;]{0,45}\b(?:was\s+|is\s+)?(?:found|discovered)\b"
            r"[^.!?;]{0,35}\b(?:injured|bleeding|unconscious)\b|"
            r"\b(?:found|discovered)\b[^.!?;]{0,35}\b"
            r"(?:student|pupil|child|teacher|staff|visitor)\b"
            r"[^.!?;]{0,35}\b(?:injured|bleeding|unconscious)\b",
            low,
        )
    )
    urgent = bool(
        active or missing
        or uncertain_injury_status
        or re.search(
            r"\b(?:not\s+breathing|unconscious|severe|critical|bleeding\s+"
            r"heavily|ambulance|hospitali[sz]ed|seizure|poisoned|snake\s+bite)\b|"
            r"\b(?:student|pupil|child)\b[^.!?;]{0,55}\b"
            rf"(?:was\s+|got\s+|has\s+been\s+)?{_VEHICLE_IMPACT}\s+"
            rf"by\s+(?:a\s+|the\s+)?{_VEHICLE}\b|"
            rf"\b{_VEHICLE}\b[^.!?;]{{0,55}}\b{_VEHICLE_IMPACT}"
            r"\s+(?:a\s+|the\s+)?"
            rf"{_MINOR}\b|"
            r"\b(?:ular\b[^.!?;]{0,45}\bmematuk|"
            r"(?:murid|pelajar|kanak-kanak)\b[^.!?;]{0,45}\bdipatuk)\b|"
            r"(?:蛇[^。！？；]{0,25}咬伤|蛇[^。！？；]{0,25}咬傷)|"
            r"昏迷|大量出血|救护车|救護車|送院|sawan|tidak\s+sedarkan\s+diri", low,
        )
    )
    any_material = bool(
        physical or active or cyber or missing or safeguarding or finance
        or regulatory or food_topic or food_incident or transport or facility
    )
    if severity in {"high", "critical"} and not urgent:
        severity = "medium" if any_material else "low"

    if not physical:
        stakeholders.discard("medical_services")
    if not active:
        stakeholders.discard("malaysia_emergency_services_999")
        stakeholders.discard("fire_and_rescue")
    if not (
        parent_mentioned or (physical and physical_minor) or safeguarding
        or (food_incident and physical_minor)
    ):
        stakeholders.discard("guardian")
    if not (regulatory or urgent):
        stakeholders.discard("education_authority")
    if not public:
        stakeholders.discard("public_media")
    return family, severity, keep, stakeholders


def _unsupported_claim_without_evidence_legacy(text: str) -> bool:
    """High-precision evidence contradiction floor.

    Missing-evidence and claimed-result cues must occur in the same logical
    segment. Contrast words split unrelated facts, and explicit verified or
    measured support defeats the floor. The semantic interpreter still
    handles wider paraphrases; this function protects the obvious cases if a
    concept flag is omitted.
    """
    missing = re.compile(
        r"\b(?:no|without)\s+(?:(?:outcome|impact|baseline|supporting|relevant|"
        r"measurement)\s+)?(?:data|evidence|measurements?|results?)\s+"
        r"(?:(?:has|have|had|was|were|is|are)\s+)?(?:been\s+)?"
        r"(?:collected|measured|gathered|available)\b|"
        r"\b(?:data|evidence|measurements?|results?)\s+"
        r"(?:has|have|had|was|were|is|are)\s+not\s+(?:been\s+)?"
        r"(?:collected|measured|gathered|available)\b|"
        r"\b(?:did|do|does|have|has|had)\s+not\s+(?:collect|collected|measure|"
        r"measured|gather|gathered|record|recorded)\s+(?:(?:any|the)\s+)?"
        r"(?:data|evidence|measurements?|results?|it)\b|"
        r"\bno\s+(?:survey|measurement|assessment|audit)\s+"
        r"(?:was|has\s+been|had\s+been)\s+(?:conducted|completed|done)\b|"
        r"\b(?:do|does|did|have|has|had)\s+not\s+have\s+(?:any\s+)?"
        r"(?:data|evidence|results?)\b|"
        r"\b(?:data|evidence|results?)\s+(?:is|are|was|were)\s+unavailable\b|"
        r"\b(?:the\s+)?(?:number|figure|percentage|result)\s+"
        r"(?:is|was)\s+(?:made\s+up|fabricated|invented)\b|"
        r"(?:尚未|还未|還未)(?:收集|蒐集|采集|测量|測量)(?:成效|结果|結果)?数据|"
        r"(?:没有|沒有|无|無)(?:已收集|已测量|已測量|成效|结果|結果)数据"
    )
    claim = re.compile(
        r"\b(?:\d+(?:\.\d+)?\s*%|percent(?:age)?|improv(?:e|ed|ement)|"
        r"increas(?:e|ed)|decreas(?:e|ed)|claim|official\s+report|"
        r"make\s+it\s+(?:sound\s+)?official)\b|百分之|改善|提升|正式报告|正式報告"
    )
    supported = re.compile(
        r"\b(?:based\s+on|according\s+to|supported\s+by)\s+(?:the\s+)?"
        r"(?:verified|collected|measured|audited|completed)\b|"
        r"\b(?:verified|collected|measured|audited)\s+"
        r"(?:data|evidence|results?|survey|audit)\b|"
        r"\b(?:data|evidence|results?)\s+(?:shows?|confirms?|supports?)\b"
    )
    segments = re.split(
        r"\b(?:but|however|whereas|while)\b|[;；\n]+",
        (text or "").casefold(),
    )
    return any(
        missing.search(segment)
        and claim.search(segment)
        and not supported.search(segment)
        for segment in segments
    )


def _unsupported_claim_without_evidence(text: str) -> bool:
    """Detect an asserted result beside a bounded, explicit evidence gap.

    The contradiction may span adjacent sentences, semicolons or contrast
    words, so punctuation is not a safety boundary. Explicitly verified
    support and negative instructions ("do not claim") remain safe.
    """
    value = (text or "").casefold()
    missing = re.compile(
        r"\b(?:no|without)\s+(?:(?:outcome|impact|baseline|supporting|relevant|"
        r"measurement)\s+)?(?:data|evidence|measurements?|results?)"
        r"(?:\s+(?:(?:has|have|had|was|were|is|are)\s+)?(?:been\s+)?"
        r"(?:collected|measured|gathered|available))?\b|"
        r"\b(?:data|evidence|measurements?|results?)\s+"
        r"(?:has|have|had|was|were|is|are)\s+not\s+(?:been\s+)?"
        r"(?:collected|measured|gathered|available)\b|"
        r"\b(?:did|do|does|have|has|had)\s+not\s+(?:collect(?:ed)?|"
        r"measure(?:d)?|gather(?:ed)?|record(?:ed)?)\s+(?:(?:any|the)\s+)?"
        r"(?:data|evidence|measurements?|"
        r"results?|it)\b|"
        r"\b(?:haven't|hasn't|hadn't|don't|doesn't|didn't)\s+(?:collect|"
        r"collected|measure|measured|gather|gathered|record|recorded|have)\s+"
        r"(?:(?:any|the)\s+)?(?:data|evidence|measurements?|results?|it)\b|"
        r"\bno\s+(?:survey|measurement|assessment|audit)\s+"
        r"(?:was|has\s+been|had\s+been)\s+(?:conducted|completed|done)\b|"
        r"\b(?:did|do|does)\s+not\s+(?:run|conduct|complete)\s+"
        r"(?:a\s+|an\s+|the\s+)?(?:survey|measurement|assessment|audit)\b|"
        r"\b(?:do|does|did|have|has|had)\s+not\s+have\s+(?:any\s+)?"
        r"(?:data|evidence|results?)\b|"
        r"\b(?:data|evidence|results?)\s+(?:is|are|was|were)\s+unavailable\b|"
        r"\b(?:the\s+)?(?:number|figure|percentage|result)\s+"
        r"(?:is|was)\s+(?:made\s+up|fabricated|invented)\b"
    )
    claim = re.compile(
        r"\b(?:\d+(?:\.\d+)?\s*%|percent(?:age)?|improv(?:ed|ement)|"
        r"increas(?:ed|e)|decreas(?:ed|e)|make\s+it\s+(?:sound\s+)?official)\b"
    )
    supported = re.compile(
        r"\b(?:based\s+on|according\s+to|supported\s+by)\s+(?:the\s+)?"
        r"(?:verified|collected|measured|audited|completed)\b|"
        r"\b(?:verified|collected|measured|audited|completed)\s+"
        r"(?:outcome\s+)?(?:data|evidence|results?|survey|audit|measurement)\b|"
        r"\b(?:data|evidence|results?)\s+(?:shows?|confirms?|supports?)\b"
    )
    gaps = list(missing.finditer(value))
    claims = list(claim.finditer(value))
    for gap in gaps:
        for asserted in claims:
            distance = min(
                abs(gap.start() - asserted.end()),
                abs(asserted.start() - gap.end()),
            )
            if distance > 420:
                continue
            prefix = value[max(0, asserted.start() - 80):asserted.start()]
            if re.search(
                r"\b(?:do\s+not|don't|cannot|can't|must\s+not|should\s+not|"
                r"never|avoid)\b"
                r"[^.!?;]{0,35}\b(?:claim|state|say|report|assert|present)?\s*$|"
                r"\bwithout\s+(?:claiming|stating|reporting|asserting|presenting)\b"
                r"[^.!?;]{0,25}$|\b(?:whether|determine\s+if|assess\s+if|"
                r"measure\s+whether)\b[^.!?;]{0,35}$",
                prefix,
            ):
                continue
            window_start = max(0, min(gap.start(), asserted.start()) - 100)
            window_end = min(len(value), max(gap.end(), asserted.end()) + 180)
            if supported.search(value[window_start:window_end]):
                continue
            return True
    return False


def _socioeconomic_differential_harm(text: str, concepts: set[str]) -> bool:
    """Detect status-based punitive/surveillance treatment, not support."""
    value = (text or "").casefold()
    status = re.compile(
        r"\b(?:family|household|socioeconomic|social|financial)\s+"
        r"(?:background|income|status|standing)|\bparents?['’]?\s+income\b|"
        r"\b(?:poor|low[- ]income|impoverished|b40)\s+(?:family|families|"
        r"household|households|parents?|students?|pupils?|children)\b|"
        r"\b(?:family|household|parents?|students?|pupils?|children)\s+"
        r"(?:is\s+|are\s+|comes?\s+from\s+)?poor\b|"
        r"\b(?:mother|father|parent|guardian|family|household)\b"
        r"[^.!?;]{0,45}\b(?:receives?|on)\s+(?:welfare|benefits?)\b|"
        r"\b(?:lives?|living|resident)\b[^.!?;]{0,35}"
        r"\b(?:ppr|public\s+housing)\b|"
        r"\b(?:ppr|welfare\s+(?:recipient|family)|public\s+housing)\b"
    )
    safe_principle = bool(re.search(
        r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|cannot)\b"
        r"[^.!?;]{0,100}\b(?:use|monitor|watch|flag|label|classify|punish|"
        r"discipline|penali[sz]e|treat|affect|determine)\b[^.!?;]{0,100}"
        r"\b(?:family|household|socioeconomic|social|financial|poor|"
        r"low[- ]income|b40)\b|"
        r"\b(?:family|household|socioeconomic|social|financial)\s+"
        r"(?:background|income|status)\b[^.!?;]{0,80}\b(?:must\s+not|"
        r"should\s+not|cannot|never)\s+(?:affect|determine|justify)\b",
        value,
    ))
    if safe_principle:
        return False
    harmful_action = re.compile(
        r"\b(?:monitor(?:ed|ing)?|watch(?:ed|ing)?|surveil(?:led|ling)?|"
        r"track(?:ed|ing)?)\b[^.!?;]{0,60}\b"
        r"(?:closely|more|extra|strictly|as\s+high[- ]risk|because|based\s+on)\b|"
        r"\b(?:flag(?:ged|ging)?|label(?:led|ed|ling|ing)?|classif(?:y|ied|ying)|"
        r"mark(?:ed|ing)?)\b[^.!?;]{0,60}\b(?:high[- ]risk|"
        r"problem|troublemaker|suspicious|undesirable)\b|"
        r"\b(?:punish(?:ed|ment|ing)?|disciplin(?:e|ed|ary|ing)|"
        r"penali[sz](?:e|ed|ing)|treat(?:ed|ing)?)\b[^.!?;]{0,60}\b"
        r"(?:different(?:ly)?|harsh(?:ly)?|strict(?:ly)?|harder|more\s+"
        r"severely|because|based\s+on)\b|"
        r"\b(?:stricter|harsher|more\s+severe)\s+(?:discipline|punishment)\b|"
        r"\b(?:exclude|segregate|deny)\b[^.!?;]{0,50}\b(?:because|based\s+on|"
        r"access|opportunity|participation)\b|"
        r"\bkeep\b[^.!?;]{0,30}\b(?:a\s+)?closer\s+eye\s+on\b"
    )
    status_matches = list(status.finditer(value))
    for harm in harmful_action.finditer(value):
        prefix = value[max(0, harm.start() - 45):harm.start()]
        if re.search(
            r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|cannot)\b",
            prefix,
        ):
            continue
        if any(
            min(abs(harm.start() - item.end()), abs(item.start() - harm.end())) <= 220
            for item in status_matches
        ):
            return True
    return bool(
        {"socioeconomic_data", "differential_treatment"}.issubset(concepts)
        and not safe_principle
    )


_EXTERNAL_RECIPIENT_PATTERNS = {
    "guardian": r"\b(?:parents?|guardians?|family)\b|家长|家長|监护人|監護人|ibu bapa|penjaga",
    "education_authority": (
        r"\b(?:district\s+education\s+office|education\s+authority|"
        r"education\s+office|district\s+office|ministry\s+(?:portal|of\s+education)|"
        r"ppd|jpn|moe)\b|教育局|教育部"
    ),
    "medical_services": r"\b(?:hospital|medical\s+services?|clinic|doctor)\b|医院|醫院|诊所|診所",
    "malaysia_emergency_services_999": r"\b(?:999|emergency\s+services?)\b",
    "fire_and_rescue": r"\b(?:fire\s+and\s+rescue|fire\s+department|bomba)\b|消防",
    "police": r"\bpolice\b|警方|警察|polis",
    "local_authority": r"\b(?:local\s+authority|municipal(?:ity)?|council)\b|地方政府|市议会|市議會",
    "event_organizer": r"\b(?:event\s+organizer|event\s+organiser|organizer|organiser|guest)\b",
    "vendor": r"\b(?:vendor|supplier|contractor)\b|供应商|供應商|pembekal",
    "transport_provider": r"\b(?:transport\s+provider|bus\s+operator|bus\s+company)\b",
    "public_media": r"\b(?:facebook|public|publicly|media|website|social\s+media)\b|公开|公開|脸书|臉書",
    "school_community": r"\b(?:all\s+parents|parent\s+group|school\s+community|all\s+staff)\b|全体家长|全體家長|家长群|家長群",
}
_RELEASE_ACTION_PATTERN = re.compile(
    r"\b(?:send|publish|submit|release|email|contact|notify|call|forward|"
    r"share|upload|post)\b|\bmessage\s+(?:the\s+)?(?:parents?|guardians?|"
    r"family|office|authority|hospital|police|organizer|organiser|vendor)\b|"
    r"发送|發送|发布|發佈|提交|联系|聯絡|通知|转发|轉發|上传|上傳|分享"
)
_RELEASE_NEGATION_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|never|not\s+to)\b|"
    r"\b(?:draft\s+only|nothing\s+should\s+be\s+sent)\b|"
    r"不要|不得|禁止|只(?:做|要)草稿"
)


def _release_clauses(text: str) -> tuple[list[str], list[str]]:
    value = (text or "").casefold()
    chunks = [
        chunk.strip(" ,:")
        for chunk in re.split(
            r"[.!?;；。！？\n]+|\b(?:but|however|instead|then)\b",
            value,
        )
        if chunk.strip(" ,:")
    ]
    positive: list[str] = []
    negative: list[str] = []
    for chunk in chunks:
        action_text = re.sub(
            r"\b(?:contact|call|message|email)\s+(?:scripts?|drafts?|templates?)\b",
            "",
            chunk,
        )
        if not _RELEASE_ACTION_PATTERN.search(action_text):
            continue
        conditional_approval = bool(re.search(
            r"\b(?:without|until|unless|only\s+after)\b[^.!?;]{0,60}"
            r"\b(?:approval|approved|authorisation|authorization|review)\b",
            chunk,
        ))
        if _RELEASE_NEGATION_PATTERN.search(chunk) and not conditional_approval:
            negative.append(chunk)
        else:
            positive.append(chunk)
    return positive, negative


def _recipients_in_release_text(value: str) -> set[str]:
    recipients = {
        recipient
        for recipient, pattern in _EXTERNAL_RECIPIENT_PATTERNS.items()
        if re.search(pattern, value)
    }
    if re.search(r"\b(?:publish|post)\b|发布|發佈", value):
        recipients.add("public_media")
    return recipients


def _release_is_negated(text: str) -> bool:
    return _shared_release_is_negated(text)


def _negated_external_recipients(text: str) -> set[str]:
    return _shared_negated_external_recipients(text)


def _infer_explicit_external_recipients(
    text: str,
    *,
    requested_action: str,
    requested_audience: str,
    requested_outputs: list[dict],
) -> set[str]:
    """Conservatively recover an explicit release recipient.

    Semantic intake remains the primary interpreter.  This deterministic
    backstop exists only for the authorisation boundary: if the user plainly
    says to send/contact/submit/publish and the model omits the structured
    external-action field, the human gate must still be present.  It never
    creates an external action from a mere request to *draft* content.
    """
    return _shared_infer_external_recipients(
        text,
        requested_audience=requested_audience,
        requested_outputs=requested_outputs,
    )


def _source_requested_output_contracts(text: str) -> tuple[list[dict], list[dict]]:
    """Recover explicitly named outputs from first-party source text.

    This is a provider-outage coverage floor, not an open-ended intent
    classifier. It maps high-precision phrases to the closed catalog and sends
    unmistakable uncatalogued school documents through the existing governed
    custom-deliverable path. A semantic model may add detail but cannot erase
    these source-named outputs.
    """
    source = str(text or "")
    low = source.casefold()
    outputs: list[dict] = []
    custom: list[dict] = []
    seen_roles: set[str] = set()
    seen_labels: set[str] = set()

    def add_role(
        role: str,
        *,
        audience: str = "internal",
        recipient_type: str = "school_staff",
    ) -> None:
        if role in seen_roles:
            return
        seen_roles.add(role)
        outputs.append({
            "artifact_role": role,
            "audience": audience,
            "recipient_type": recipient_type,
        })

    def add_custom(label: str, template_key: str) -> None:
        key = label.casefold()
        if key in seen_labels:
            return
        seen_labels.add(key)
        custom.append({
            "label": label,
            "audience": "internal",
            "recipient_type": "school_staff",
            "mode": "draft",
            "template_key": template_key,
        })

    if re.search(
        r"\b(?:internal\s+)?(?:incident|accident)\s+report\b|"
        r"\blaporan\s+(?:dalaman\s+)?(?:insiden|kemalangan)\b|"
        r"(?:内部|內部)?(?:事故|意外)(?:报告|報告)", low,
    ):
        add_role("internal_incident_report", recipient_type="school_leadership")
    if re.search(
        r"\b(?:evidence(?:[- ]preservation)?|chain[- ]of[- ]custody)\s+"
        r"(?:log|record)\b|\blog\s+(?:bukti|keterangan)\b|"
        r"(?:证据|證據)(?:保存|保全)?(?:记录|紀錄|日志|日誌)", low,
    ):
        add_role("evidence_preservation_log", recipient_type="school_leadership")
    if re.search(
        r"\b(?:staff|teacher)\s+(?:internal\s+)?notice\b|"
        r"\bnotis\s+(?:dalaman\s+)?(?:staf|guru)\b|"
        r"(?:职员|職員|教师|教師)(?:内部|內部)?通知", low,
    ):
        add_role("staff_internal_notice")
    if re.search(
        r"\b(?:classroom|student|pupil|learning|academic)\s+support\s+plan\b|"
        r"\b(?:support|intervention)\s+plan\b|\bpelan\s+sokongan\b|"
        r"(?:学习|學習|学生|學生)(?:支持|支援)(?:计划|計劃)", low,
    ):
        add_role("student_support_plan")
    if re.search(
        r"\b(?:event|school|internal)\s+action\s+plan\b|"
        r"\bpelan\s+tindakan\b|(?:活动|活動|校内|校內)(?:行动|行動)(?:计划|計劃)",
        low,
    ) and re.search(
        r"\b(?:event|bazaar|fundrais|charity|recycling\s+day|ceremony|competition)\b|"
        r"\b(?:acara|jualan\s+amal|hari\s+kitar\s+semula)\b|义卖|義賣|活动|活動",
        low,
    ):
        add_role("event_action_plan")
    if re.search(
        r"\b(?:education\s+authority|district\s+education|ppd|jpn)\s+"
        r"(?:report|request)\b|(?:教育局|教育部)(?:报告|報告|申请|申請)", low,
    ):
        role = (
            "education_authority_request"
            if re.search(r"\brequest|support\b|申请|申請", low)
            else "education_authority_report"
        )
        add_role(role, audience="external_agency", recipient_type="education_authority")
    if re.search(
        r"\b(?:public|media)\s+(?:holding\s+)?statement\b|"
        r"\b(?:facebook|public)\s+(?:post|notice)\b|公开声明|公開聲明", low,
    ):
        add_role("public_communication_draft", audience="public", recipient_type="public_media")

    parent_document = bool(re.search(
        r"\bparent(?:s|al)?\s+(?:notice|notification|update|message|draft|letter)\b|"
        r"\b(?:notice|notification|update|message|letter)\s+(?:draft\s+)?"
        r"(?:to|for)\s+parents?\b|\b(?:makluman|notis|mesej)\s+"
        r"(?:kepada\s+)?(?:ibu\s+bapa|penjaga)\b|"
        r"(?:家长|家長)(?:通知|消息|信息|信函)|"
        r"(?:通知|消息|信息|信函)(?:给|給)?(?:家长|家長)", low,
    ))
    if parent_document:
        broad = bool(re.search(
            r"\b(?:all|every)\s+parents?\b|\bparents\b|"
            r"\bparent\s+(?:group|community)\b|\b(?:all|whole)\s+school\b|"
            r"\b(?:semua|seluruh)\s+ibu\s+bapa\b|"
            r"全体家长|全體家長|家长群|家長群|全校", low,
        )) or bool(re.search(
            r"\b(?:event|bazaar|charity|recycling\s+day|facility|classroom|relocation)\b|"
            r"义卖|義賣|活动|活動|课室|課室", low,
        ))
        add_role(
            "school_parent_notice" if broad else "private_parent_notice",
            audience="school_community" if broad else "private_recipient",
            recipient_type="school_community" if broad else "guardian",
        )

    custom_patterns = (
        (r"\bteacher\s+observation\s+(?:template|form|sheet)\b|"
         r"\bobservation\s+(?:template|form)\b|教师观察表|教師觀察表",
         "Teacher observation template", "teacher_observation"),
        (r"\b(?:stock|inventory)[- ]control\s+(?:sheet|log|table)\b|"
         r"\b(?:stock|inventory)\s+(?:sheet|log)\b|库存控制表|庫存控制表",
         "Stock-control sheet", "stock_control"),
        (r"\b(?:class(?:room)?\s+)?relocation\s+plan\b|课室调迁计划|課室調遷計劃",
         "Class relocation plan", "relocation_plan"),
        (r"\bconfidential\s+intake\s+(?:note|form|record)\b|保密受理记录|保密受理紀錄",
         "Confidential intake note", "confidential_intake"),
        (r"\binvestigation\s+plan\b|调查计划|調查計劃",
         "Confidential investigation plan", "investigation_plan"),
        (r"\bmeeting\s+agenda\b|会议议程|會議議程",
         "Meeting agenda", "meeting_agenda"),
    )
    for pattern, label, template_key in custom_patterns:
        if re.search(pattern, low):
            add_custom(label, template_key)
    return outputs, custom


def _official_record_mutation_requested(text: str) -> bool:
    value = str(text or "").casefold()
    if re.search(r"\b(?:draft|prepare|write)\s+(?:a\s+)?(?:request|proposal)\b", value):
        return False
    record = bool(re.search(
        r"\b(?:attendance|student|pupil|official|enrolment|enrollment|grade)\s+record\b|"
        r"\b(?:attendance|grade|mark)\s+(?:entry|status)\b|"
        r"\brekod\s+(?:kehadiran|murid|rasmi)\b|(?:考勤|出勤|学生|學生|官方)(?:记录|紀錄)",
        value,
    ))
    mutation = bool(re.search(
        r"\b(?:change|update|edit|correct|amend|modify|set|mark)\b|"
        r"\b(?:ubah|kemas\s+kini|betulkan|pinda)\b|更改|更新|修改|改正",
        value,
    ))
    return record and mutation


class SchoolSituationCompiler:
    """Compile open school input into a closed, non-authorising situation."""

    def __init__(self, policy_path: str | Path) -> None:
        self.policy_path = Path(policy_path)
        self.policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        self.families = set(self.policy.get("families") or [])
        self.phases = set(self.policy.get("phases") or [])
        self.severities = set(self.policy.get("severities") or [])
        self.allowed_signals = set(self.policy.get("signals") or [])
        self.allowed_stakeholders = set(self.policy.get("stakeholders") or [])
        self.catalog = self.policy.get("artifact_catalog") or {}

    def compile(
        self,
        text: str,
        semantics: dict,
        *,
        clarification_answers: dict | None = None,
        selected_deliverable_ids: list[str] | None = None,
        custom_deliverables: list[dict] | None = None,
    ) -> dict:
        if semantics.get("school_domain") is not True:
            return {"active": False, "reason": "outside_school_domain"}

        suggested = semantics.get("situation") or {}
        family = str(suggested.get("family") or "").strip().lower()
        phase = str(suggested.get("phase") or "").strip().lower()
        severity = str(suggested.get("severity") or "").strip().lower()
        signals = {
            str(item).strip().lower()
            for item in (suggested.get("signals") or [])
            if str(item).strip().lower() in self.allowed_signals
        }
        stakeholders = {
            str(item).strip().lower()
            for item in (suggested.get("stakeholder_candidates") or [])
            if str(item).strip().lower() in self.allowed_stakeholders
        }
        affected_people = self._closed_list(
            suggested.get("affected_people_types"),
            {"student", "staff", "guardian", "visitor", "unknown"},
        )
        lower_text = (text or "").casefold()
        if _contains_any(lower_text, (
            "student", "pupil", "child", "teenager", "learner", "murid",
            "pelajar", "kanak-kanak", "学生", "學生", "孩子", "儿童", "兒童",
        )) and "student" not in affected_people:
            affected_people.append("student")
        if _contains_any(lower_text, (
            "teacher", "staff", "employee", "guru", "kakitangan", "教师", "教職員",
        )) and "staff" not in affected_people:
            affected_people.append("staff")
        secondary_families = self._closed_list(
            suggested.get("secondary_families"), self.families
        )

        family, phase, severity, signals, stakeholders = self._safe_fallbacks(
            text, semantics, family, phase, severity, signals, stakeholders,
            affected_people=affected_people,
            lexical_fallback=not (
                semantics.get("checked") is True and bool(suggested)
            ),
        )
        family, severity, signals, stakeholders = _corroborate_semantic_impact(
            text, family, severity, signals, stakeholders,
        )
        answers = {
            str(k)[:80]: str(v)[:120]
            for k, v in (clarification_answers or {}).items()
        }
        if answers.get("immediate_danger"):
            answer = answers["immediate_danger"].casefold()
            if answer in {"yes", "active", "ongoing", "true"}:
                signals.add("active_danger")
                severity = "critical"
                phase = "ongoing"
            elif answer in {"no", "contained", "false"}:
                signals.discard("active_danger")
                if phase == "ongoing":
                    phase = "just_occurred"
            elif answer in {"unknown", "tbc", "unsure"}:
                signals.add("external_help_may_be_required")
                severity = "critical"

        # Deterministic floors: the interpreter can miss a relationship, but
        # a confirmed injury/minor/external-help signal has stable implications.
        if "injury_or_illness" in signals:
            stakeholders.add("medical_services")
            raw_physical_incident, raw_minor_incident = _physical_incident_flags(text)
            if (
                "minor_involved" in signals
                or raw_minor_incident
                or ("student" in affected_people and not raw_physical_incident)
            ):
                signals.add("minor_involved")
                stakeholders.add("guardian")
            if severity == "unknown":
                severity = "medium"
        if "active_danger" in signals:
            signals.add("external_help_may_be_required")
            stakeholders.add("malaysia_emergency_services_999")
            severity = "critical"
            phase = "ongoing"
        source_life_unknown = _life_safety_status_unknown(text)
        if source_life_unknown and (
            "injury_or_illness" in signals
            or "safeguarding_concern" in signals
            or "person_missing" in signals
        ):
            # Uncertainty is not proof of an emergency.  Raise only to the
            # one-question review boundary; the human answer decides whether
            # active-danger coverage is added.
            signals.add("external_help_may_be_required")
            if severity not in {"critical", "high"}:
                severity = "high"
        if severity in {"critical", "high"}:
            signals.add("possible_regulatory_trigger")
            stakeholders.update({"school_leadership", "education_authority"})

        safe_unknowns = self._safe_unknowns(suggested.get("unknowns"))
        if source_life_unknown and not any(
            item.get("impact") == "life_safety" for item in safe_unknowns
        ):
            safe_unknowns.append({
                "fact_id": "urgent_help_or_danger_still_present",
                "impact": "life_safety",
            })

        situation = {
            "schema_version": "1.0",
            "active": True,
            "family": family,
            "phase": phase,
            "severity": severity,
            "signals": sorted(signals),
            "affected_people_types": affected_people or (
                ["student"] if "minor_involved" in signals else ["unknown"]
            ),
            "secondary_families": [
                item for item in secondary_families if item != family
            ],
            "stakeholder_candidates": sorted(stakeholders),
            # Judge-visible metadata must remain first-party.  A semantic LLM
            # may classify or paraphrase the case, but its prose is not source
            # evidence and must never replace the actual user request in UI or
            # audit metadata.
            "case_summary": str(text or "School administration request.").strip()[:300],
            # Immutable source contract: generation and repair always retain the
            # actual request instead of reconstructing it from a family label.
            "source_request": str(text or "").strip()[:4000],
            "requested_action": str(semantics.get("requested_action") or "")[:80],
            "requested_audience": str(semantics.get("audience") or "unknown")[:40],
            "data_use_concepts": sorted({
                str(item).strip().lower()
                for item in (semantics.get("data_use_concepts") or [])
                if str(item).strip()
            }),
            "known_facts": self._safe_fact_list(
                suggested.get("known_facts"), source_text=text,
            ),
            "unknowns": safe_unknowns,
            "requested_deliverables": self._closed_list(
                suggested.get("requested_deliverables"), set(self.catalog)
            ),
            "requested_outputs": [
                {
                    **deepcopy(item),
                    "artifact_role": str(
                        item.get("artifact_role") or item.get("role") or ""
                    ).strip().lower(),
                }
                for item in (
                    suggested.get("requested_outputs")
                    or suggested.get("requested_output_specs")
                    or []
                )[:8]
                if isinstance(item, dict)
                and str(
                    item.get("artifact_role") or item.get("role") or ""
                ).strip().lower() in self.catalog
            ],
            "explicit_external_actions": self._closed_list(
                suggested.get("explicit_external_actions"), _EXTERNAL_RECIPIENTS
            ),
            "compiler_source": semantics.get("source", "unknown"),
            "governance_note": (
                "Situation labels propose coverage only; they never authorise an action."
            ),
        }
        source_outputs, inferred_custom_deliverables = (
            _source_requested_output_contracts(text)
        )
        existing_output_roles = {
            str(item.get("artifact_role") or "").strip().lower()
            for item in situation["requested_outputs"]
            if isinstance(item, dict)
        }
        for output in source_outputs:
            role = str(output.get("artifact_role") or "").strip().lower()
            if role and role not in existing_output_roles:
                situation["requested_outputs"].append(output)
                existing_output_roles.add(role)
        effective_custom_deliverables = list(custom_deliverables or [])
        existing_custom_labels = {
            str(item.get("label") or "").strip().casefold()
            for item in effective_custom_deliverables
            if isinstance(item, dict)
        }
        for item in inferred_custom_deliverables:
            label_key = str(item.get("label") or "").strip().casefold()
            if label_key and label_key not in existing_custom_labels:
                effective_custom_deliverables.append(item)
                existing_custom_labels.add(label_key)
        inferred_external = _infer_explicit_external_recipients(
            text,
            requested_action=situation["requested_action"],
            requested_audience=situation["requested_audience"],
            requested_outputs=situation["requested_outputs"],
        )
        negated_external = _negated_external_recipients(text)
        if _release_is_negated(text):
            # The user's explicit non-release instruction is stronger than a
            # mistaken semantic suggestion or a live planner send proposal.
            situation["explicit_external_actions"] = []
        else:
            # External action is authority-bearing, not merely descriptive.
            # The semantic LLM's list is therefore never sufficient on its
            # own: only recipients recovered from an explicit source-text
            # release clause may create a GREEN human gate.
            situation["explicit_external_actions"] = sorted(
                inferred_external - negated_external
            )
        # A strong release verb with no material recipient is not guessed.
        # One human answer resolves only the recipient boundary; the chosen
        # external action still remains GREEN and requires approval later.
        if answers.get("external_recipient"):
            answer = answers["external_recipient"].strip().casefold()
            recipients = set(situation["explicit_external_actions"])
            recipients.discard("external_stakeholder")
            if re.search(
                r"\b(?:draft\s+only|do\s+not\s+send|don't\s+send|none|"
                r"internal\s+only|school\s+staff)\b|不要发送|不要發送|只做草稿|"
                r"jangan\s+hantar|draf\s+sahaja",
                answer,
            ):
                pass
            elif re.search(
                r"\b(?:parent|guardian|family|ibu\s+bapa|penjaga)\b|家长|家長|监护人|監護人",
                answer,
            ):
                recipients.add("guardian")
            elif re.search(
                r"\b(?:district\s+education|education\s+(?:office|authority)|"
                r"ppd|jpn|moe)\b|教育局|教育部",
                answer,
            ):
                recipients.add("education_authority")
            elif re.search(r"\b(?:public|media|facebook)\b|公开|公開|脸书|臉書", answer):
                recipients.add("public_media")
            elif re.search(
                r"\b(?:all\s+parents|parent\s+group|school\s+community|"
                r"semua\s+ibu\s+bapa)\b|家长群|家長群|全体家长|全體家長",
                answer,
            ):
                recipients.add("school_community")
            else:
                # The free-text answer may identify a rare recipient not in the
                # closed list. Keep it generic and governed rather than expand
                # policy vocabulary from user text.
                recipients.add("external_stakeholder")
            situation["explicit_external_actions"] = sorted(recipients)
        response_pack = self._build_pack(
            text,
            situation,
            selected_deliverable_ids=selected_deliverable_ids,
            custom_deliverables=effective_custom_deliverables,
        )
        degraded = bool(
            semantics.get("checked") is not True
            or str(semantics.get("source") or "").startswith("fallback")
        )
        response_pack["degraded_mode"] = degraded
        if degraded:
            response_pack["degraded_message"] = (
                "Semantic API classification was unavailable. This is a conservative "
                "generic pack; confirm any school-specific or cross-domain outputs."
            )
        response_pack["critical_question"] = self._critical_question(situation, answers)
        response_pack["state"] = (
            "needs_clarification" if response_pack["critical_question"] else "ready"
        )
        return {"situation": situation, "response_pack": response_pack}

    @staticmethod
    def _closed_list(value: Any, allowed: set[str]) -> list[str]:
        return sorted({
            str(item).strip().lower()
            for item in (value or [])
            if str(item).strip().lower() in allowed
        })

    @staticmethod
    def _safe_fact_list(value: Any, *, source_text: str = "") -> list[dict]:
        source_norm = re.sub(r"\s+", " ", str(source_text or "")).strip().casefold()
        source_segments = [
            segment.strip()
            for segment in re.split(r"[.!?;；。！？\n]+", source_norm)
            if segment.strip()
        ]
        negation = re.compile(
            r"\b(?:no|not|never|without|neither|nor|cannot|can't|didn't|"
            r"doesn't|isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|"
            r"tidak|tiada|belum|jangan)\b|没有|沒有|尚未|还未|還未|不得|不要"
        )
        stopwords = {
            "a", "an", "the", "is", "was", "were", "are", "to", "of",
            "in", "on", "at", "and", "or", "for", "with", "this", "that",
            "school", "student", "students", "pupil", "child",
        }
        out: list[dict] = []
        for item in (value or [])[:20]:
            if not isinstance(item, dict):
                continue
            key = re.sub(r"[^a-z0-9_]+", "_", str(item.get("fact_id") or "fact").lower())[:80]
            fact_value = re.sub(
                r"\s+", " ", str(item.get("value") or "")
            ).strip()[:160]
            if not fact_value or not source_norm:
                continue
            fact_norm = fact_value.casefold()
            meaningful = {
                token for token in re.findall(
                    r"[^\W_]+", fact_norm, flags=re.UNICODE
                )
                if token not in stopwords and len(token) > 1
            }
            fact_negative = bool(negation.search(fact_norm))
            grounded = False
            for segment in source_segments:
                segment_tokens = set(re.findall(
                    r"[^\W_]+", segment, flags=re.UNICODE
                ))
                lexical_support = bool(
                    fact_norm in segment
                    or (meaningful and meaningful.issubset(segment_tokens))
                )
                if not lexical_support:
                    continue
                # Bag-of-words overlap cannot turn "not injured" into
                # "injured", "not contacted" into "contacted", or invert
                # closure/breach assertions. Polarity must agree in the same
                # source clause.
                if fact_negative == bool(negation.search(segment)):
                    grounded = True
                    break
            if not grounded:
                continue
            status = str(item.get("status") or "reported").lower()
            if status not in {"reported", "confirmed", "unverified", "unknown"}:
                status = "reported"
            # A semantic extractor cannot independently confirm a fact.  Keep
            # its source polarity conservative even when it returns
            # ``confirmed``.
            if status == "confirmed":
                status = "reported"
            out.append({
                "fact_id": key or "fact",
                "value": fact_value,
                "status": status,
                "source_type": "semantic_extraction_grounded_in_user_request",
            })
        return out

    @staticmethod
    def _safe_unknowns(value: Any) -> list[dict]:
        out: list[dict] = []
        for item in (value or [])[:12]:
            if not isinstance(item, dict):
                continue
            fact_id = re.sub(
                r"[^a-z0-9_]+", "_", str(item.get("fact_id") or "unknown").lower()
            )[:80]
            impact = str(item.get("impact") or "content_only").lower()
            if impact not in {
                "life_safety", "governance_boundary", "required_deliverables",
                "external_recipient", "content_only",
            }:
                impact = "content_only"
            out.append({"fact_id": fact_id or "unknown", "impact": impact})
        return out

    def _safe_fallbacks(
        self,
        text: str,
        semantics: dict,
        family: str,
        phase: str,
        severity: str,
        signals: set[str],
        stakeholders: set[str],
        affected_people: list[str] | set[str] | None = None,
        lexical_fallback: bool = True,
    ) -> tuple[str, str, str, set[str], set[str]]:
        area = str(semantics.get("school_area") or "other")
        if family not in self.families:
            family = {
                "health": "health_medical",
                "student_support": "teaching_learning_support",
                "discipline": "discipline_behaviour",
                "attendance": "attendance_student_movement",
                "parent_communication": "communications_reputation",
                "school_event": "events_cocurricular",
                "fundraising_finance": "finance_procurement",
                "public_communication": "communications_reputation",
                "official_records": "records_regulatory",
            }.get(area, "general_school_admin")
        if phase not in self.phases:
            phase = "follow_up" if semantics.get("case_relation") == "follow_up" else "unknown"
        if severity not in self.severities:
            severity = "unknown"

        low = (text or "").casefold()
        people = {str(item).lower() for item in (affected_people or [])}
        planned_or_training = bool(re.search(
            r"\b(?:drill|exercise|awareness|campaign|lesson|training|"
            r"prevention|simulation|tabletop|workshop|talk|next\s+week|"
            r"scheduled)\b|演习|演習|训练|訓練|宣传|宣傳|预防|預防|"
            r"latihan|kempen|kesedaran|simulasi",
            low,
        ))
        # Safety floors run even when semantic intake succeeded. They use
        # incident grammar, not loose topic words, and can only add coverage or
        # raise severity; they never authorise an action.
        incident_injury = bool(re.search(
            r"\b(?:student|pupil|child|teacher|staff|visitor|person|murid|pelajar)\b"
            r"[^.!?\n]{0,100}\b(?:injured|hurt|bitten|bit|bites|bleeding|"
            r"unconscious|poisoned|collapsed|fainted|faints|burned|burnt|scalded|"
            r"cedera|digigit|pengsan|keracunan|had\s+an?\s+asthma\s+attack|"
            r"suffered\s+(?:an?\s+)?seizure|hit\s+by\s+(?:a\s+)?(?:car|vehicle)|"
            r"struck\s+by\s+(?:a\s+)?(?:car|vehicle)|fell[^.!?\n]{0,50}"
            r"(?:broke|fractured)\s+(?:an?\s+)?(?:arm|leg|wrist|ankle)|"
            r"(?:broke|fractured)\s+(?:an?\s+)?(?:arm|leg|wrist|ankle)|"
            r"snake\s+bite)\b|"
            r"\b(?:injured|hurt|bitten|bit|bites|bleeding|unconscious|poisoned|"
            r"collapsed|fainted|burned|burnt|scalded|cedera|digigit|pengsan|"
            r"keracunan|seizure)\b[^.!?\n]{0,50}"
            r"\b(?:student|pupil|child|teacher|staff|visitor|person|murid|pelajar)\b|"
            r"\b(?:taken|transported|admitted)\s+to\s+(?:the\s+)?"
            r"(?:hospital|clinic)\b|(?:学生|學生|老师|老師|教师|教師|职员|職員)"
            r"[^。！？\n]{0,40}(?:受伤|受傷|咬伤|咬傷|流血|昏迷|中毒)",
            low,
        ))
        # Replace loose lexical matches above with the shared high-precision
        # physical predicate. Planning/training is handled per clause so a
        # real injury during a drill is not hidden.
        incident_injury, incident_minor = _physical_incident_flags(low)
        planned_or_training = False
        if incident_injury:
            signals.add("injury_or_illness")
            stakeholders.add("medical_services")
            raw_minor = bool(re.search(
                r"\b(?:student|pupil|child|murid|pelajar|kanak-kanak)\b|"
                r"学生|學生|孩子|儿童|兒童",
                low,
            ))
            raw_minor = incident_minor
            if raw_minor:
                signals.add("minor_involved")
                stakeholders.add("guardian")
            if family == "general_school_admin":
                family = "health_medical"
        resolved_hazard = bool(re.search(
            r"\b(?:removed|captured|contained|cleared|resolved|no\s+longer\s+"
            r"present|safe\s+now|no\s+(?:immediate|active|continuing)\s+danger|"
            r"danger\s+(?:is|was)\s+not\s+(?:present|ongoing|immediate))\b|"
            r"已(?:移走|捕获|捕獲|控制|解除)|"
            r"telah\s+(?:ditangkap|dialihkan|dikawal)",
            low,
        ))
        explicit_active_hazard = bool(re.search(
            r"\b(?:active\s+danger|immediate\s+danger|emergency|fire|smoke|"
            r"gas\s+leak|chemical\s+spill|armed\s+intruder)\b|"
            r"\b(?:snake|wild\s+animal)\b[^.!?\n]{0,100}"
            r"\b(?:still|may\s+still|currently|now|entered|on\s+(?:the\s+)?campus|"
            r"in\s+(?:the\s+)?school)\b|"
            r"\b(?:still|may\s+still|currently|now)\b[^.!?\n]{0,100}"
            r"\b(?:snake|wild\s+animal)\b|紧急|緊急|危险|危險|火灾|火災|"
            r"kecemasan|bahaya|kebakaran",
            low,
        ))
        explicit_active_hazard = _active_hazard_present(low)
        resolved_hazard = not explicit_active_hazard
        if explicit_active_hazard:
            signals.update({"active_danger", "external_help_may_be_required"})
            stakeholders.add("malaysia_emergency_services_999")
            family = "safety_emergency"
            phase = "ongoing"
            severity = "critical"
        if re.search(
            r"\b(?:missing|unaccounted[- ]for)\s+(?:student|pupil|child)\b|"
            r"\b(?:student|pupil|child)\s+(?:is\s+)?missing\b|"
            r"学生失踪|學生失蹤|murid\s+hilang|pelajar\s+hilang",
            low,
        ):
            signals.update({
                "person_missing", "safeguarding_concern",
                "external_help_may_be_required", "evacuation_accountability",
            })
            family = "safeguarding_welfare"
            severity = "critical"
        cyber_negated = bool(re.search(
            r"\bno\s+(?:data\s+breach|ransomware|phishing\s+incident)\b|"
            r"没有(?:发生)?资料泄露|沒有(?:發生)?資料洩露",
            low,
        ))
        cyber_incident_floor = bool(
            not planned_or_training
            and not cyber_negated
            and re.search(
                r"\b(?:data\s+breach|ransomware|phishing|student\s+data)\b"
                r"[^.!?\n]{0,100}\b(?:occurred|detected|received|clicked|"
                r"compromised|leaked|exposed|sent\s+to\s+the\s+wrong|attack(?:ed)?)\b|"
                r"\b(?:leaked|exposed|compromised)\b[^.!?\n]{0,80}"
                r"\b(?:student\s+data|school\s+data|account|system)\b|"
                r"资料(?:已经|已)?泄露|資料(?:已經|已)?洩露|检测到勒索|檢測到勒索",
                low,
            )
        )
        cyber_incident_floor = _actual_cyber_incident(low)
        if cyber_incident_floor:
            signals.update({
                "personal_data_involved", "evidence_preservation_needed",
                "data_security_incident",
            })
            family = "cyber_data"

        if not lexical_fallback:
            return family, phase, severity, signals, stakeholders

        # Degradation path only. Live arbitrary inputs normally receive these
        # facets from the semantic LLM; the fallback protects operation during
        # a transient API/JSON failure and never authorises an action.
        if _contains_any(low, ("bomba", "fire and rescue", "fire", "smoke", "snake", "蜂", "bomba")):
            stakeholders.add("fire_and_rescue")
        if _contains_any(low, ("missing", "abuse", "self-harm", "suicide", "陌生人", "失踪", "虐待")):
            signals.add("safeguarding_concern")
            if family == "general_school_admin":
                family = "safeguarding_welfare"
        if re.search(r"\b(?:murid|pelajar|kanak-kanak)\b[^.!?\n]{0,40}\bhilang\b", low):
            signals.update({
                "person_missing", "safeguarding_concern",
                "external_help_may_be_required", "evacuation_accountability",
            })
            family = "safeguarding_welfare"
            severity = "critical"
        if _contains_any(low, (
            "school bus", "bus crash", "transport", "bas sekolah", "kemalangan bas",
            "校车", "校車",
        )):
            signals.add("transport_operation")
            stakeholders.add("transport_provider")
        if _contains_any(low, (
            "food poisoning", "contaminated food", "contaminated water", "canteen food",
            "keracunan makanan", "air tercemar", "食物中毒", "食水污染", "饮水污染",
        )):
            signals.add("food_water_exposure")
        if _contains_any(low, (
            "school event", "competition", "ceremony", "guest", "organizer",
            "aktiviti sekolah", "majlis", "tetamu", "penganjur", "学校活动", "學校活動", "嘉宾", "主办方",
        )):
            signals.add("event_operation")
        if _contains_any(low, (
            "evacuate", "evacuation", "assemble students", "pindahkan", "pemindahan",
            "疏散", "撤离", "撤離",
        )):
            signals.add("evacuation_accountability")
        if _contains_any(low, ("money", "coupon", "procurement", "refund", "cash", "固本", "采购", "退款")):
            signals.add("financial_value_involved")
            if family == "general_school_admin":
                family = "finance_procurement"
        if _contains_any(low, (
            "discipline", "misconduct", "bully", "fight", "steal", "stole",
            "stolen", "theft", "robbed", "违规", "霸凌", "打架", "偷窃",
        )):
            # Money may be the subject of student misconduct without making
            # the task a school finance/procurement decision.
            if _contains_any(low, ("steal", "stole", "stolen", "theft", "robbed", "偷窃")):
                signals.discard("financial_value_involved")
            signals.add("evidence_preservation_needed")
            family = "discipline_behaviour"
        if _contains_any(low, (
            "tutor", "academic support", "learning support", "meal support",
            "meal-support", "student support", "pupil support",
            "classroom support", "teacher observation", "speech practice",
            "practice sessions", "deliver their speeches",
            "bimbingan", "sokongan pembelajaran", "bantuan makanan",
            "学习支持", "學習支援", "课业辅导", "課業輔導", "膳食援助",
        )) and family in {"general_school_admin", "food_hygiene"}:
            # Support services are not a food-safety incident merely because a
            # meal is mentioned.  This family selects an internal support plan
            # and remains subject to the socioeconomic-use prohibition.
            family = "teaching_learning_support"
        if re.search(
            r"\b(?:water\s+)?pipe\s+burst\b|\bburst\s+(?:water\s+)?pipe\b|"
            r"\b(?:water\s+leak|power\s+outage|building\s+damage|"
            r"electrical\s+fault|classrooms?\s+(?:cannot|can\s+not)\s+be\s+used)\b",
            low,
        ):
            family = "facilities_environment"
            signals.add("service_disruption")
        if re.search(
            r"\b(?:teacher|staff|employee)\b[^.!?\n]{0,80}"
            r"\b(?:harassment|harassed|bullying|grievance|workplace\s+complaint)\b|"
            r"\b(?:harassment|grievance)\b[^.!?\n]{0,80}"
            r"\b(?:colleague|teacher|staff|employee)\b",
            low,
        ):
            family = "staffing_hr"
            signals.add("evidence_preservation_needed")
        if re.search(r"\b(?:attendance|enrolment|enrollment)\s+record\b|\brekod\s+kehadiran\b|考勤记录|出勤紀錄", low):
            family = "attendance_student_movement"
            signals.add("official_record_involved")
        if re.search(
            r"\b(?:students?|pupils?|children)\b[^.!?\n]{0,55}"
            r"\b(?:vomited|vomiting|fell\s+sick|became\s+ill)\b"
            r"[^.!?\n]{0,55}\b(?:after|from|following)\b|"
            r"\b(?:food\s+poisoning|contaminated\s+(?:food|water))\b",
            low,
        ):
            family = "food_hygiene"
            signals.add("food_water_exposure")
        return family, phase, severity, signals, stakeholders

    def _build_pack(
        self,
        text: str,
        situation: dict,
        *,
        selected_deliverable_ids: list[str] | None,
        custom_deliverables: list[dict] | None,
    ) -> dict:
        """Build an intent-first, action-scoped school response pack.

        The semantic model describes the request; this deterministic compiler
        decides neither permission nor prose. It preserves explicit outputs,
        adds only predicate-backed necessities, and records unsafe requested
        uses separately from the safe replacement actions that may proceed.
        """
        family = str(situation.get("family") or "general_school_admin")
        severity = str(situation.get("severity") or "unknown")
        signals = set(situation.get("signals") or [])
        stakeholders = set(situation.get("stakeholder_candidates") or [])
        people = set(situation.get("affected_people_types") or [])
        concepts = set(situation.get("data_use_concepts") or [])
        requested_action = str(situation.get("requested_action") or "").casefold()
        requested_audience = str(situation.get("requested_audience") or "unknown").casefold()
        external_requests = set(situation.get("explicit_external_actions") or [])
        source_text = str(text or "")
        lower_text = source_text.casefold()
        official_record_mutation = _official_record_mutation_requested(source_text)
        structured_outputs = [
            deepcopy(item) for item in (situation.get("requested_outputs") or [])
            if isinstance(item, dict)
        ]
        explicit_broad_parent_audience = bool(re.search(
            r"\b(?:all|every)\s+parents?\b|\bparent\s+(?:group|community)\b|"
            r"\bschool\s+community\b|\b(?:all|whole)\s+school\b|"
            r"\b(?:semua|seluruh)\s+ibu\s+bapa\b|\bkumpulan\s+ibu\s+bapa\b|"
            r"\bkomuniti\s+sekolah\b|"
            r"(?:家长群|家長群|全体家长|全體家長|所有家长|所有家長|全校通知)",
            lower_text,
        ))
        incident_family = family in {
            "safety_emergency", "health_medical", "safeguarding_welfare",
            "discipline_behaviour", "transport_travel", "food_hygiene",
        }
        # A live model may turn "draft for the injured pupil's parents" into
        # a whole-community notice.  Audience expansion is high-impact too:
        # unless the source explicitly names a broad parent audience, a
        # person-level incident gets a private guardian draft.
        if incident_family and not explicit_broad_parent_audience:
            for item in structured_outputs:
                if str(item.get("artifact_role") or "").lower() == (
                    "school_parent_notice"
                ):
                    item.update({
                        "artifact_role": "private_parent_notice",
                        "audience": "private_recipient",
                        "recipient_type": "guardian",
                    })
        output_roles = {
            str(item.get("artifact_role") or "").strip().lower()
            for item in structured_outputs
        }
        output_audiences = {
            str(item.get("audience") or "").strip().lower()
            for item in structured_outputs
        }
        entries: dict[str, dict] = {}

        def add(role: str, requirement: str, reason: str, **contract: Any) -> None:
            if role not in self.catalog:
                return
            current = entries.get(role)
            if current is None:
                entries[role] = {
                    "role": role, "requirement": requirement,
                    "reason": reason, "contract": deepcopy(contract),
                }
                return
            if requirement == "required" and current["requirement"] != "required":
                current.update({"requirement": requirement, "reason": reason})
            current["contract"].update(deepcopy(contract))

        broad_audience = bool(
            explicit_broad_parent_audience
            or requested_audience in {"school_community", "public"}
            or output_audiences.intersection({"school_community", "public"})
            or output_roles.intersection({
                "school_parent_notice", "public_communication_draft",
            })
        )
        unsupported_evidence_contradiction = (
            _unsupported_claim_without_evidence(lower_text)
        )
        unsupported_claim = bool(
            "unsupported_fact_invention" in concepts
            or unsupported_evidence_contradiction
        )
        mentions_status_data = bool(re.search(
            r"\b(?:family|household|socioeconomic|social|financial)\s+"
            r"(?:background|income|status|standing)|\bparents?['’]?\s+income\b|"
            r"\b(?:poor|low[- ]income|impoverished|b40)\s+(?:family|families|"
            r"household|households|parents?)\b|\bfamily\s+(?:is\s+)?poor\b|"
            r"家庭背景|家境|家庭收入|社会地位|社會地位",
            lower_text,
        ))
        mentions_differential_use = bool(re.search(
            r"\b(?:treat|monitor|label|discipline|target|punish|penali[sz]e|watch|"
            r"surveil|flag|classify)\b[^.!?\n]{0,90}"
            r"\b(?:different(?:ly)?|closely|harsh(?:ly)?|strict(?:ly)?|more|extra|"
            r"high[- ]risk|because|based\s+on)\b|"
            r"\b(?:extra\s+surveillance|closer\s+monitoring|more\s+harshly|"
            r"high[- ]risk\s+flag)\b|"
            r"区别对待|區別對待|特别监控|特別監控",
            lower_text,
        ))
        safe_status_constraint = bool(re.search(
            r"\b(?:do\s+not|don't|never)\s+use\b[^.!?\n]{0,120}"
            r"\b(?:family|household|socioeconomic|social|financial)\b|"
            r"不要使用[^。！？\n]{0,80}(?:家庭背景|家境|家庭收入|社会地位|社會地位)",
            lower_text,
        ))
        status_bias = bool(
            (
                {"socioeconomic_data", "differential_treatment"}.issubset(concepts)
                or (mentions_status_data and mentions_differential_use)
            )
            and not safe_status_constraint
        )
        # The raw-text floor is intentionally narrower than topic matching:
        # supportive aid/tutoring is allowed, while status-based punishment,
        # labelling and surveillance are blocked. Closed semantic concepts are
        # retained as a second signal when the model explicitly identifies both.
        status_bias = _socioeconomic_differential_harm(lower_text, concepts)
        aggregate_only = bool(re.search(
            r"\b(?:class|cohort|school|grade|year)\s+(?:average|aggregate|overall)\b|"
            r"\b(?:anonymous|anonymised|anonymized|aggregate)\s+(?:results?|marks?|data)\b|"
            r"班级平均|班級平均|整体平均|整體平均|汇总数据|彙總數據",
            lower_text,
        ))
        sensitive_detail = bool(re.search(
            r"\b(?:marks?|scores?|grades?|failed?|weak(?:ness|nesses)?|"
            r"why\s+(?:each|they)|disciplin(?:e|ary)|behavio(?:u)?r|injur(?:y|ed)|"
            r"diagnos(?:is|ed)|medical|family\s+background|household\s+income|adhd)\b|"
            r"成绩|成績|分数|分數|不及格|弱点|弱點|纪律|紀律|伤势|傷勢|家庭背景|诊断|診斷",
            lower_text,
        ))
        explicit_individual = bool(
            re.search(
                r"\b(?:full\s+names?|student(?:s)?['’]?\s+names?|nam(?:e|ed|es|ing)|"
                r"list(?:ed|ing)?|include(?:d|s|ing)?|show(?:n|ing)?|each\s+student)\b|"
                r"学生姓名|學生姓名|全名|列出学生|列出學生",
                lower_text,
            )
            or re.search(
                r"\b(?:a|the|year\s+\d+)\s+(?:student|pupil|child)\b"
                r"[^.!?\n]{0,100}\b(?:failed?|weak|diagnos(?:ed|is)|adhd|"
                r"mark|score|grade|disciplin|injur|medical)\b",
                lower_text,
            )
            or re.search(
                r"(?:\bthat\s+|:\s*|\b(?:student|pupil|child)\s+)"
                r"[A-Z][A-Za-z'’-]{1,40}\b[^.!?\n]{0,100}"
                r"\b(?:failed?|weak|diagnos(?:ed|is)|adhd|marks?|scores?|grades?|"
                r"disciplin|injur|medical)\b",
                source_text,
            )
        )
        # One shared detector is used by compiler, deterministic generation
        # fallback and post-generation verification.  Keeping a second local
        # approximation here caused both multilingual privacy bypasses and
        # false REDs for explicitly aggregate results.
        raw_individual_sensitive_broadcast = bool(
            broad_audience
            and source_has_individual_sensitive_detail(source_text)
        )
        sensitive_broadcast = bool(
            broad_audience and (
                concepts.intersection({
                    "public_pii", "health_or_discipline", "student_sensitive_data",
                })
                or raw_individual_sensitive_broadcast
            )
        )
        persistent_sensitive = "persistent_sensitive_learning" in concepts
        input_decision = "NO_OVERRIDE"
        input_reasons: list[str] = []
        transformations: list[str] = []
        unsupported_transform = (
            "Replace it with an evidence-status report and a measurement plan."
        )
        status_transform = (
            "Use observed conduct, verified evidence, applicable school rules and proportionate support only."
        )
        privacy_transform = (
            "Use an anonymous class-level notice and separate one-to-one guardian communication where authorised."
        )
        memory_transform = (
            "Keep case data task-local and retain only a reviewed non-personal procedure."
        )
        if unsupported_claim:
            input_decision = "INFEASIBLE"
            input_reasons.append(
                "The requested claim is unsupported by collected evidence and cannot be presented as fact."
            )
            transformations.append(unsupported_transform)
        if status_bias:
            if input_decision != "INFEASIBLE":
                input_decision = "RED"
            input_reasons.append(
                "Family or socioeconomic background cannot justify labelling or differential monitoring of a pupil."
            )
            transformations.append(status_transform)
        if sensitive_broadcast:
            if input_decision != "INFEASIBLE":
                input_decision = "RED"
            input_reasons.append(
                "Person-level student results, behaviour or protected details cannot be disclosed to a broad audience."
            )
            transformations.append(privacy_transform)
        if persistent_sensitive:
            if input_decision != "INFEASIBLE":
                input_decision = "RED"
            input_reasons.append(
                "Person-level student or parent data cannot become reusable agent memory."
            )
            transformations.append(memory_transform)

        fact_ids = [
            str(item.get("fact_id") or "")
            for item in (situation.get("known_facts") or [])
            if isinstance(item, dict) and str(item.get("fact_id") or "")
        ]
        requested_outputs = [
            deepcopy(item)
            for item in structured_outputs
            if isinstance(item, dict)
            and str(item.get("artifact_role") or "").strip().lower() in self.catalog
        ]
        if not requested_outputs:
            requested_outputs.extend(
                {"artifact_role": role}
                for role in (situation.get("requested_deliverables") or [])
                if role in self.catalog
            )
        # UI selections are explicit operator intent too. Feed them through
        # the same transformation contract as semantic requested outputs; do
        # not bolt them on later where privacy rules could be bypassed.
        requested_role_names = {
            str(item.get("artifact_role") or "") for item in requested_outputs
        }
        for role in (selected_deliverable_ids or []):
            if role in self.catalog and role not in requested_role_names:
                requested_outputs.append({"artifact_role": role})
                requested_role_names.add(role)
        has_explicit_outputs = bool(requested_outputs)
        if not requested_outputs:
            if unsupported_claim:
                requested_outputs = [{"artifact_role": "evidence_status_report"}]
            elif "education_authority" in external_requests or (
                "education_authority" in stakeholders
                and requested_action in {"send", "submit", "message", "contact"}
            ):
                requested_outputs = [{"artifact_role": "education_authority_request"}]
            elif requested_audience == "school_community" or (
                "guardian_notification_relevant" in signals
                and re.search(r"\b(?:notice|message|whatsapp|circular|inform)\b", lower_text)
            ) or (
                re.search(
                    r"\b(?:parents?|guardians?|ibu\s+bapa|penjaga)\b"
                    r"[^.!?\n]{0,100}\b(?:notice|message|whatsapp|circular|inform)\b|"
                    r"\b(?:notice|message|whatsapp|circular|inform)\b"
                    r"[^.!?\n]{0,100}\b(?:parents?|guardians?|ibu\s+bapa|penjaga)\b|"
                    r"(?:家长|家長|全体家长|全體家長)[^。！？\n]{0,60}"
                    r"(?:通知|消息|信息|WhatsApp)|(?:通知|消息|信息|WhatsApp)"
                    r"[^。！？\n]{0,60}(?:家长|家長|全体家长|全體家長)",
                    source_text,
                    flags=re.IGNORECASE,
                )
            ):
                requested_outputs = [{"artifact_role": "school_parent_notice"}]
            elif family == "discipline_behaviour":
                requested_outputs = [{"artifact_role": "discipline_investigation_report"}]
            elif family == "teaching_learning_support" and re.search(
                r"\b(?:plan|support|tutor|assist|intervention|bimbingan|sokongan)\b|"
                r"计划|計劃|支持|支援|辅导|輔導",
                lower_text,
            ):
                requested_outputs = [{"artifact_role": "student_support_plan"}]
            elif family == "events_cocurricular" and re.search(
                r"\b(?:plan|organise|organize|coordinate|manage)\b", lower_text
            ):
                requested_outputs = [{"artifact_role": "event_action_plan"}]
            elif (
                family == "general_school_admin"
                and requested_action in {"draft", "write", "prepare", "report"}
            ):
                requested_outputs = [{"artifact_role": "school_document"}]

        for output in requested_outputs:
            original_role = str(output.get("artifact_role") or "school_document").lower()
            if unsupported_claim:
                add(
                    "evidence_status_report", "required",
                    "truthful replacement for an unsupported claim",
                    claim_policy="evidence_gap_explicit_no_unsupported_metrics",
                    source_fact_ids=fact_ids,
                    safe_transformation=unsupported_transform,
                    action_data_use_concepts=[],
                )
                add(
                    "measurement_plan", "required",
                    "create a path to valid evidence before any future claim",
                    claim_policy="future_measurement_only",
                    source_fact_ids=fact_ids,
                    action_data_use_concepts=[],
                )
                continue
            role = original_role
            contract: dict[str, Any] = {
                "purpose": str(output.get("purpose") or "")[:240],
                "requested_languages": [
                    str(item).lower() for item in (output.get("languages") or [])
                    if str(item).lower() in {"en", "ms", "zh"}
                ],
                "source_fact_ids": output.get("source_fact_ids") or fact_ids,
                "claim_policy": "reported_facts_only",
            }
            if output.get("label"):
                contract["requested_label"] = str(output.get("label"))[:120]
            if output.get("audience"):
                contract["requested_audience"] = str(output.get("audience"))[:40]
            if output.get("recipient_type"):
                contract["requested_recipient_type"] = str(output.get("recipient_type"))[:80]
            if (
                str(output.get("audience") or requested_audience).lower()
                == "school_community"
                and role == "private_parent_notice"
            ):
                # A message for the whole parent community is not a private
                # one-family letter.  Keep the audience boundary visible in
                # both the artifact role and its deterministic policy.
                role = "school_parent_notice"
            if sensitive_broadcast:
                role = "school_parent_notice"
                contract.update({
                    "safe_transformation": privacy_transform,
                    "excluded_data_concepts": [
                        "public_pii", "health_or_discipline",
                        "student_sensitive_data", "individual_marks",
                        "individual_weakness_reasons",
                    ],
                    "claim_policy": "anonymous_aggregate_or_general_support_only",
                    "action_data_use_concepts": [],
                })
            if status_bias or safe_status_constraint:
                if (
                    family == "discipline_behaviour"
                    and original_role in {
                        "school_document", "staff_internal_notice",
                        "public_communication_draft",
                    }
                ):
                    role = "discipline_investigation_report"
                exclusions = set(contract.get("excluded_data_concepts") or [])
                exclusions.update({"socioeconomic_data", "differential_treatment"})
                safe_transforms = [
                    str(contract.get("safe_transformation") or "").strip(),
                    (
                        status_transform if status_bias else
                        "Honor the user's explicit prohibition on status-based treatment."
                    ),
                ]
                contract.update({
                    "safe_transformation": " ".join(
                        item for item in safe_transforms if item
                    ),
                    "excluded_data_concepts": sorted(exclusions),
                    "claim_policy": (
                        "anonymous_observed_conduct_and_verified_evidence_only"
                        if sensitive_broadcast else
                        "observed_conduct_and_verified_evidence_only"
                    ),
                    "action_data_use_concepts": (
                        [] if sensitive_broadcast else ["health_or_discipline"]
                    ),
                })
            if not sensitive_broadcast and not (status_bias or safe_status_constraint):
                action_concepts = (
                    concepts.intersection({
                        "health_or_discipline", "student_sensitive_data",
                    })
                    if role in {
                        "internal_incident_report", "private_parent_notice",
                        "discipline_investigation_report", "safeguarding_action_plan",
                        "student_support_plan",
                    }
                    else set()
                )
                contract["action_data_use_concepts"] = sorted(action_concepts)
            add(role, "required", "explicitly requested by the user", **contract)

        # Predicate-backed necessities only. Mere personal data is not a data
        # breach; a routine official message is not a regulatory incident.
        if "injury_or_illness" in signals:
            add("internal_incident_report", "required", "record the reported incident")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian update")
            add(
                "medical_handover_script",
                "required" if severity in {"critical", "high"} else "recommended",
                "support an accurate handover if a human contacts medical services",
            )
        if "active_danger" in signals:
            add("site_safety_checklist", "required", "support immediate human safety response")
            add("emergency_contact_script", "required", "prepare verified facts for emergency services")
        elif severity in {"critical", "high"} and signals.intersection({
            "injury_or_illness", "person_missing", "safeguarding_concern",
            "external_help_may_be_required",
        }):
            add("site_safety_checklist", "recommended", "precautionary safety support")
            add("emergency_contact_script", "recommended", "contact script if a human escalates")
        if "fire_and_rescue" in stakeholders:
            add(
                "fire_rescue_contact_script",
                "required" if "active_danger" in signals else "recommended",
                "agency-specific factual contact script",
            )
        if "person_missing" in signals:
            add("student_accountability_checklist", "required", "account for students and last-known facts")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian notification")
        if "safeguarding_concern" in signals:
            add("safeguarding_action_plan", "required", "protect the affected pupil while facts are assessed")
            add("evidence_preservation_log", "recommended", "preserve evidence without assigning blame")
        if "transport_operation" in signals:
            add("transport_response_plan", "required", "coordinate the transport impact")
        if "food_water_exposure" in signals:
            add("food_safety_response", "required", "contain and document the exposure")
            if "student" in people or "minor_involved" in signals:
                add(
                    "private_parent_notice", "required",
                    "prepare a minimum-necessary private guardian health notice",
                )
        if "guardian_notification_relevant" in signals and (
            "student" in people or "minor_involved" in signals
        ) and not {"private_parent_notice", "school_parent_notice"}.intersection(entries):
            add(
                "private_parent_notice", "required",
                "prepare a minimum-necessary private guardian update",
            )
        if "event_operation" in signals and not has_explicit_outputs:
            add("event_action_plan", "required", "coordinate the requested event operation")
            add(
                "external_stakeholder_message", "required",
                "prepare the affected organiser or guest message",
            )
        if "evacuation_accountability" in signals or "active_danger" in signals:
            add("student_accountability_checklist", "required", "support controlled accountability")
        if "data_security_incident" in signals:
            add("cyber_incident_response", "required", "contain the actual cyber or data incident")
            add("evidence_preservation_log", "required", "preserve technical and decision evidence")
            add("regulatory_notification_assessment", "required", "assess current notification duties")
        if "financial_value_involved" in signals:
            add("finance_procurement_memo", "required", "govern the financial-value decision")
        if family == "records_regulatory" and "possible_regulatory_trigger" in signals:
            add("regulatory_notification_assessment", "required", "assess a genuine reporting trigger")
        if severity in {"critical", "high"}:
            add("regulatory_notification_assessment", "recommended", "check current reporting requirements")
            add("post_incident_review", "recommended", "support controlled follow-up")
        if not entries and not official_record_mutation:
            defaults = self.policy.get("family_packs", {}).get(family) or []
            add(str(defaults[0] if defaults else "school_document"),
                "required", f"core {family} output")

        recipient_draft_role = {
            "guardian": "private_parent_notice",
            "school_community": "school_parent_notice",
            "medical_services": "medical_handover_script",
            "malaysia_emergency_services_999": "emergency_contact_script",
            "fire_and_rescue": "fire_rescue_contact_script",
            "education_authority": "education_authority_request",
            "event_organizer": "external_stakeholder_message",
            "transport_provider": "external_stakeholder_message",
            "public_media": "public_communication_draft",
            "police": "external_stakeholder_message",
            "local_authority": "external_stakeholder_message",
            "vendor": "external_stakeholder_message",
            "external_stakeholder": "external_stakeholder_message",
        }
        recipient_existing_roles = {
            "guardian": {"private_parent_notice"},
            "school_community": {"school_parent_notice"},
            "medical_services": {"medical_handover_script"},
            "malaysia_emergency_services_999": {"emergency_contact_script"},
            "fire_and_rescue": {"fire_rescue_contact_script"},
            "education_authority": {
                "education_authority_report", "education_authority_request",
            },
            "event_organizer": {"external_stakeholder_message"},
            "transport_provider": {"external_stakeholder_message"},
            "public_media": {"public_communication_draft"},
            "police": {"external_stakeholder_message"},
            "local_authority": {"external_stakeholder_message"},
            "vendor": {"external_stakeholder_message"},
            "external_stakeholder": {"external_stakeholder_message"},
        }
        for recipient in sorted(external_requests):
            role = recipient_draft_role.get(recipient)
            existing = recipient_existing_roles.get(recipient, set())
            if role and not unsupported_claim and not existing.intersection(entries):
                add(role, "required", "a governed draft is required before external release")

        # Recipient-derived broad drafts are created after requested-output
        # transformation. Apply the same privacy contract here so a public or
        # community release gate can never reintroduce the blocked details via
        # its companion draft.
        if sensitive_broadcast:
            for broad_role in {
                "school_parent_notice", "public_communication_draft",
            }:
                entry = entries.get(broad_role)
                if not entry:
                    continue
                contract = entry["contract"]
                exclusions = set(contract.get("excluded_data_concepts") or [])
                exclusions.update({
                    "public_pii", "health_or_discipline",
                    "student_sensitive_data", "individual_marks",
                    "individual_weakness_reasons", "socioeconomic_data",
                })
                contract.update({
                    "safe_transformation": privacy_transform,
                    "excluded_data_concepts": sorted(exclusions),
                    "claim_policy": "anonymous_aggregate_or_general_support_only",
                    "action_data_use_concepts": [],
                })

        requested_pack_languages: list[str] = []
        for output in structured_outputs:
            for language in output.get("languages") or []:
                code = str(language).strip().lower()
                if code in {"en", "ms", "zh"} and code not in requested_pack_languages:
                    requested_pack_languages.append(code)
        # Provider-outage floor for explicit language instructions.  This is
        # a closed format contract, not semantic task planning: it merely
        # preserves the languages the user named in the source request.
        explicit_language_request = bool(re.search(
            r"\b(?:bilingual|multilingual|dwibahasa|in\s+(?:english|malay|"
            r"bahasa\s+melayu|chinese|mandarin))\b|双语|雙語|英文|英语|英語|"
            r"马来文|馬來文|马来语|馬來語|华文|華文|中文",
            source_text,
            flags=re.IGNORECASE,
        ))
        if explicit_language_request:
            source_language_names = {
                "en": bool(re.search(
                    r"\benglish\b|英文|英语|英語", source_text, re.IGNORECASE,
                )),
                "ms": bool(re.search(
                    r"\b(?:malay|bahasa\s+melayu)\b|马来文|馬來文|马来语|馬來語",
                    source_text,
                    re.IGNORECASE,
                )),
                "zh": bool(re.search(
                    r"\b(?:chinese|mandarin)\b|华文|華文|中文",
                    source_text,
                    re.IGNORECASE,
                )),
            }
            named = [code for code in ("en", "ms", "zh") if source_language_names[code]]
            if named:
                requested_pack_languages = named
        if not requested_pack_languages:
            if re.search(r"[\u3400-\u9fff]", source_text):
                requested_pack_languages = ["zh"]
            else:
                malay_cues = re.findall(
                    r"\b(?:sediakan|jangan|murid|pelajar|sekolah|kantin|"
                    r"berhampiran|ibu\s+bapa|penjaga|laporan|draf|hantar)\b",
                    lower_text,
                )
                requested_pack_languages = ["ms"] if len(malay_cues) >= 2 else ["en"]
        # Added safety/accountability artifacts must follow the same language
        # contract as the explicitly requested files. Otherwise a provider
        # fallback creates a half-Malay/half-English response pack.
        for entry in entries.values():
            if not entry["contract"].get("requested_languages"):
                entry["contract"]["requested_languages"] = list(
                    requested_pack_languages
                )

        selected_set = set(selected_deliverable_ids or [])
        has_selection = selected_deliverable_ids is not None
        deliverables: list[dict] = []
        for entry in entries.values():
            role = entry["role"]
            requirement = entry["requirement"]
            item = self._catalog_item(role, requirement, entry["reason"])
            item.update(deepcopy(entry["contract"]))
            if item.get("requested_audience") in {
                "internal", "private_recipient", "school_community",
                "external_agency", "public",
            }:
                item["audience"] = item["requested_audience"]
            if item.get("requested_recipient_type"):
                item["recipient_type"] = item["requested_recipient_type"]
            item["selected"] = (
                True if requirement == "required"
                else (item["deliverable_id"] in selected_set if has_selection else False)
            )
            deliverables.append(item)

        for custom in (custom_deliverables or [])[:10]:
            if not isinstance(custom, dict):
                continue
            label = str(custom.get("label") or "").strip()[:120]
            if not label:
                continue
            audience = str(custom.get("audience") or "internal").lower()
            if audience not in {
                "internal", "private_recipient", "school_community",
                "external_agency", "public",
            }:
                audience = "internal"
            recipient_type = str(
                custom.get("recipient_type") or "school_staff"
            ).strip().lower()[:80]
            mode = str(custom.get("mode") or "draft").strip().lower()
            if recipient_type == "public_media":
                audience = "public"
            elif mode == "external_release" and audience == "internal":
                audience = (
                    "private_recipient" if recipient_type == "guardian"
                    else "external_agency"
                )
            role = (
                "public_communication_draft" if audience == "public"
                else "school_parent_notice" if audience == "school_community"
                else "school_document"
            )
            custom_id = _new_id("custom")
            item = self._catalog_item(role, "user_added", "added by the user")
            item.update({
                "deliverable_id": custom_id, "label": label,
                "filename": self._custom_filename(label), "audience": audience,
                "recipient_type": recipient_type,
                "required": False, "selected": True,
                "source_fact_ids": fact_ids, "action_data_use_concepts": [],
                "requested_languages": list(requested_pack_languages),
            })
            template_key = str(custom.get("template_key") or "").strip().lower()
            if template_key in {
                "teacher_observation", "stock_control", "relocation_plan",
                "confidential_intake", "investigation_plan", "meeting_agenda",
            }:
                item["custom_template_key"] = template_key
            deliverables.append(item)
            if mode == "external_release" or audience in {
                "private_recipient", "external_agency", "public",
            }:
                deliverables.append({
                    "deliverable_id": f"external_release_{custom_id}",
                    "artifact_role": "external_release_gate",
                    "label": f"Request external release: {label}",
                    "filename": "", "kind": "external_action",
                    "mode": "external_release", "audience": audience,
                    "recipient_type": recipient_type,
                    "requirement": "user_added", "required": False,
                    "selected": True,
                    "reason": "the user added an outward-facing deliverable",
                    "source_policy": "governed_release_only",
                    "linked_deliverable_id": custom_id,
                    "action_data_use_concepts": ["external_release"],
                })

        if official_record_mutation:
            deliverables.append({
                "deliverable_id": "official_record_change_gate",
                "artifact_role": "official_record_change_gate",
                "label": "Request controlled official-record change",
                "filename": "",
                "kind": "system_action",
                "mode": "official_record_change",
                "audience": "internal",
                "recipient_type": "student_information_system",
                "requirement": "explicit_user_request",
                "required": True,
                "selected": True,
                "reason": (
                    "the user explicitly requested a change to an official "
                    "student record"
                ),
                "source_policy": "human_authorised_record_change_only",
                "linked_deliverable_id": "",
                "action_data_use_concepts": ["official_record_change"],
            })

        for recipient in sorted(external_requests):
            audience = {
                "public_media": "public", "guardian": "private_recipient",
                "school_community": "school_community",
                "event_organizer": "private_recipient",
                "transport_provider": "private_recipient", "vendor": "private_recipient",
                "external_stakeholder": "private_recipient",
            }.get(recipient, "external_agency")
            deliverables.append({
                "deliverable_id": f"external_release_{recipient}",
                "artifact_role": "external_release_gate",
                "label": f"Request external release to {recipient.replace('_', ' ')}",
                "filename": "", "kind": "external_action", "mode": "external_release",
                "audience": audience, "recipient_type": recipient,
                "requirement": "explicit_user_request", "required": True,
                "selected": True, "reason": "the user explicitly requested an external action",
                "source_policy": "governed_release_only",
                "linked_deliverable_id": recipient_draft_role.get(recipient),
                "action_data_use_concepts": ["external_release"],
            })

        return {
            "pack_id": _new_id("pack"), "revision": 2,
            "policy_version": self.policy.get("policy_version"),
            "case_summary": situation.get("case_summary"), "severity": severity,
            "emergency_banner": self._emergency_banner(situation),
            "input_governance": {
                "decision": input_decision, "reasons": input_reasons,
                "blocked_request": str(text or "")[:1000] if input_decision != "NO_OVERRIDE" else "",
                "safe_transformations": transformations,
                "note": "This evaluates the user's requested use; every proposed action is governed separately.",
            },
            "deliverables": deliverables,
            "coverage": {
                "expected_selected": sum(1 for item in deliverables if item.get("selected")),
                "required": sum(1 for item in deliverables if item.get("required")),
                "rule_basis": [family, *sorted(signals)],
            },
            "governance_note": (
                "User-input governance and agent-action governance are separate; "
                "each selected action is governed before execution."
            ),
        }

    def _build_pack_legacy(
        self,
        text: str,
        situation: dict,
        *,
        selected_deliverable_ids: list[str] | None,
        custom_deliverables: list[dict] | None,
    ) -> dict:
        family = situation["family"]
        signals = set(situation["signals"])
        stakeholders = set(situation["stakeholder_candidates"])
        roles: list[tuple[str, str, str]] = []

        def add(role: str, requirement: str, reason: str) -> None:
            if role not in self.catalog:
                return
            for idx, existing in enumerate(roles):
                if existing[0] != role:
                    continue
                if requirement == "required" and existing[1] != "required":
                    roles[idx] = (role, requirement, reason)
                return
            roles.append((role, requirement, reason))

        people = set(situation.get("affected_people_types") or [])
        families = [family, *(situation.get("secondary_families") or [])]
        for pack_family in families:
            base_roles = self.policy.get("family_packs", {}).get(pack_family) or []
            for idx, role in enumerate(base_roles):
                if role == "private_parent_notice" and not (
                    "student" in people or "minor_involved" in signals
                ):
                    continue
                add(
                    role,
                    # The primary family supplies one core document. Secondary
                    # labels are context, not permission to inflate the default
                    # pack; their concrete signals below can still promote the
                    # right cross-domain deliverable to required.
                    "required"
                    if pack_family == family and idx == 0
                    else "recommended",
                    f"{pack_family} coverage",
                )
        if not roles:
            add("school_document", "required", "general school-administration coverage")

        if "injury_or_illness" in signals:
            add("internal_incident_report", "required", "record the reported incident")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian update")
            add(
                "medical_handover_script",
                "required" if situation["severity"] in {"critical", "high"} else "recommended",
                "support accurate medical handover",
            )
        if "active_danger" in signals:
            add("site_safety_checklist", "required", "support immediate human safety response")
            add("emergency_contact_script", "required", "prepare facts for Malaysia emergency services — 999")
        elif (
            situation["severity"] in {"critical", "high"}
            and signals.intersection({
                "injury_or_illness", "person_missing", "safeguarding_concern",
                "external_help_may_be_required",
            })
        ):
            add("site_safety_checklist", "recommended", "prepare precautionary immediate-safety steps")
            add("emergency_contact_script", "recommended", "prepare a no-assumption emergency contact script if escalation is needed")
        if "fire_and_rescue" in stakeholders:
            add(
                "fire_rescue_contact_script",
                "required" if "active_danger" in signals else "recommended",
                "prepare agency-specific facts without claiming a call was made",
            )
        if "person_missing" in signals:
            add("student_accountability_checklist", "required", "account for students and last-known facts")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian notification")
        if "safeguarding_concern" in signals:
            add("safeguarding_action_plan", "required", "protect the affected student while facts are assessed")
            add("evidence_preservation_log", "recommended", "preserve reported evidence without assigning blame")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private safeguarding update for the guardian")
        if "transport_provider" in stakeholders or "transport_operation" in signals:
            add("transport_response_plan", "required", "coordinate the school transport impact")
        if "food_water_exposure" in signals:
            add("food_safety_response", "required", "contain and document the food or water safety concern")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian health notice")
        if "event_operation" in signals:
            add("event_action_plan", "required", "coordinate the school event change")
            add("external_stakeholder_message", "required", "prepare the affected organizer or guest message")
        if "evacuation_accountability" in signals or "active_danger" in signals:
            add("student_accountability_checklist", "required", "support controlled student accountability")
        if "data_security_incident" in signals:
            add("cyber_incident_response", "required", "contain the cyber or data incident")
            add("evidence_preservation_log", "required", "preserve technical and decision evidence")
            add("regulatory_notification_assessment", "required", "assess current reporting obligations")
        if (
            "guardian_notification_relevant" in signals
            and ("student" in people or "minor_involved" in signals)
        ):
            add("private_parent_notice", "required", "prepare the relevant private guardian notice")
        if "public_interest" in signals:
            add("public_communication_draft", "recommended", "prepare a privacy-safe holding statement")
        if "personal_data_involved" in signals:
            add("cyber_incident_response", "required", "contain and assess the personal-data incident")
        if "financial_value_involved" in signals:
            add("finance_procurement_memo", "required", "record and govern the financial-value decision")
        if "official_record_involved" in signals:
            add("regulatory_notification_assessment", "required", "assess official-record and reporting obligations")
        if situation["severity"] in {"critical", "high"}:
            add("regulatory_notification_assessment", "recommended", "check applicable school and authority reporting requirements")
            add("education_authority_report", "conditional", "prepare if school policy or authority direction requires it")
            add("post_incident_review", "recommended", "support controlled follow-up after immediate safety work")
        for role in situation.get("requested_deliverables") or []:
            add(role, "required", "explicitly requested by the user")
        recipient_draft_role = {
            "guardian": "private_parent_notice",
            "medical_services": "medical_handover_script",
            "malaysia_emergency_services_999": "emergency_contact_script",
            "fire_and_rescue": "fire_rescue_contact_script",
            "education_authority": "education_authority_report",
            "event_organizer": "external_stakeholder_message",
            "transport_provider": "external_stakeholder_message",
            "public_media": "public_communication_draft",
            "police": "external_stakeholder_message",
            "local_authority": "external_stakeholder_message",
            "vendor": "external_stakeholder_message",
            "external_stakeholder": "external_stakeholder_message",
        }
        for recipient in situation.get("explicit_external_actions") or []:
            linked_role = recipient_draft_role.get(recipient)
            if linked_role:
                add(linked_role, "required", "draft required before the requested external action")

        deliverables: list[dict] = []
        selected_set = set(selected_deliverable_ids or [])
        has_selection = selected_deliverable_ids is not None
        for role, requirement, reason in roles:
            item = self._catalog_item(role, requirement, reason)
            item["selected"] = (
                True if requirement == "required"
                else (item["deliverable_id"] in selected_set if has_selection else False)
            )
            deliverables.append(item)

        for custom in (custom_deliverables or [])[:10]:
            if not isinstance(custom, dict):
                continue
            label = str(custom.get("label") or "").strip()[:120]
            if not label:
                continue
            audience = str(custom.get("audience") or "internal").strip().lower()
            if audience not in {"internal", "private_recipient", "external_agency", "public"}:
                audience = "internal"
            mode = str(custom.get("mode") or "draft").strip().lower()
            if mode not in {"draft", "external_release"}:
                mode = "draft"
            requested_recipient = str(
                custom.get("recipient_type") or ""
            ).strip().lower()
            recipient_audience = {
                "public_media": "public",
                "guardian": "private_recipient",
                "school_staff": "internal",
                "school_leadership": "internal",
                "medical_services": "external_agency",
                "malaysia_emergency_services_999": "external_agency",
                "fire_and_rescue": "external_agency",
                "police": "external_agency",
                "education_authority": "external_agency",
                "local_authority": "external_agency",
                "event_organizer": "private_recipient",
                "vendor": "private_recipient",
                "transport_provider": "private_recipient",
                "external_stakeholder": "private_recipient",
            }
            if requested_recipient in recipient_audience:
                recipient = requested_recipient
                audience = recipient_audience[recipient]
            else:
                recipient = {
                    "public": "public_media",
                    "external_agency": "education_authority",
                    "private_recipient": "external_stakeholder",
                    "internal": "school_staff",
                }[audience]
            custom_id = _new_id("custom")
            custom_role = (
                "public_communication_draft" if audience == "public"
                else "school_document"
            )
            deliverables.append({
                "deliverable_id": custom_id,
                "artifact_role": custom_role,
                "label": label,
                "filename": self._custom_filename(label),
                "kind": "artifact",
                "mode": "draft",
                "audience": audience,
                "recipient_type": recipient,
                "requirement": "user_added",
                "required": False,
                "selected": True,
                "reason": "added by the user; still subject to independent governance",
                "source_policy": "case_facts_only",
            })
            if mode == "external_release":
                deliverables.append({
                    "deliverable_id": f"external_release_{custom_id}",
                    "artifact_role": "external_release_gate",
                    "label": f"Request external release: {label}",
                    "filename": "",
                    "kind": "external_action",
                    "mode": "external_release",
                    "audience": audience,
                    "recipient_type": recipient,
                    "requirement": "user_added",
                    "required": False,
                    "selected": True,
                    "reason": "separate release request for the user-added draft",
                    "source_policy": "governed_release_only",
                    "linked_deliverable_id": custom_id,
                })

        # Explicit sends/calls are separate actions. Drafting a script does not
        # imply the real communication occurred or was authorised.
        for recipient in situation.get("explicit_external_actions") or []:
            did = f"external_release_{recipient}"
            release_audience = {
                "public_media": "public",
                "guardian": "private_recipient",
                "event_organizer": "private_recipient",
                "transport_provider": "private_recipient",
                "vendor": "private_recipient",
                "external_stakeholder": "private_recipient",
            }.get(recipient, "external_agency")
            deliverables.append({
                "deliverable_id": did,
                "artifact_role": "external_release_gate",
                "label": f"Request external release to {recipient.replace('_', ' ')}",
                "filename": "",
                "kind": "external_action",
                "mode": "external_release",
                "audience": release_audience,
                "recipient_type": recipient,
                "requirement": "explicit_user_request",
                "required": True,
                "selected": True,
                "reason": "the user explicitly requested a real external action",
                "source_policy": "governed_release_only",
                "linked_deliverable_id": recipient_draft_role.get(recipient),
            })

        return {
            "pack_id": _new_id("pack"),
            "revision": 1,
            "policy_version": self.policy.get("policy_version"),
            "case_summary": situation["case_summary"],
            "severity": situation["severity"],
            "emergency_banner": self._emergency_banner(situation),
            "deliverables": deliverables,
            "coverage": {
                "expected_selected": sum(1 for d in deliverables if d.get("selected")),
                "required": sum(1 for d in deliverables if d.get("required")),
                "rule_basis": [family, *sorted(signals)],
            },
            "governance_note": "Each selected action will be governed separately before execution.",
        }

    def _catalog_item(self, role: str, requirement: str, reason: str) -> dict:
        spec = deepcopy(self.catalog[role])
        return {
            "deliverable_id": role,
            "artifact_role": role,
            "label": spec.get("label", role.replace("_", " ").title()),
            "filename": spec.get("filename", f"{role}.md"),
            "kind": "artifact",
            "mode": "draft",
            "audience": spec.get("audience", "internal"),
            "recipient_type": spec.get("recipient_type", "school_staff"),
            "requirement": requirement,
            "required": requirement == "required",
            "selected": True,
            "reason": reason,
            "source_policy": spec.get("source_policy", "case_facts_only"),
        }

    @staticmethod
    def _custom_filename(label: str) -> str:
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
        # User labels can contain names, contact details or sensitive case
        # words. Keep those out of filenames, traces and downloadable URLs.
        return f"custom_school_output_{digest}_draft.md"

    @staticmethod
    def _emergency_banner(situation: dict) -> dict | None:
        if situation["severity"] not in {"critical", "high"}:
            return None
        signals = set(situation.get("signals") or [])
        if not signals.intersection({
            "active_danger", "injury_or_illness", "person_missing",
            "safeguarding_concern",
        }):
            return None
        return {
            "severity": situation["severity"],
            "message": (
                "If danger or injury is ongoing, use the school's emergency SOP "
                "and contact qualified emergency services now. This agent prepares "
                "materials; it does not replace on-scene human action."
            ),
        }

    @staticmethod
    def _critical_question(situation: dict, answers: dict) -> dict | None:
        unknown_ids = {u.get("fact_id") for u in situation.get("unknowns") or []}
        life_unknown = any(
            u.get("impact") == "life_safety" for u in situation.get("unknowns") or []
        )
        source_text = str(situation.get("case_summary") or "")
        source_physical, _ = _physical_incident_flags(source_text)
        source_safety = bool(
            source_physical
            or _active_hazard_present(source_text)
            or _actual_safeguarding_concern(source_text)
            or re.search(
                r"\b(?:missing|unaccounted[- ]for)\s+"
                r"(?:student|pupil|child)\b|\b(?:student|pupil|child)\s+"
                r"(?:is\s+)?missing\b|学生失踪|學生失蹤|"
                r"murid\s+hilang|pelajar\s+hilang",
                source_text.casefold(),
            )
        )
        if not answers.get("immediate_danger") and situation["severity"] in {"critical", "high"} and (
            life_unknown or "danger_still_present" in unknown_ids
        ) and source_safety and "active_danger" not in set(situation["signals"]):
            return {
                "question_id": "immediate_danger",
                "prompt": "Is there still immediate danger or an unmet medical emergency now?",
                "why": "This changes the immediate safety priority and external-contact package.",
                "options": ["Yes", "No", "Unknown"],
                "allow_tbc": True,
                "scope": "life_safety",
            }
        if (
            "external_stakeholder" in set(
                situation.get("explicit_external_actions") or []
            )
            and not answers.get("external_recipient")
        ):
            return {
                "question_id": "external_recipient",
                "prompt": (
                    "Who should receive this external message or document? "
                    "Choose Draft only if nothing should be sent."
                ),
                "why": (
                    "The recipient changes the privacy boundary and approval "
                    "record, so the agent must not guess it."
                ),
                "options": [
                    "Parent or guardian",
                    "District Education Office",
                    "Other external recipient",
                    "Draft only - do not send",
                ],
                "allow_tbc": True,
                "scope": "external_release",
            }
        return None


def selected_pack_deliverables(response_pack: dict | None) -> list[dict]:
    return [
        d for d in ((response_pack or {}).get("deliverables") or [])
        if d.get("selected") is True
    ]


def reconcile_school_response_pack(
    plan: CandidatePlan,
    envelope: TaskEnvelope,
) -> dict:
    """Ensure every selected response-pack item has exactly one action shell."""
    pack = (envelope.metadata or {}).get("school_response_pack") or {}
    expected = selected_pack_deliverables(pack)
    if not expected:
        return {"active": False, "reason": "no_selected_response_pack"}

    existing_by_deliverable: dict[str, CandidateAction] = {}
    existing_by_role: dict[str, list[CandidateAction]] = {}
    for action in plan.actions:
        did = str((action.metadata or {}).get("deliverable_id") or "")
        role = str((action.metadata or {}).get("artifact_role") or "")
        if not role and (
            str(action.tool or "").lower() in {"fs", "docx", "report"}
        ):
            role = infer_artifact_role(action)
        if did:
            existing_by_deliverable.setdefault(did, action)
        if role:
            existing_by_role.setdefault(role, []).append(action)

    def valid_shape(action: CandidateAction, item: dict) -> bool:
        tool = str(action.tool or "").strip().lower()
        operation = str(action.operation or "").strip().lower()
        if item.get("kind") == "artifact":
            return (
                tool in {"docx", "report"}
                or (tool == "fs" and "save" in operation)
            )
        if item.get("kind") == "system_action":
            return bool(
                tool in {"gui", "record", "student_information_system"}
                or (action.metadata or {}).get("official_record_change_action")
                is True
            )
        # Response-pack gates are control-plane chat actions in the competition
        # build. Never bind a descriptive recipient to a planner-supplied live
        # email/publish target; future connectors must verify the recipient in
        # the signed execution ticket instead.
        return bool(
            tool == "chat"
            and (
                (action.metadata or {}).get("external_release_action") is True
                or any(word in operation for word in (
                    "send", "publish", "submit", "release", "message", "answer",
                ))
            )
        )

    inserted: list[str] = []
    matched: list[str] = []
    consumed_action_ids: set[str] = set()
    for item in expected:
        did = str(item.get("deliverable_id") or "")
        role = str(item.get("artifact_role") or "school_document")
        action = existing_by_deliverable.get(did)
        if action is not None and (
            action.action_id in consumed_action_ids or not valid_shape(action, item)
        ):
            action = None
        if action is None:
            action = next(
                (
                    candidate for candidate in existing_by_role.get(role, [])
                    if candidate.action_id not in consumed_action_ids
                    and valid_shape(candidate, item)
                ),
                None,
            )
        if action is None and item.get("kind") == "artifact":
            action = CandidateAction(
                tool="fs",
                operation="save_under_outputs",
                target=str(item.get("filename") or f"{role}.md"),
                purpose=f"prepare {item.get('label') or role}",
                expected_effect="create one governed Markdown draft",
                reversibility="high",
                uncertainty="medium",
                requires_governance=True,
                metadata={},
            )
            plan.actions.append(action)
            inserted.append(did)
        elif action is None and item.get("kind") == "system_action":
            action = CandidateAction(
                tool="gui",
                operation="update_official_record",
                target=str(
                    item.get("recipient_type") or "student_information_system"
                ),
                purpose="request a controlled official student-record change",
                expected_effect=(
                    "change the official record only after independent human "
                    "approval"
                ),
                reversibility="medium",
                uncertainty="medium",
                requires_governance=True,
                metadata={},
            )
            plan.actions.append(action)
            inserted.append(did)
        elif action is None:
            recipient = str(item.get("recipient_type") or "external_recipient")
            action = CandidateAction(
                tool="chat",
                operation="answer",
                target=recipient,
                purpose=f"request approval before external release to {recipient}",
                expected_effect="pause for independent human approval; do not claim release",
                reversibility="high",
                uncertainty="low",
                requires_governance=True,
                metadata={"body": "Human approval is required before this external action."},
            )
            plan.actions.append(action)
            inserted.append(did)
        else:
            matched.append(did)

        consumed_action_ids.add(action.action_id)

        action.metadata.update({
            "deliverable_id": did,
            "artifact_role": role,
            "artifact_label": item.get("label"),
            "audience": item.get("audience"),
            "recipient_type": item.get("recipient_type"),
            "response_pack_id": pack.get("pack_id"),
            "response_pack_requirement": item.get("requirement"),
            "coverage_source": "school_response_pack",
            "source_policy": item.get("source_policy"),
            "linked_deliverable_id": item.get("linked_deliverable_id"),
            "situation_severity": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("severity"),
            "school_case_summary": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("case_summary"),
            "school_known_facts": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("known_facts") or [],
            "school_unknowns": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("unknowns") or [],
            "school_signals": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("signals") or [],
            "school_family": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("family"),
            "source_request": (
                (envelope.metadata or {}).get("school_situation") or {}
            ).get("source_request") or envelope.normalized_goal,
            "requested_languages": item.get("requested_languages") or [],
            "source_fact_ids": item.get("source_fact_ids") or [],
            "claim_policy": item.get("claim_policy") or "reported_facts_only",
            "safe_transformation": item.get("safe_transformation") or "",
            "excluded_data_concepts": item.get("excluded_data_concepts") or [],
            "custom_template_key": item.get("custom_template_key") or "",
            # Per-action concepts are the action contract. The task-level input
            # findings remain in response_pack.input_governance and must never
            # contaminate an independently safe replacement artifact.
            "response_pack_data_use_concepts": (
                item.get("action_data_use_concepts") or []
            ),
            "action_data_contract": True,
        })
        if str(item.get("purpose") or "").strip():
            action.purpose = str(item.get("purpose"))[:400]
        if item.get("kind") == "artifact":
            # The response-pack contract, not the live planner, owns the
            # downloadable filename. This keeps custom labels, names and case
            # details out of paths even when a planner invents a descriptive
            # target.
            action.target = str(item.get("filename") or f"{role}.md")
        if item.get("kind") == "external_action":
            action.metadata.update({
                "external_release_action": True,
                "release_state": "pending_approval",
                "approval_boundary": "human_required_before_external_release",
                "data_use_concepts": ["external_release"],
            })
        elif item.get("kind") == "system_action":
            action.metadata.update({
                "official_record_change_action": True,
                "system_level_change": True,
                "release_state": "not_applicable",
                "approval_boundary": "human_required_before_record_change",
                "data_use_concepts": ["official_record_change"],
            })

    superseded: list[str] = []
    effective_actions: list[CandidateAction] = []
    for action in plan.actions:
        tool = str(action.tool or "").lower()
        operation = str(action.operation or "").lower()
        is_text_artifact = (
            tool in {"docx", "report", "xlsx", "pptx"}
            or (tool == "fs" and "save" in operation)
        )
        is_planner_external = bool(
            tool in {"email", "publish"}
            or (action.metadata or {}).get("external_release_action") is True
            or any(word in operation for word in (
                "send", "publish", "submit", "release", "message",
            ))
            or re.search(
                r"\b(?:external\s+release|send|publish|submit|release|"
                r"contact\s+(?:the\s+)?(?:parent|guardian|office|authority|"
                r"recipient|stakeholder))\b",
                " ".join((
                    str(action.purpose or ""),
                    str(action.expected_effect or ""),
                    str(action.target or ""),
                )).casefold(),
            ) is not None
        )
        is_planner_system_change = bool(
            (action.metadata or {}).get("official_record_change_action") is True
            or (
                tool in {"gui", "record", "student_information_system"}
                and any(word in operation for word in (
                    "update", "change", "edit", "correct", "write", "save",
                ))
            )
        )
        if (
            (is_text_artifact or is_planner_external or is_planner_system_change)
            and action.action_id not in consumed_action_ids
        ):
            action.metadata["response_pack_superseded"] = True
            superseded.append(action.action_id)
            continue
        effective_actions.append(action)
    plan.actions = effective_actions

    plan.notes.append(
        f"response-pack reconciled: expected={len(expected)} inserted={len(inserted)}"
    )
    return {
        "active": True,
        "pack_id": pack.get("pack_id"),
        "expected": [str(d.get("deliverable_id")) for d in expected],
        "inserted": inserted,
        "matched": matched,
        "superseded": superseded,
        "coverage_complete": len(inserted) + len(matched) == len(expected),
    }


def govern_school_research_actions(
    plan: CandidatePlan,
    envelope: TaskEnvelope,
) -> dict:
    """Replace case-bearing web queries with a PII-free official query.

    Research remains a normal proposed action and therefore receives the same
    independent governance as every other action. This function only narrows
    its query and source boundary; it cannot cause the search to execute.
    """
    metadata = envelope.metadata or {}
    situation = metadata.get("school_situation") or {}
    semantics = metadata.get("school_semantics") or {}
    if not situation and not (
        metadata.get("school_semantics_checked")
        and semantics.get("school_domain") is True
    ):
        return {"active": False, "reason": "not_a_checked_school_task"}
    family = str(situation.get("family") or "")
    if not family:
        family = {
            "health": "health_medical",
            "discipline": "discipline_behaviour",
            "attendance": "attendance_student_movement",
            "school_event": "events_cocurricular",
            "fundraising_finance": "finance_procurement",
            "official_records": "records_regulatory",
        }.get(str(semantics.get("school_area") or ""), "general_school_admin")
    topic = _OFFICIAL_RESEARCH_TOPICS.get(
        family, "Malaysia public school administration official guidance"
    )
    sites = " OR ".join(f"site:{domain}" for domain in _OFFICIAL_DOMAINS)
    safe_query = f"({sites}) {topic}"
    changed: list[str] = []
    for action in plan.actions:
        if str(action.tool or "").strip().lower() != "web_search":
            continue
        action.target = safe_query
        action.metadata.update({
            "query": safe_query,
            "school_policy_research": True,
            "research_purpose": "official_guidance",
            "allowed_domains": list(_OFFICIAL_DOMAINS),
            "query_privacy": "case_identifiers_removed",
            "retrieved_content_trust": "untrusted_evidence_only",
            "never_use_for_case_fact_completion": True,
            "data_use_concepts": [],
        })
        changed.append(action.action_id)
    return {
        "active": bool(changed),
        "rewritten_action_ids": changed,
        "official_domains": list(_OFFICIAL_DOMAINS),
    }
