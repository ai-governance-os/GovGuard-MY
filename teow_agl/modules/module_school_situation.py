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
from .module_school_artifact_guard import (
    SCHOOL_OPERATOR_COMMAND_PREFIX,
    infer_artifact_role,
)
from .module_school_case_context import merge_followup_situation
from .module_deliverable_mentions import is_requested_output_mention
from .module_school_intent_contract import (
    build_user_intent_contract,
    evaluate_deliverable_coverage,
)
from .module_school_release_intent import (
    infer_explicit_external_recipients as _shared_infer_external_recipients,
    internal_school_repository_write_requested,
    negated_external_recipients as _shared_negated_external_recipients,
    release_is_globally_negated as _shared_release_is_negated,
)
from .module_school_privacy import source_has_individual_sensitive_detail
from .module_school_fallback_floors import deterministic_incident_facets
from .module_school_hardening_v2 import (
    approval_bypass_attempt,
    donor_preference_request,
    explicit_release_channel_specs,
    fact_invention_or_no_tbc_request,
    institutional_investment_request,
    institutional_return_prediction,
    missing_minor_reported,
    public_attention_reported,
    school_transport_collision,
    school_transport_incident,
)


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


def _explicitly_nonincident_admin_request(text: str) -> bool:
    """Return true when source modality contradicts a live-incident reading.

    This is deliberately not a hazard vocabulary. It recognises an explicit
    administrative/policy/planning request and stands down only if the same
    source lacks an actuality marker. A novel real incident remains owned by
    the semantic planner even when deterministic regexes do not know its noun.
    """
    value = str(text or "").casefold()
    actuality = bool(re.search(
        r"\b(?:occurred|happened|reported|discovered|found|caught|collapsed|"
        r"fainted|injured|bitten|stung|vomited|missing|exposed|leaked|"
        r"compromised|cancelled|broke\s+down|burst|failed|was\s+sent|"
        r"has\s+been|currently|suddenly|may\s+still|cannot\s+be\s+used|"
        r"there\s+(?:is|are))\b",
        value,
    ))
    if actuality:
        return False
    admin_topic = bool(re.search(
        r"\b(?:agenda|timetable|ordinary\s+menu|lunch\s+menu|allergy\s+menu|"
        r"policy\s+(?:agenda|review)|awareness\s+agenda|routine\s+meeting|"
        r"recycling[- ]day\s+notice|planned\s+(?:drill|exercise)|"
        r"training\s+agenda|simulation|tabletop|next\s+week)\b|"
        r"\b(?:agenda|menu|timetable)\b|"
        r"\b(?:agenda\s+latihan|minggu\s+depan)\b|"
        r"(?:会议议程|會議議程|下周|下週|演习|演習)",
        value,
    ))
    return admin_topic or bool(
        _logical_clauses(value)
        and all(_planned_hypothetical_clause(clause)
                for clause in _logical_clauses(value))
    )


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


def _is_explicit_false_online_harm_rumour_clause(clause: str) -> bool:
    value = str(clause or "").casefold()
    expressly_false = bool(re.search(
        r"\b(?:false|fabricated|untrue|inaccurate|hoax)\s+"
        r"(?:online\s+)?(?:rumou?r|claim|post|story)\b|"
        r"\b(?:rumou?r|claim|post|story)\b[^.!?;]{0,55}"
        r"\b(?:is|was)\s+(?:false|fabricated|untrue|inaccurate|a\s+hoax)\b",
        value,
    ))
    online = bool(re.search(
        r"\b(?:facebook|social\s+media|online|viral|website|public\s+post)\b",
        value,
    ))
    harmful_minor_content = bool(re.search(
        rf"\b{_MINOR}\b[^.!?;]{{0,90}}\b"
        r"(?:died|dead|killed|injured|missing)\b|"
        r"\b(?:died|dead|killed|injured|missing)\b[^.!?;]{0,90}"
        rf"\b{_MINOR}\b",
        value,
    ))
    return expressly_false and online and harmful_minor_content


def _affirmed_person_harm_clause(clause: str) -> bool:
    """Detect an actual person-harm assertion, including death wording."""
    value = str(clause or "").casefold()
    if not value or _planned_hypothetical_clause(value):
        return False
    physical, _ = _physical_incident_flags(value)
    death_or_missing = bool(re.search(
        rf"\b{_PERSON}\b[^.!?;]{{0,100}}\b"
        r"(?:died|dead|killed|fatal(?:ly)?|missing)\b|"
        r"\b(?:died|dead|killed|fatal(?:ly)?|missing)\b"
        rf"[^.!?;]{{0,100}}\b{_PERSON}\b",
        value,
    ))
    return physical or death_or_missing


def _false_online_harm_rumour_is_only_harm_claim(text: str) -> bool:
    """Return true only when every person-harm claim is expressly false.

    A communications case may quote a false death/injury rumour without
    becoming an emergency incident.  A mixed case is different: one real harm
    assertion must retain its own severity even when a second online claim is
    explicitly false.  ``unverified`` is deliberately not treated as false.
    """
    clauses: list[str] = []
    for logical in _logical_clauses(str(text or "").casefold()):
        clauses.extend(
            part.strip(" ,:")
            for part in re.split(
                r",\s*(?:while|whereas|and)\s+|\b(?:while|whereas)\b",
                logical,
            )
            if part.strip(" ,:")
        )
    found_false_harm = False
    for clause in clauses:
        if _is_explicit_false_online_harm_rumour_clause(clause):
            found_false_harm = True
            # If the same segment contains a separate affirmed incident before
            # the false-rumour marker, do not let the later qualifier erase it.
            marker = re.search(
                r"\b(?:false|fabricated|untrue|inaccurate|hoax)\b|"
                r"\b(?:rumou?r|claim|post|story)\b",
                clause,
            )
            if marker and _affirmed_person_harm_clause(clause[:marker.start()]):
                return False
            continue
        if _affirmed_person_harm_clause(clause):
            return False
    return found_false_harm


def _mask_exercise_hazard_terms(text: str) -> str:
    """Remove hazard words that only name a drill, not a live hazard."""
    return re.sub(
        r"\b(?:fire|bomb[- ]?threat|evacuation|emergency)\s+"
        r"(?:drill|exercise|training|simulation)\b|"
        r"\b(?:latihan|simulasi)\s+(?:kebakaran|ancaman\s+bom|"
        r"pemindahan|kecemasan)\b",
        " exercise ",
        str(text or "").casefold(),
    )


def _active_hazard_present(text: str) -> bool:
    value = _mask_exercise_hazard_terms(text)
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
        live_hazard_during_exercise = bool(re.search(
            r"\b(?:real|actual)\s+(?:fire|smoke|flames?|gas\s+leak|"
            r"chemical\s+spill)\b|"
            r"\b(?:fire|smoke|gas\s+leak|chemical\s+spill)\b"
            r"[^.!?;]{0,90}\b(?:broke\s+out|spreading|filling|"
            r"currently|ongoing|active|right\s+now)\b|"
            r"\b(?:broke\s+out|spreading|filling|currently|ongoing)\b"
            r"[^.!?;]{0,90}\b(?:fire|smoke|gas\s+leak|chemical\s+spill)\b|"
            r"\bkebakaran\s+sebenar\b|"
            r"\b(?:kebakaran|asap|kebocoran\s+gas|tumpahan\s+kimia)\b"
            r"[^.!?;]{0,90}\b(?:berlaku|sedang\s+merebak|merebak|"
            r"sedang\s+memenuhi|masih\s+aktif)\b",
            clause,
        ))
        if _planned_hypothetical_clause(clause) and not (
            live_hazard_during_exercise
        ):
            continue
        # "We do not know whether danger remains" is a question-triggering
        # unknown, not evidence that danger is present.
        if status_unknown.search(clause):
            continue
        if resolved.search(clause):
            continue
        if (
            live_hazard_during_exercise
            or active.search(clause)
            or appeared_on_site.search(clause)
        ):
            return True
    return False


def _has_affirmed_source_statement(
    text: str,
    positive: re.Pattern[str],
) -> bool:
    """Match only an affirmative source statement, never uncertainty.

    Safety and privacy floors must not treat a phrase inside a question,
    ``cannot confirm`` scope, or ``not ruled out`` scope as established fact.
    """
    uncertain = re.compile(
        r"\b(?:cannot|can't|could\s+not|couldn't|do\s+not|don't)\s+"
        r"(?:confirm|verify|establish|know|rule\s+out)\b|"
        r"\b(?:not|never)\s+(?:confirmed|verified|established|known|ruled\s+out)\b|"
        r"\b(?:unconfirmed|unknown|unclear|uncertain|not\s+sure|whether|"
        r"has\s+(?:not\s+)?been\s+ruled\s+out|"
        r"have\s+(?:not\s+)?been\s+ruled\s+out|false\s+that)\b|"
        r"\b(?:tidak|belum)\s+(?:dapat\s+)?(?:disahkan|dipastikan|diketahui)\b|"
        r"\b(?:tidak\s+pasti|sama\s+ada)\b|"
        r"(?:无法确认|無法確認|不能确认|不能確認|尚未确认|尚未確認|"
        r"不确定|不確定|是否|未排除)",
        re.IGNORECASE,
    )
    for match in re.finditer(
        r"[^.!?;。！？；\n]+[.!?;。！？；]?",
        str(text or ""),
    ):
        clause = match.group(0).strip()
        if not clause or not positive.search(clause):
            continue
        if "?" in clause or "？" in clause or uncertain.search(clause):
            continue
        return True
    return False


_NO_UNMET_EMERGENCY_ASSERTION = re.compile(
    r"\b(?:there\s+is\s+)?no\s+(?:unmet\s+)?(?:immediate\s+)?"
    r"(?:medical\s+)?emergency(?:\s+(?:now|at\s+present))?\b|"
    r"\bno\s+(?:immediate|active|continuing)\s+danger\b|"
    r"\b(?:immediate\s+)?danger\s+(?:is\s+)?not\s+(?:present|ongoing)\b|"
    r"\btiada\s+(?:bahaya\s+segera|kecemasan\s+perubatan\s+yang\s+belum\s+ditangani)\b|"
    r"(?:没有|沒有)(?:即时|即時)?(?:危险|危險)|"
    r"(?:没有|沒有)未处理的医疗紧急情况",
    re.IGNORECASE,
)


def _explicitly_no_unmet_emergency(text: str) -> bool:
    """Detect an express first-party resolution of present safety urgency.

    ``stable`` by itself is not enough. The source must say there is no
    immediate danger or no unmet medical/emergency need, so a conservative
    semantic severity label cannot reopen a question already answered by the
    operator.
    """
    return _has_affirmed_source_statement(
        text,
        _NO_UNMET_EMERGENCY_ASSERTION,
    )


def _explicitly_all_pupils_safe_and_supervised(text: str) -> bool:
    return _has_affirmed_source_statement(
        text,
        re.compile(
            r"\ball\s+(?:pupils?|students?|children)\s+(?:are|were)\s+"
            r"safe\s+and\s+(?:accounted\s+for\s+and\s+)?supervised\b|"
            r"\ball\s+(?:pupils?|students?|children)\s+(?:are|were)\s+"
            r"supervised\s+and\s+safe\b",
            re.IGNORECASE,
        ),
    )


def _explicitly_contained_structural_hazard(text: str) -> bool:
    """Require both access control and affirmative occupancy clearance."""
    value = str(text or "").casefold()
    structural_fall = bool(re.search(
        r"\b(?:roof|ceiling|wall|beam|panel)\b[^.!?;]{0,75}"
        r"\b(?:collaps(?:e|ed)|fell|fallen|caved\s+in|gave\s+way)\b|"
        r"\b(?:collaps(?:e|ed)|caved\s+in|gave\s+way)\b"
        r"[^.!?;]{0,75}\b(?:roof|ceiling|wall|beam|panel)\b",
        value,
    ))
    affected_space_closed = bool(re.search(
        r"\b(?:the\s+)?(?:room|classroom|affected\s+area|area|block)\s+"
        r"(?:is|was|has\s+been|had\s+been)\s+"
        r"(?:closed|cordoned\s+off|isolated|secured)\b",
        value,
    ))
    occupancy_cleared = bool(re.search(
        r"\b(?:the\s+)?(?:room|classroom|affected\s+area|area|block)\s+"
        r"(?:was|has\s+been|had\s+been)\s+(?:cleared|evacuated|confirmed\s+empty)\b|"
        r"\bno\s+(?:one|person|pupil|student|child|staff\s+member)\s+"
        r"(?:is|was|remains?)\s+(?:inside|in\s+the\s+(?:room|area|block))\b|"
        r"\b(?:all|every)\s+(?:occupants?|pupils?|students?|children|staff)\s+"
        r"(?:are|were|have\s+been|had\s+been)\s+"
        r"(?:evacuated|cleared|accounted\s+for)\b|"
        r"\beveryone\s+(?:is|was|has\s+been)\s+(?:clear|accounted\s+for)\b",
        value,
    ))
    unresolved_exposure = bool(re.search(
        r"\b(?:people|persons?|pupils?|students?|children|staff)\s+"
        r"(?:remain|are\s+still|were\s+still|may\s+be)\s+"
        r"(?:inside|in\s+the\s+room|trapped\s+inside)\b|"
        r"\b(?:pupils?|students?|children|staff)\s+may\s+be\s+trapped\b|"
        r"\b(?:further\s+(?:collapse|panels?\s+falling)|still\s+unsafe|"
        r"may\s+still\s+be\s+unsafe|active\s+danger|immediate\s+danger|"
        r"door\s+(?:is\s+)?jammed)\b|"
        r"\b(?:(?:have|has)\s+not|haven't|hasn't|cannot|can't|"
        r"do\s+not|don't)\s+(?:checked?|confirm(?:ed)?|know)\s+whether\s+"
        r"(?:anyone|people|pupils?|students?|children|staff)\s+"
        r"(?:remain|are|were)?\s*(?:still\s+)?inside\b",
        value,
    ))
    return bool(
        structural_fall
        and affected_space_closed
        and occupancy_cleared
        and not unresolved_exposure
    )


def _explicitly_resolved_or_contained_safety(text: str) -> bool:
    """Recognise narrow first-party evidence that immediate exposure ended.

    ``safe and supervised`` resolves pupil welfare, but does not by itself
    resolve a separate fire, traffic or structural hazard. Structural damage
    is considered contained only when the affected space is closed *and* the
    source affirmatively confirms that occupants were cleared/accounted for.
    """
    value = str(text or "").casefold()
    return bool(
        _explicitly_no_unmet_emergency(value)
        or _explicitly_all_pupils_safe_and_supervised(value)
        or _explicitly_contained_structural_hazard(value)
    )


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
            r"\b(?:there\s+(?:is|was)\s+)?(?:still\s+)?(?:immediate\s+)?"
            r"(?:danger|hazard)\b|"
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
    *,
    semantic_authoritative: bool = False,
) -> tuple[str, str, set[str], set[str]]:
    """Apply deterministic safety floors without re-interpreting live semantics.

    A checked semantic result is a *planning proposal*, never an execution
    authorisation. The deterministic layer may add safety requirements and
    will still govern every resulting action, but a finite lexical recogniser
    must not delete a correctly understood open-input concept merely because
    it has not seen that wording before. Lexical fallback results retain the
    former corroboration ceiling.
    """
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
        r"\b(?:water\s+(?:pipe\s+)?leak|power\s+outage|broken\s+(?:pipe|gate|roof)|"
        r"(?:water\s+)?pipe\s+burst|burst\s+(?:water\s+)?pipe|"
        r"classrooms?\s+(?:cannot|can\s+not)\s+be\s+used|"
        r"building\s+damage|toilet\s+fault|electrical\s+fault)\b|"
        r"\b(?:roof|ceiling|wall|beam|panel)\b[^.!?;]{0,75}"
        r"\b(?:collaps(?:e|ed)|fell|fallen|caved\s+in|gave\s+way)\b|"
        r"\b(?:collaps(?:e|ed)|caved\s+in|gave\s+way)\b"
        r"[^.!?;]{0,75}\b(?:roof|ceiling|wall|beam|panel)\b|"
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
        r"\b(?:parent|guardian|ibu\s+bapa|penjaga)\b|家长|家長|监护人|監護人", low,
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
    if not semantic_authoritative:
        for signal, supported in predicates.items():
            if not supported:
                keep.discard(signal)
        if "minor_involved" in keep and not (
            (physical and physical_minor) or missing or safeguarding
            or student_mentioned
        ):
            keep.discard("minor_involved")
        if "evidence_preservation_needed" in keep and not (
            physical or safeguarding or cyber
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
    if (
        not semantic_authoritative
        and family in category_supported
        and not category_supported[family]
    ):
        family = "general_school_admin"
    if semantic_authoritative and _explicitly_nonincident_admin_request(text):
        keep.difference_update({
            "active_danger", "injury_or_illness", "minor_involved",
            "person_missing", "safeguarding_concern",
            "external_help_may_be_required", "possible_regulatory_trigger",
            "financial_value_involved", "evacuation_accountability",
            "food_water_exposure", "data_security_incident",
            "guardian_notification_relevant", "evidence_preservation_needed",
        })
        if family in {
            "safety_emergency", "health_medical", "safeguarding_welfare",
            "facilities_environment", "transport_travel", "food_hygiene",
            "cyber_data", "finance_procurement", "records_regulatory",
        }:
            family = "general_school_admin"
        severity = "low"
        stakeholders.difference_update({
            "medical_services", "malaysia_emergency_services_999",
            "fire_and_rescue", "police", "education_authority",
        })

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
    false_harm_rumour_only = bool(
        _false_online_harm_rumour_is_only_harm_claim(low) and not any_material
    )
    if false_harm_rumour_only:
        # A semantic model may correctly notice that the quoted words describe
        # death or injury but incorrectly assign that severity to the school
        # case.  The source explicitly says the online claim is false, and no
        # independent incident predicate above corroborates it.  Preserve the
        # public/privacy response while removing only the unsupported impact
        # signals that would expand the pack into emergency/regulatory work.
        family = "communications_reputation"
        severity = "medium"
        keep.difference_update({
            "active_danger", "injury_or_illness", "person_missing",
            "safeguarding_concern", "external_help_may_be_required",
            "possible_regulatory_trigger", "food_water_exposure",
            "evacuation_accountability", "guardian_notification_relevant",
            "evidence_preservation_needed",
        })
        keep.add("public_interest")
        if student_mentioned:
            keep.add("minor_involved")
        stakeholders.difference_update({
            "medical_services", "malaysia_emergency_services_999",
            "fire_and_rescue", "police", "education_authority", "guardian",
        })
        stakeholders.update({"public_media", "school_leadership"})
    resolved_or_contained = _explicitly_resolved_or_contained_safety(low)
    active_signal_resolved = bool(
        "active_danger" not in keep
        or _explicitly_no_unmet_emergency(low)
        or _explicitly_contained_structural_hazard(low)
    )
    if (
        semantic_authoritative
        and severity in {"high", "critical"}
        and resolved_or_contained
        and active_signal_resolved
        and not urgent
    ):
        # High-impact semantic labels are proposals.  When the source itself
        # says every pupil is safe/supervised or a structural-fall area is
        # closed and cleared, uncorroborated emergency signals cannot override
        # that evidence. A distinct semantic active-danger signal survives
        # unless the source explicitly resolves that hazard. Retain the
        # material incident and its operational plan, but avoid unsupported
        # emergency contacts and regulatory expansion.
        severity = "medium" if any_material else "low"
        for signal in (
            "active_danger", "injury_or_illness", "person_missing",
            "safeguarding_concern", "external_help_may_be_required",
            "possible_regulatory_trigger", "evacuation_accountability",
            "data_security_incident", "guardian_notification_relevant",
            "evidence_preservation_needed",
        ):
            if not predicates.get(signal, False):
                keep.discard(signal)
        if not active:
            stakeholders.difference_update({
                "malaysia_emergency_services_999", "fire_and_rescue", "police",
            })
        if not regulatory:
            stakeholders.discard("education_authority")
        if not physical:
            stakeholders.discard("medical_services")
    if (
        not semantic_authoritative
        and severity in {"high", "critical"}
        and not urgent
    ):
        severity = "medium" if any_material else "low"

    if not semantic_authoritative:
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
        r"\bno\s+(?:(?:baseline|follow[- ]up|outcome|impact)\s+"
        r"(?:or|and)\s+)*(?:baseline|follow[- ]up|outcome|impact)\s+"
        r"(?:data|evidence|measurements?|results?)(?:\s+(?:has|have|had|was|were|is|are))?"
        r"\s*(?:not\s+)?(?:been\s+)?(?:collected|measured|gathered|available)\b|"
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
        r"\bno\s+(?:(?:baseline|follow[- ]up|outcome|impact)\s+"
        r"(?:or|and)\s+)*(?:baseline|follow[- ]up|outcome|impact)\s+"
        r"(?:data|evidence|measurements?|results?)(?:\s+(?:has|have|had|was|were|is|are))?"
        r"\s*(?:not\s+)?(?:been\s+)?(?:collected|measured|gathered|available)\b|"
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
    # Ordinary staff phrasing often expresses the causal link before the
    # monitoring verb ("explain his family background so teachers can monitor
    # him"). Do not require an extra adverb such as "closely" before treating
    # status-based surveillance as differential treatment.
    if re.search(
        r"\b(?:family|household|socioeconomic|social|financial)\s+"
        r"(?:background|income|status|standing)\b[^.!?;]{0,120}"
        r"\b(?:so|therefore|thus|hence|to\s+(?:let|help|allow))\b"
        r"[^.!?;]{0,80}\b(?:monitor|watch|track|flag|surveil)\b",
        value,
    ):
        return True
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


def _status_based_content_manipulation(text: str) -> bool:
    """Block requests to alter records because of rank or committee status."""
    value = str(text or "")
    status = re.compile(
        r"\b(?:dato['â€™]?|datuk|datin|tan\s+sri|tunku)\b|"
        r"\bPIBG\b[^.!?;\n]{0,60}\b(?:chair(?:man|person)?|president|"
        r"committee\s+member|member)\b|"
        r"\b(?:chair(?:man|person)?|president|committee\s+member|member)\b"
        r"[^.!?;\n]{0,60}\bPIBG\b",
        re.IGNORECASE,
    )
    manipulation = re.compile(
        r"\b(?:soften|tone\s+down|omit|remove|exclude|hide|suppress|withhold|"
        r"leave\s+out|play\s+down|downplay|make\s+(?:it\s+)?(?:gentler|"
        r"warmer|less\s+serious))\b|"
        r"\b(?:lembutkan|kurangkan|tinggalkan|keluarkan|sembunyikan)\b",
        re.IGNORECASE,
    )
    safe_prohibition = re.compile(
        r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|cannot)\b"
        r"[^.!?;]{0,70}\b(?:soften|omit|remove|exclude|hide|suppress|"
        r"withhold|leave\s+out|downplay)\b",
        re.IGNORECASE,
    )
    if safe_prohibition.search(value):
        return False
    status_matches = list(status.finditer(value))
    return any(
        min(abs(change.start() - item.end()), abs(item.start() - change.end()))
        <= 260
        for change in manipulation.finditer(value)
        for item in status_matches
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
    "school_community": (
        r"\b(?:all\s+parents|parent\s+group|school\s+community|all\s+staff|"
        r"(?:year\s+\d+\s+)?class\s+(?:whatsapp\s+)?group)\b|"
        r"全体家长|全體家長|家长群|家長群"
    ),
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


def _parent_notice_explicitly_negated(text: str) -> bool:
    """Return true when the user expressly excludes a parent deliverable.

    A negative deliverable constraint is different from ``do not send``: the
    latter keeps a draft and removes only the release action.  This predicate
    is intentionally narrow and covers only verbs that create/include the
    parent document, in English and Malay.
    """
    value = str(text or "").casefold()
    return bool(re.search(
        r"\b(?:do\s+not|don't|never|must\s+not|no\s+need\s+to)\b"
        r"[^.!?;\n]{0,35}\b(?:prepare|draft|write|create|make|provide|include)\b"
        r"[^.!?;\n]{0,45}\b(?:parent|guardian)s?\s+"
        r"(?:notice|notification|message|update|letter)\b|"
        r"\b(?:do\s+not|don't|never|must\s+not)\b[^.!?;\n]{0,65}"
        r"\b(?:notice|notification|message|update|letter)\b"
        r"[^.!?;\n]{0,30}\b(?:to|for)\s+(?:the\s+)?(?:parent|guardian)s?\b|"
        r"\bno\s+(?:parent|guardian)\s+(?:notice|notification|message|update|letter)\b|"
        r"\b(?:jangan|tidak\s+perlu|usah)\b[^.!?;\n]{0,35}"
        r"\b(?:sediakan|hasilkan|buat|sertakan)\b[^.!?;\n]{0,45}"
        r"\b(?:makluman|notis|mesej|surat)\b[^.!?;\n]{0,30}"
        r"\b(?:ibu\s+bapa|penjaga)\b",
        value,
    ))


def _explicit_private_guardian_release_spec(text: str) -> dict | None:
    """Recover a named-family release even when initials contain full stops."""
    source = str(text or "")
    recipient = (
        r"(?:(?:[A-Za-z](?:\.[A-Za-z]){1,5}\.?|"
        r"[A-Za-z][A-Za-z'\u2019-]{1,40})['\u2019]s\s+)?"
        r"(?:parent|guardian)\b|"
        r"(?:the\s+)?(?:student|pupil|child)['\u2019]s\s+(?:parent|guardian)\b"
    )
    action = re.compile(
        r"\b(?:send|message|whatsapp|email|notify|contact|inform|tell|"
        r"hantar|hubungi|maklumkan)\b[^!?;\n]{0,180}\b" + recipient,
        re.IGNORECASE,
    )
    for match in action.finditer(source):
        prefix = source[max(0, match.start() - 140):match.start()]
        window = source[max(0, match.start() - 35):match.end()]
        # ``WhatsApp message`` and ``email`` are often nouns inside a drafting
        # request.  They become an external action only when independently
        # commanded (for example, ``and send``), never merely because a draft
        # names its intended private recipient.
        if re.search(
            r"\b(?:draft|prepare|write|create|compose)\b[^.!?;\n]{0,120}$",
            prefix,
            re.IGNORECASE,
        ) and not re.search(
            r"\band(?:\s+then)?\s+$", prefix, re.IGNORECASE,
        ):
            continue
        if re.search(
            r"\b(?:do\s+not|don't|never|must\s+not|jangan|tidak\s+boleh)\b"
            r"[^!?;\n]{0,35}$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        if re.search(
            r"\b(?:all|every)\s+parents?\b|\bparent\s+(?:group|community)\b|"
            r"\bsemua\s+ibu\s+bapa\b",
            window,
            re.IGNORECASE,
        ):
            continue
        channel = (
            "whatsapp" if re.search(r"\bwhatsapp\b", window, re.IGNORECASE)
            else "email" if re.search(r"\b(?:email|e-mail|emel)\b", window, re.IGNORECASE)
            else "other"
        )
        return {
            "recipient_type": "guardian",
            "channel": channel,
            "linked_artifact_role": "private_parent_notice",
        }
    return None


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


def _source_negated_output_roles(text: str) -> set[str]:
    """Return closed-catalog outputs the user explicitly said not to create.

    This differs from a release negation. "Draft the notice but do not send
    it" keeps the draft. Only a negated creation verb, such as "do not prepare
    an incident report", suppresses an artifact. Each negative segment stops
    before "only/instead/but" so its requested replacement remains intact.
    """
    source = str(text or "")
    negative_segments = [
        match.group(0).casefold()
        for match in re.finditer(
            r"\b(?:do\s+not|don't|never|must\s+not|no\s+need\s+to|"
            r"jangan|tidak\s+perlu|usah)\b"
            r"[^.!?;\n]{0,220}?"
            r"(?=(?:,\s*)?(?:\b(?:but|instead|only|just|rather\s+than|"
            r"tetapi|sebaliknya|hanya)\b)|[.!?;\n]|$)",
            source,
            re.IGNORECASE,
        )
    ]
    creation = re.compile(
        r"\b(?:prepare|draft|write|create|make|provide|include|produce|"
        r"generate|repeat|recreate|redo|duplicate|sediakan|hasilkan|buat|"
        r"sertakan)\b",
        re.IGNORECASE,
    )
    role_patterns: tuple[tuple[set[str], str], ...] = (
        (
            {"internal_incident_report"},
            r"\b(?:internal\s+)?(?:incident|accident)\s+report\b|"
            r"\blaporan\s+(?:dalaman\s+)?(?:insiden|kemalangan)\b",
        ),
        (
            {"education_authority_report", "education_authority_request"},
            r"\b(?:report|request|laporan|permohonan)\b[^.!?;\n]{0,80}"
            r"\b(?:to|for|kepada|untuk)\s+(?:the\s+)?"
            r"(?:ppd|jpn|moe|district\s+education\s+office|"
            r"education\s+authority|pejabat\s+pendidikan(?:\s+daerah)?|"
            r"kementerian\s+pendidikan)\b|"
            r"\b(?:ppd|jpn|district\s+education|education\s+authority)\s+"
            r"(?:report|request)\b",
        ),
        (
            {"public_communication_draft"},
            r"\b(?:public|media)\s+(?:holding\s+)?statement\b|"
            r"\b(?:facebook|public)\s+(?:post|notice|message)\b",
        ),
        (
            {"private_parent_notice", "school_parent_notice"},
            r"\b(?:parent|guardian)s?\s+"
            r"(?:notice|notification|message|update|letter)\b|"
            r"\b(?:notice|notification|message|update|letter)\b"
            r"[^.!?;\n]{0,45}\b(?:to|for)\s+(?:the\s+)?"
            r"(?:parent|guardian)s?\b|"
            r"\b(?:makluman|notis|mesej|surat)\b[^.!?;\n]{0,35}"
            r"\b(?:ibu\s+bapa|penjaga)\b",
        ),
        (
            {"finance_procurement_memo"},
            r"\b(?:finance|financial|accounts?|reconciliation|procurement)\s+"
            r"(?:memo|memorandum|report)\b|"
            r"\b(?:memo|memorandum)\s+(?:kewangan|akaun|perolehan)\b",
        ),
        (
            {"evidence_preservation_log"},
            r"\b(?:evidence(?:[- ]preservation)?|chain[- ]of[- ]custody)\s+"
            r"(?:log|record)\b|\blog\s+(?:bukti|keterangan)\b",
        ),
        (
            {"transport_response_plan"},
            r"\b(?:school\s+)?transport\s+(?:response\s+)?plan\b|"
            r"\bpelan\s+(?:tindak\s+balas\s+)?pengangkutan\b",
        ),
        (
            {"student_support_plan"},
            r"\b(?:student|pupil|learning|academic)\s+support\s+plan\b|"
            r"\bpelan\s+sokongan\b",
        ),
        (
            {"event_action_plan"},
            r"\b(?:event|school|internal)\s+action\s+plan\b|"
            r"\bpelan\s+tindakan\b",
        ),
        (
            {"safeguarding_action_plan"},
            r"\bsafeguarding\s+(?:action\s+)?plan\b|"
            r"\bpelan\s+perlindungan\b",
        ),
        (
            {"regulatory_notification_assessment"},
            r"\bregulatory\s+notification\s+assessment\b|"
            r"\bpenilaian\s+pemberitahuan\s+kawal\s+selia\b",
        ),
    )
    roles: set[str] = set()
    for segment in negative_segments:
        if not creation.search(segment):
            continue
        for mapped_roles, pattern in role_patterns:
            if re.search(pattern, segment, re.IGNORECASE):
                roles.update(mapped_roles)
    return roles


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
    negated_roles = _source_negated_output_roles(source)
    outputs: list[dict] = []
    custom: list[dict] = []
    seen_roles: set[str] = set()
    seen_labels: set[str] = set()

    def add_role(
        role: str,
        *,
        audience: str = "internal",
        recipient_type: str = "school_staff",
        channel: str = "",
    ) -> None:
        if role in negated_roles:
            return
        if role in seen_roles:
            if channel:
                for output in outputs:
                    if output.get("artifact_role") == role and not output.get("channel"):
                        output["channel"] = channel
            return
        seen_roles.add(role)
        outputs.append({
            "artifact_role": role,
            "audience": audience,
            "recipient_type": recipient_type,
            "source_named": True,
            **({"channel": channel} if channel else {}),
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
        r"\b(?:internal\s+)?(?:finance|financial|accounts?|reconciliation|"
        r"procurement)\s+(?:memo|memorandum|report)\b|"
        r"\b(?:memo|memorandum)\s+(?:kewangan|akaun|perolehan)"
        r"(?:\s+dalaman)?\b|"
        r"\blaporan\s+kewangan\s+dalaman\b",
        low,
    ):
        add_role("finance_procurement_memo", recipient_type="school_leadership")
    if re.search(
        r"\b(?:(?:all|whole)[- ]?)?(?:staff|teacher)s?\s+"
        r"(?:internal\s+)?(?:notice|briefing)(?:\s+(?:draft|note))?\b|"
        r"\b(?:notis|memo)\s+(?:dalaman\s+)?(?:kepada\s+)?"
        r"(?:semua\s+)?(?:staf|guru)\b|"
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
        r"\b(?:school\s+)?transport\s+(?:response\s+)?plan\b|"
        r"\bpelan\s+(?:tindak\s+balas\s+)?pengangkutan\b",
        low,
    ):
        add_role("transport_response_plan")
    if re.search(
        r"\b(?:investigate|review|look\s+into|assess)\b[^.!?;\n]{0,110}"
        r"\b(?:student|pupil)s?\b[^.!?;\n]{0,90}"
        r"\b(?:behavio(?:u)?r|conduct|misconduct|discipline|bully|fight|"
        r"steal|post(?:ing|ed|s)?\s+(?:photos?|videos?))\b|"
        r"\b(?:siasat|semak)\b[^.!?;\n]{0,100}\b(?:murid|pelajar)\b"
        r"[^.!?;\n]{0,80}\b(?:tingkah\s+laku|disiplin|bergaduh|mencuri)\b",
        low,
    ):
        add_role("discipline_investigation_report")
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
        r"\b(?:staff|teacher)\s+(?:(?:evacuation|emergency|safety|drill|"
        r"exercise|first[- ]aid)\s+)?"
        r"checklist\b|"
        r"\b(?:bomb[- ]?threat|evacuation|emergency|safety)\s+drill\b"
        r"[^.!?\n]{0,120}\b(?:staff\s+)?(?:checklist|plan)\b",
        low,
    ):
        # This is a planned internal exercise artifact, not evidence of a live
        # incident and not a reason to create an emergency-service script.
        add_role("event_action_plan")
    if re.search(
        r"\b(?:prepare|draft|write|create|produce|generate|need)\b[^.\n]{0,45}"
        r"\b(?:speech|address|opening\s+remarks?|closing\s+remarks?|"
        r"emcee\s+script)\b|"
        r"\b(?:speech|address|remarks?)\s+(?:draft|script|text)\b|"
        r"\b(?:sediakan|tulis|hasilkan)\b[^.\n]{0,45}\b(?:teks\s+ucapan|"
        r"ucapan\s+(?:perasmian|penutup)|skrip\s+pengacara)\b|"
        r"(?:准备|準備|起草|撰写|撰寫|生成)[^。\n]{0,24}"
        r"(?:演讲稿|演講稿|致辞|致辭|讲话稿|講話稿)",
        low,
    ):
        add_role(
            "speech_or_address", audience="school_community",
            recipient_type="school_community",
        )
    if re.search(
        r"\b(?:meeting|committee|pta|staff)\s+minutes\b|"
        r"\bminutes\s+of\s+(?:the\s+)?(?:pta|committee|staff)?\s*meeting\b|"
        r"\b(?:prepare|draft|write|create|produce|generate)\b[^.\n]{0,50}"
        r"\bminutes\b[^.\n]{0,50}\bmeeting\b|"
        r"\bminit\s+mesyuarat\b|(?:会议记录|會議記錄|会议纪要|會議紀要)",
        low,
    ):
        add_role("meeting_minutes")
    if re.search(
        r"\b(?:duty|teacher|staff)\s+roster\b|\broster\s+of\s+duties\b|"
        r"\bjadual\s+(?:bertugas|tugas)\b|(?:值勤表|值日表|教师值班表|教師值班表)",
        low,
    ):
        add_role("duty_roster")
    timetable_pattern = (
        r"\b(?:class|exam|examination|school|lesson)?\s*"
        r"(?:timetable|schedule)\b|\bjadual\s+(?:waktu|peperiksaan|kelas)\b|"
        r"(?:时间表|時間表|课程表|課程表|考试时间表|考試時間表)"
    )
    if is_requested_output_mention(low, timetable_pattern):
        add_role("timetable_or_schedule")
    if re.search(
        r"\b(?:curriculum|teaching|learning|lesson|class)\s+continuity\s+plan\b|"
        r"\bcontinuity\s+plan\b[^.!?;\n]{0,80}\b(?:teacher|class|lesson|"
        r"curriculum|learning)\b|\bpelan\s+kesinambungan\s+"
        r"(?:kurikulum|pengajaran|pembelajaran)\b|(?:课程连续性计划|課程連續性計劃|"
        r"教学延续计划|教學延續計劃)",
        low,
    ):
        add_role("curriculum_continuity_plan", recipient_type="school_leadership")
    if re.search(
        r"\b(?:medical|clinical)\s+handover(?:\s+(?:script|note|draft))?\b|"
        r"\b(?:handover|serahan)\s+(?:to\s+)?(?:medical\s+services?|"
        r"hospital|clinic)\b|\bskrip\s+serahan\s+perubatan\b",
        low,
    ):
        add_role(
            "medical_handover_script",
            recipient_type="medical_services",
        )
    authority_report_reverse = bool(re.search(
        r"\b(?:anonymous\s+)?report\b[^.!?;\n]{0,60}\b(?:to|for)\s+"
        r"(?:the\s+)?(?:district\s+education\s+office|education\s+authority|"
        r"education\s+office|ppd|jpn|moe)\b|"
        r"\blaporan(?:\s+tanpa\s+nama)?\b[^.!?;\n]{0,60}"
        r"\b(?:kepada|untuk)\s+(?:ppd|jpn|kementerian\s+pendidikan|"
        r"pejabat\s+pendidikan)\b",
        low,
    ))
    if authority_report_reverse or re.search(
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
        channel = (
            "facebook" if re.search(r"\bfacebook\b|脸书|臉書", low)
            else "website" if re.search(r"\bwebsite\b|网站|網站", low)
            else "social_media"
        )
        add_role(
            "public_communication_draft", audience="public",
            recipient_type="public_media", channel=channel,
        )

    parent_document = bool(re.search(
        r"\b(?:draft|prepare|write|create|compose)\b[^.!?;\n]{0,65}"
        r"\bemail\b[^.!?;\n]{0,65}\b(?:all|every)\s+parents?\b|"
        r"\bparent(?:s|al)?\s+(?:notice|notification|update|message|draft|letter)\b|"
        r"\b(?:share|inform|tell|notify)\b[^.!?;\n]{0,120}"
        r"\b(?:to|with)\s+(?:all|every)\s+(?:school\s+)?parents?\b|"
        r"\b(?:notice|notification|update|message|letter|draft)\s+(?:draft\s+)?"
        r"(?:to|for)\s+(?:(?:all|every)\s+)?(?:school\s+)?parents?\b|"
        r"\b(?:makluman|notis|mesej)\s+"
        r"(?:kepada\s+)?(?:ibu\s+bapa|penjaga)\b|"
        r"\b(?:notice|notification|update|message|letter|draft)\b[^.!?;\n]{0,45}"
        r"\b(?:to|for)\s+(?:the\s+)?(?:student|pupil|child)['\u2019]s\s+"
        r"(?:parent|guardian)\b|"
        r"\b(?:notice|notification|update|message|letter|draft)\b[^.!?;\n]{0,45}"
        r"\b(?:to|for)\s+(?:[a-z][a-z'\u2019-]{0,40}\s+){0,3}"
        r"[a-z][a-z'\u2019-]{0,40}['\u2019]s\s+(?:own\s+)?"
        r"(?:parent|guardian)\b|"
        r"\b(?:notice|notification|update|message|letter|draft)\b[^.!?;\n]{0,45}"
        r"\b(?:to|for)\s+(?:(?:[a-z](?:\.[a-z]){1,5}\.?|"
        r"[a-z][a-z'\u2019-]{1,40})['\u2019]s\s+)?(?:parent|guardian)\b|"
        r"(?:家长|家長)(?:通知|消息|信息|信函)|"
        r"(?:通知|消息|信息|信函)(?:给|給)?(?:家长|家長)", low,
    ))
    if parent_document and not _parent_notice_explicitly_negated(source):
        explicit_private_parent = bool(re.search(
            r"\bprivate\b[^.!?;\n]{0,35}\b(?:message|notice|notification|"
            r"update|letter)\b|"
            r"\b(?:own|the\s+(?:student|pupil|child)['\u2019]s)\s+"
            r"(?:parent|guardian)\b|"
            r"\b(?:their|his|her)\s+(?:parents?|guardians?)\b|"
            r"\b[A-Za-z][A-Za-z'\u2019-]{1,40}['\u2019]s\s+(?:own\s+)?"
            r"(?:parent|guardian)\b",
            source,
            re.IGNORECASE,
        ))
        broad = not explicit_private_parent and (
            bool(re.search(
                r"\b(?:all|every)\s+(?:school\s+)?parents?\b|\bparents\b|"
                r"\bparent\s+(?:group|community)\b|\b(?:all|whole)\s+school\b|"
                r"\b(?:semua|seluruh)\s+ibu\s+bapa\b|"
                r"全体家长|全體家長|家长群|家長群|全校", low,
            ))
            or bool(re.search(
                r"\b(?:event|bazaar|charity|recycling\s+day|facility|"
                r"classroom|relocation)\b|"
                r"义卖|義賣|活动|活動|课室|課室", low,
            ))
        )
        parent_channel = (
            "whatsapp" if re.search(r"\bwhatsapp\b", low)
            else "email" if re.search(r"\b(?:email|e-mail|emel)\b", low)
            else "sms" if re.search(r"\b(?:sms|text message)\b", low)
            else "notice"
        )
        add_role(
            "school_parent_notice" if broad else "private_parent_notice",
            audience="school_community" if broad else "private_recipient",
            recipient_type="school_community" if broad else "guardian",
            channel=parent_channel,
        )

    # Preserve a safe gratitude deliverable even when the unsafe donor
    # ranking request and the thank-you request are in adjacent sentences.
    if donor_preference_request(low) and re.search(
        r"\b(?:draft|prepare|write|create)?\s*(?:a\s+)?"
        r"(?:thank[- ]?you|appreciation|acknowledgement)\s+"
        r"(?:message|letter|note)\b",
        low,
    ):
        add_role(
            "external_stakeholder_message", audience="private_recipient",
            recipient_type="external_stakeholder", channel="message",
        )

    if re.search(
        r"\b(?:thank[- ]?you|appreciation|acknowledgement)\s+"
        r"(?:message|letter|note)\b[^.!?\n]{0,70}\b(?:donors?|sponsors?|contributors?|volunteers?)\b|"
        r"\b(?:message|letter|note)\b[^.!?\n]{0,70}"
        r"\b(?:thank|appreciat)\w*\b[^.!?\n]{0,50}\b(?:donors?|sponsors?|volunteers?)\b|"
        r"\b(?:donors?|sponsors?|contributors?|volunteers?)\b[^.!?\n]{0,90}"
        r"\b(?:thank[- ]?you|appreciation|acknowledgement)\s+"
        r"(?:message|letter|note)\b|"
        r"(?:捐款人|捐赠者|捐贈者|赞助人|贊助人)[^。！？\n]{0,40}(?:感谢|感謝|致谢|致謝)|"
        r"(?:ucapan|mesej)\s+terima\s+kasih[^.!?\n]{0,50}(?:penderma|penaja)",
        low,
    ):
        add_role(
            "external_stakeholder_message", audience="private_recipient",
            recipient_type="external_stakeholder",
            channel="message",
        )

    if re.search(
        r"\b(?:class(?:room)?|room)?[- ]?relocation\s+checklist\b|"
        r"\bchecklist\b[^.!?;\n]{0,45}\b(?:class(?:room)?|room)\s+relocation\b",
        low,
    ):
        add_custom("Room-relocation checklist", "relocation_plan")
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
        prior_case_context: dict | None = None,
        clarification_answers: dict | None = None,
        selected_deliverable_ids: list[str] | None = None,
        custom_deliverables: list[dict] | None = None,
        declared_intent: dict | None = None,
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
        raw_declared_intent = (
            declared_intent if isinstance(declared_intent, dict) else {}
        )
        allowed_outcome_modes = {
            "recommend_response_pack", "prepare_selected_documents",
        }
        allowed_authority_modes = {
            "draft_only", "prepare_for_approval", "request_external_release",
        }
        allowed_audiences = {
            "internal", "private_recipient", "school_community",
            "external_agency", "public",
        }
        allowed_channels = {
            "whatsapp", "email", "sms", "facebook", "website",
            "phone", "letter", "other",
        }

        def declared_values(key: str, allowed: set[str], limit: int = 8) -> list[str]:
            values = raw_declared_intent.get(key) or []
            if isinstance(values, str):
                values = [values]
            return list(dict.fromkeys(
                str(item).strip().lower()
                for item in list(values)[:limit]
                if str(item).strip().lower() in allowed
            ))

        declared_outcome = str(
            raw_declared_intent.get("outcome_mode") or ""
        ).strip().lower()
        if declared_outcome not in allowed_outcome_modes:
            declared_outcome = ""
        declared_authority = str(
            raw_declared_intent.get("authority_mode") or ""
        ).strip().lower()
        if declared_authority not in allowed_authority_modes:
            declared_authority = ""
        declared_families = declared_values("task_families", self.families)
        declared_audiences = declared_values(
            "intended_audiences", allowed_audiences
        )
        declared_roles = declared_values(
            "selected_artifact_roles", set(self.catalog), limit=12
        )
        declared_channels = declared_values(
            "requested_channels", allowed_channels
        )
        declared_unknown_policy = str(
            raw_declared_intent.get("unknown_policy") or ""
        ).strip().lower()
        if declared_unknown_policy not in {
            "tbc_and_continue", "ask_one_critical_question",
        }:
            declared_unknown_policy = ""
        declared_attachment_refs = [
            re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(item).strip())[:120]
            for item in list(raw_declared_intent.get("attachment_refs") or [])[:12]
            if str(item).strip()
        ]
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
        semantic_authoritative = bool(
            semantics.get("checked") is True and bool(suggested)
        )
        family, severity, signals, stakeholders = _corroborate_semantic_impact(
            text, family, severity, signals, stakeholders,
            semantic_authoritative=semantic_authoritative,
        )

        # Re-apply the closed source-grounded floors after semantic planning.
        # These predicates are additive safety evidence. They must never act as
        # an allow-list for what an open-input semantic planner may understand.
        closed_facets = deterministic_incident_facets(text)
        signals.update(closed_facets["signals"])
        signals.difference_update(closed_facets.get("suppress_signals") or set())
        stakeholders.update(closed_facets["stakeholders"])
        closed_family = str(closed_facets.get("family") or "")
        closed_severity = str(closed_facets.get("severity") or "unknown")
        if closed_family and (
            family == "general_school_admin"
            or _SEVERITY_RANK.get(closed_severity, 0)
            >= _SEVERITY_RANK.get(severity, 0)
        ):
            family = closed_family
        closed_phase = str(closed_facets.get("phase") or "")
        if closed_phase in self.phases:
            phase = closed_phase
        if _SEVERITY_RANK.get(closed_severity, 0) > _SEVERITY_RANK.get(severity, 0):
            severity = closed_severity
        if missing_minor_reported(text):
            signals.update({
                "person_missing", "minor_involved", "safeguarding_concern",
                "external_help_may_be_required", "evacuation_accountability",
            })
            stakeholders.update({"guardian", "school_leadership"})
            family = "safeguarding_welfare"
            severity = "critical"
        if school_transport_incident(text):
            signals.add("transport_operation")
            stakeholders.add("transport_provider")
            if school_transport_collision(text):
                signals.update({
                    "minor_involved", "guardian_notification_relevant",
                })
                stakeholders.add("guardian")
                if family == "general_school_admin":
                    family = "transport_travel"
                if _SEVERITY_RANK.get(severity, 0) < _SEVERITY_RANK["medium"]:
                    severity = "medium"
                if phase == "unknown":
                    phase = "just_occurred"
        if public_attention_reported(text):
            signals.add("public_interest")
            stakeholders.add("public_media")

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
        source_life_unknown = _life_safety_status_unknown(text)
        if source_life_unknown:
            # An expressly unresolved danger question is not confirmation that
            # danger is active. Preserve the high-priority review boundary,
            # but let the one human answer decide whether active coverage runs.
            signals.discard("active_danger")
        if "active_danger" in signals:
            signals.add("external_help_may_be_required")
            stakeholders.add("malaysia_emergency_services_999")
            severity = "critical"
            phase = "ongoing"
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
                )[:12]
                if isinstance(item, dict)
                and str(
                    item.get("artifact_role") or item.get("role") or ""
                ).strip().lower() in self.catalog
            ],
            "explicit_external_actions": self._closed_list(
                suggested.get("explicit_external_actions"), _EXTERNAL_RECIPIENTS
            ),
            "compiler_source": semantics.get("source", "unknown"),
            "declared_attachment_refs": declared_attachment_refs,
            "governance_note": (
                "Situation labels propose coverage only; they never authorise an action."
            ),
        }
        if (
            str(semantics.get("case_relation") or "").lower() == "follow_up"
            and prior_case_context
        ):
            # Prior facts have already passed source grounding in the parent
            # task. Merge them only after the case controller confirms
            # continuity; never re-extract or upgrade their source status.
            situation = merge_followup_situation(
                situation,
                prior_case_context,
                current_text=text,
            )
        # A current-turn human answer is newer evidence than the inherited
        # case snapshot.  ``merge_followup_situation`` deliberately carries
        # forward prior facts and signals, but that must not resurrect a stale
        # active-danger state after the operator has just answered "No".
        # Re-apply only the dynamic life-safety status here; stable historical
        # facts (injury, safeguarding, missing person, etc.) remain available.
        immediate_danger_answer = str(
            answers.get("immediate_danger") or ""
        ).strip().casefold()
        if immediate_danger_answer:
            merged_signals = set(situation.get("signals") or [])
            merged_stakeholders = set(
                situation.get("stakeholder_candidates") or []
            )
            if immediate_danger_answer in {"yes", "active", "ongoing", "true"}:
                merged_signals.update({
                    "active_danger", "external_help_may_be_required",
                })
                merged_stakeholders.add("malaysia_emergency_services_999")
                situation["severity"] = "critical"
                situation["phase"] = "ongoing"
                situation["immediate_danger_status"] = "yes"
            elif immediate_danger_answer in {"no", "contained", "false"}:
                merged_signals.discard("active_danger")
                if not merged_signals.intersection({
                    "person_missing", "safeguarding_concern",
                }):
                    # "No" also answers the unmet-emergency half of the
                    # question.  Do not keep a generic emergency-service need
                    # merely because the parent snapshot once had one.
                    merged_signals.discard("external_help_may_be_required")
                    merged_stakeholders.discard(
                        "malaysia_emergency_services_999"
                    )
                if str(situation.get("phase") or "") == "ongoing":
                    situation["phase"] = "just_occurred"
                situation["immediate_danger_status"] = "no"
            elif immediate_danger_answer in {"unknown", "tbc", "unsure"}:
                merged_signals.add("external_help_may_be_required")
                if str(situation.get("severity") or "") not in {
                    "critical", "high",
                }:
                    situation["severity"] = "high"
                situation["immediate_danger_status"] = "unknown"
            situation["signals"] = sorted(merged_signals)
            situation["stakeholder_candidates"] = sorted(merged_stakeholders)
        if re.search(
            r"\b(?:response|follow[- ]?up)\s+pack(?:age)?\b",
            lower_text,
        ):
            # "Prepare the response pack" delegates package selection; it is
            # not a request for an extra file literally called response pack.
            situation["requested_outputs"] = [
                item for item in situation["requested_outputs"]
                if str(item.get("artifact_role") or "").strip().lower()
                not in {"school_document", "user_titled_document"}
            ]
            situation["requested_deliverables"] = [
                role for role in situation["requested_deliverables"]
                if str(role).strip().lower()
                not in {"school_document", "user_titled_document"}
            ]
        source_outputs, inferred_custom_deliverables = (
            _source_requested_output_contracts(text)
        )
        source_specific_roles = {
            str(item.get("artifact_role") or "").strip().lower()
            for item in source_outputs
            if str(item.get("artifact_role") or "").strip().lower()
            not in {"school_document", "user_titled_document"}
        }
        if (
            "school_parent_notice" in source_specific_roles
            and "private_parent_notice" not in source_specific_roles
        ):
            # A first-party "all parents" instruction owns the audience. Do
            # not keep a semantic model's narrower guardian draft beside it.
            situation["requested_outputs"] = [
                item for item in situation["requested_outputs"]
                if str(item.get("artifact_role") or "").strip().lower()
                != "private_parent_notice"
            ]
            situation["requested_deliverables"] = [
                role for role in situation["requested_deliverables"]
                if str(role).strip().lower() != "private_parent_notice"
            ]
        existing_roles_before_source = {
            str(item.get("artifact_role") or "").strip().lower()
            for item in situation["requested_outputs"]
            if isinstance(item, dict)
        }
        if (
            source_specific_roles
            and existing_roles_before_source
            and existing_roles_before_source.issubset({
                "school_document", "user_titled_document",
            })
            and len(situation["requested_outputs"]) == 1
        ):
            # Replace a semantic provider's generic wrapper with the
            # source-recovered first-class role. Do not preserve both and turn
            # one explicitly requested speech/minutes/roster into two files.
            situation["requested_outputs"] = []
        existing_output_roles = {
            str(item.get("artifact_role") or "").strip().lower()
            for item in situation["requested_outputs"]
            if isinstance(item, dict)
        }
        for output in source_outputs:
            role = str(output.get("artifact_role") or "").strip().lower()
            if role and role in existing_output_roles:
                # Preserve first-party explicitness even when the semantic
                # model already proposed the same canonical role.
                for existing in situation["requested_outputs"]:
                    if str(existing.get("artifact_role") or "").lower() == role:
                        existing["source_named"] = True
                        for key in ("channel", "audience", "recipient_type"):
                            if output.get(key) and not existing.get(key):
                                existing[key] = output[key]
                        break
            elif role:
                situation["requested_outputs"].append(output)
                existing_output_roles.add(role)
        # Structured intent is a first-class source of explicit *work*, but it
        # is never a source of new authority or a broader audience. Internal
        # artifacts may be selected directly. Outward-facing roles are accepted
        # only when the free-text/semantic request already established that
        # audience; otherwise the declaration is recorded as rejected.
        inferred_audience = str(situation.get("requested_audience") or "unknown").lower()
        accepted_declared_roles: list[str] = []
        rejected_declared_roles: list[dict] = []
        for role in declared_roles:
            spec = self.catalog.get(role) or {}
            role_audience = str(spec.get("audience") or "internal").lower()
            audience_safe = (
                role_audience == "internal"
                or role in existing_output_roles
                or role_audience == inferred_audience
            )
            if not audience_safe:
                rejected_declared_roles.append({
                    "artifact_role": role,
                    "reason": "declared_role_cannot_expand_inferred_audience",
                })
                continue
            accepted_declared_roles.append(role)
            if role not in existing_output_roles:
                situation["requested_outputs"].append({
                    "artifact_role": role,
                    "audience": role_audience,
                    "recipient_type": str(
                        spec.get("recipient_type") or "school_staff"
                    ),
                    "source_named": True,
                    "explicit": True,
                    "source": "user_declared_intent",
                })
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

        # Freeze the user's requested work before policy recommendations are
        # added. Every explicit output gets its own obligation, including
        # several unfamiliar documents that share the generic school_document
        # role. The compiler may transform an unsafe use, but it may not merge
        # or silently forget an obligation.
        intent_contract = build_user_intent_contract(
            text,
            situation,
            custom_deliverables=effective_custom_deliverables,
        )
        accepted_declared_audiences = [
            audience for audience in declared_audiences
            if audience == inferred_audience or audience == "internal"
        ]
        rejected_declared_audiences = [
            audience for audience in declared_audiences
            if audience not in accepted_declared_audiences
        ]
        intent_contract.update({
            "schema_version": "school_intent_contract.v2",
            "declaration": {
                "outcome_mode": declared_outcome,
                "authority_mode": declared_authority,
                "task_families": declared_families,
                "intended_audiences": accepted_declared_audiences,
                "selected_artifact_roles": accepted_declared_roles,
                "requested_channels": declared_channels,
                "attachment_refs": declared_attachment_refs,
                "unknown_policy": declared_unknown_policy,
            },
            "provenance": {
                "declared_fields": sorted(
                    key for key, value in {
                        "outcome_mode": declared_outcome,
                        "authority_mode": declared_authority,
                        "task_families": declared_families,
                        "intended_audiences": accepted_declared_audiences,
                        "selected_artifact_roles": accepted_declared_roles,
                        "requested_channels": declared_channels,
                        "attachment_refs": declared_attachment_refs,
                        "unknown_policy": declared_unknown_policy,
                    }.items() if value
                ),
                "agent_inferred": True,
                "human_confirmed": bool(clarification_answers),
                "policy_required": True,
                "safe_substitution": False,
            },
            "rejected_declarations": {
                "audiences": rejected_declared_audiences,
                "artifact_roles": rejected_declared_roles,
            },
        })
        unmatched_obligations = list(intent_contract.get("obligations") or [])

        def claim_obligation(output: dict) -> str:
            role = str(output.get("artifact_role") or "school_document").lower()
            label = str(output.get("label") or "").strip().casefold()
            purpose = str(output.get("purpose") or "").strip().casefold()
            candidates = [
                item for item in unmatched_obligations
                if str(item.get("artifact_role") or "") == role
            ]
            match = next(
                (
                    item for item in candidates
                    if label
                    and str(item.get("label") or "").strip().casefold() == label
                    and (
                        not purpose
                        or str(item.get("purpose") or "").strip().casefold()
                        == purpose
                    )
                ),
                candidates[0] if candidates else None,
            )
            if match is None:
                return ""
            unmatched_obligations.remove(match)
            return str(match.get("obligation_id") or "")

        unique_requested_outputs: list[dict] = []
        seen_roles: set[str] = set()
        existing_obligation_links = {
            str(item.get("source_obligation_id") or "")
            for item in effective_custom_deliverables
            if isinstance(item, dict)
        }
        for output in situation["requested_outputs"]:
            if not isinstance(output, dict):
                continue
            output = deepcopy(output)
            role = str(output.get("artifact_role") or "school_document").lower()
            obligation_id = claim_obligation(output)
            if obligation_id:
                output["source_obligation_id"] = obligation_id
            if role not in seen_roles:
                unique_requested_outputs.append(output)
                seen_roles.add(role)
                continue
            # The response-pack catalog is role keyed. Preserve every later
            # same-role request as a distinct custom node instead of allowing
            # the dict compiler to overwrite it.
            if obligation_id and obligation_id in existing_obligation_links:
                continue
            label = str(output.get("label") or "").strip()
            if not label:
                label = (
                    str(output.get("purpose") or "").strip()[:100]
                    or f"Requested {role.replace('_', ' ')} "
                    f"{len(effective_custom_deliverables) + 1}"
                )
            effective_custom_deliverables.append({
                "label": label,
                "purpose": str(output.get("purpose") or "")[:500],
                "artifact_role": role,
                "audience": str(output.get("audience") or "internal"),
                "recipient_type": str(
                    output.get("recipient_type") or "school_staff"
                ),
                "languages": list(output.get("languages") or []),
                "mode": "draft",
                "explicit": True,
                "source_obligation_id": obligation_id,
            })
            if obligation_id:
                existing_obligation_links.add(obligation_id)
        situation["requested_outputs"] = unique_requested_outputs
        situation["intent_contract"] = intent_contract
        inferred_external = _infer_explicit_external_recipients(
            text,
            requested_action=situation["requested_action"],
            requested_audience=situation["requested_audience"],
            requested_outputs=situation["requested_outputs"],
        )
        negated_external = _negated_external_recipients(text)
        release_specs = explicit_release_channel_specs(text)
        private_guardian_spec = _explicit_private_guardian_release_spec(text)
        if private_guardian_spec is not None:
            inferred_external.add("guardian")
            if not any(
                str(item.get("recipient_type") or "") == "guardian"
                for item in release_specs
            ):
                release_specs.append(private_guardian_spec)
        if _release_is_negated(text) or declared_authority == "draft_only":
            # The user's explicit non-release instruction is stronger than a
            # mistaken semantic suggestion or a live planner send proposal.
            situation["explicit_external_actions"] = []
            situation["explicit_external_release_specs"] = []
        else:
            # A negated route must not be resurrected merely because the
            # channel backstop recognised its noun (for example, an anonymous
            # Facebook *draft* followed by "do not publish it").
            release_specs = [
                item for item in release_specs
                if str(item.get("recipient_type") or "") not in negated_external
            ]
            if declared_channels:
                release_specs = [
                    item for item in release_specs
                    if str(item.get("channel") or "other").lower()
                    in set(declared_channels)
                ]
            # Preserve each source-named channel.  A combined clause such as
            # "send WhatsApp to parents and post Facebook" must not collapse
            # to a single public recipient merely because both verbs share a
            # sentence.
            source_channel_recipients = {
                str(item.get("recipient_type") or "") for item in release_specs
                if str(item.get("recipient_type") or "")
            }
            situation["explicit_external_actions"] = sorted(
                (inferred_external - negated_external)
                | source_channel_recipients
            )
            situation["explicit_external_release_specs"] = release_specs
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
                r"\b(?:parent|guardian|ibu\s+bapa|penjaga)\b|家长|家長|监护人|監護人",
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
                r"(?:year\s+\d+\s+)?class\s+(?:whatsapp\s+)?group|"
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
        response_pack["intent_contract"] = intent_contract
        response_pack["intent_summary"] = {
            "outcome": (
                "Prepare only the selected document(s)"
                if declared_outcome == "prepare_selected_documents"
                else "Recommend a governed response pack"
            ),
            "authority": declared_authority or "inferred from the request",
            "audiences": (
                accepted_declared_audiences
                or [inferred_audience]
            ),
            "user_requested_count": int(
                intent_contract.get("explicit_count") or 0
            ),
            "policy_added_count": sum(
                1 for item in response_pack.get("deliverables") or []
                if item.get("selection_origin") in {
                    "policy_required", "safe_substitution"
                }
            ),
            "confirmation_state": (
                "human_confirmed" if clarification_answers
                else "user_declared_for_review"
                if intent_contract["provenance"]["declared_fields"]
                else "agent_inferred_for_review"
            ),
            "rejected_expansions": (
                len(rejected_declared_audiences)
                + len(rejected_declared_roles)
            ),
        }
        intent_coverage = evaluate_deliverable_coverage(
            intent_contract,
            response_pack.get("deliverables") or [],
        )
        # System recommendations may help when the user delegates the whole
        # response pack.  They must not silently become required work beside a
        # concrete requested artifact (for example, a speech unexpectedly
        # expanding into an event plan and an organiser letter).
        unrequested_ids = set(
            intent_coverage.get("unrequested_deliverable_ids") or []
        )
        if int(intent_contract.get("explicit_count") or 0) > 0:
            for deliverable in response_pack.get("deliverables") or []:
                if (
                    str(deliverable.get("deliverable_id") or "")
                    in unrequested_ids
                    and deliverable.get("selection_origin")
                    == "system_recommendation"
                ):
                    deliverable.update({
                        "selected": False,
                        "requirement": "recommended",
                        "reason": (
                            "optional system recommendation; not requested by "
                            "the user"
                        ),
                    })
        response_pack["intent_coverage"] = evaluate_deliverable_coverage(
            intent_contract,
            [
                item for item in (response_pack.get("deliverables") or [])
                if item.get("selected") is not False
            ],
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
                # A command can contain a grammatical presupposition without
                # supplying evidence for it ("state the exact time the
                # ambulance arrived" / "give the doctor's diagnosis").  A
                # semantic model may rewrite that into a declarative fact.
                # Only an assertion before the command may support a fact.
                # "Please write that an ambulance arrived" is still an
                # instruction, not evidence.
                directive_match = SCHOOL_OPERATOR_COMMAND_PREFIX.search(segment)
                if directive_match:
                    asserted_prefix = segment[:directive_match.start()].strip()
                    colon_index = segment.find(":", directive_match.end())
                    stated_payload = (
                        segment[colon_index + 1:].strip()
                        if colon_index >= 0 else ""
                    )
                    if (
                        fact_norm not in asserted_prefix
                        and fact_norm not in stated_payload
                    ):
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
            if re.search(
                r"\b(?:fire|smoke|flames?|kebakaran|asap)\b",
                _mask_exercise_hazard_terms(low),
            ):
                stakeholders.add("fire_and_rescue")
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

        # These high-impact floors run even after a successful semantic pass.
        # They add coverage only and never create release authority.
        if missing_minor_reported(low):
            signals.update({
                "person_missing", "minor_involved", "safeguarding_concern",
                "external_help_may_be_required", "evacuation_accountability",
            })
            stakeholders.update({"guardian", "school_leadership"})
            family = "safeguarding_welfare"
            severity = "critical"
        if school_transport_incident(low):
            signals.add("transport_operation")
            stakeholders.add("transport_provider")
            if family == "general_school_admin":
                family = "transport_travel"
            if school_transport_collision(low):
                signals.update({
                    "minor_involved", "guardian_notification_relevant",
                })
                stakeholders.add("guardian")
                if _SEVERITY_RANK.get(severity, 0) < _SEVERITY_RANK["medium"]:
                    severity = "medium"
                if phase == "unknown":
                    phase = "just_occurred"
        if public_attention_reported(low):
            signals.add("public_interest")
            stakeholders.add("public_media")
        if institutional_investment_request(low) or institutional_return_prediction(low):
            signals.add("financial_value_involved")
            family = "finance_procurement"

        # Provider-independent incident facets are a closed degradation floor:
        # they add controls observable in the source but never release authority.
        incident_facets = deterministic_incident_facets(low)
        signals.update(incident_facets["signals"])
        stakeholders.update(incident_facets["stakeholders"])
        facet_family = str(incident_facets.get("family") or "")
        facet_severity = str(incident_facets.get("severity") or "unknown")
        if facet_family and (
            family == "general_school_admin"
            or _SEVERITY_RANK.get(facet_severity, 0)
            >= _SEVERITY_RANK.get(severity, 0)
        ):
            family = facet_family
        facet_phase = str(incident_facets.get("phase") or "")
        if facet_phase in self.phases:
            phase = facet_phase
        if _SEVERITY_RANK.get(facet_severity, 0) > _SEVERITY_RANK.get(severity, 0):
            severity = facet_severity

        if not lexical_fallback:
            return family, phase, severity, signals, stakeholders

        # Degradation path only. Live arbitrary inputs normally receive these
        # facets from the semantic LLM; the fallback protects operation during
        # a transient API/JSON failure and never authorises an action.
        if _contains_any(low, (
            "bomba", "fire and rescue", "fire", "smoke", "snake",
            "kebakaran", "asap", "蜂",
        )):
            stakeholders.add("fire_and_rescue")
        if not _contains_any(
            _mask_exercise_hazard_terms(low),
            (
                "bomba", "fire and rescue", "fire", "smoke", "snake",
                "kebakaran", "asap",
            ),
        ):
            stakeholders.discard("fire_and_rescue")
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
            r"\b(?:water\s+(?:pipe\s+)?leak|power\s+outage|building\s+damage|"
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
        if re.search(
            r"\b(?:teacher|teachers|staff|employee|employees)\b"
            r"[^.!?;\n]{0,100}\b(?:argu(?:e|ed|ing|ment)|conflict|"
            r"dispute|altercation|confrontation)\b|"
            r"\b(?:argu(?:e|ed|ing|ment)|conflict|dispute|altercation|"
            r"confrontation)\b[^.!?;\n]{0,100}"
            r"\b(?:teacher|teachers|staff|employee|employees)\b",
            low,
        ):
            family = "staffing_hr"
            stakeholders.add("school_leadership")
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
        external_release_specs = [
            deepcopy(item)
            for item in (situation.get("explicit_external_release_specs") or [])
            if isinstance(item, dict)
        ]
        source_text = str(text or "")
        lower_text = source_text.casefold()
        release_references_missing_payload = bool(
            not (situation.get("declared_attachment_refs") or [])
            and re.search(
                r"\b(?:send|publish|post|submit|release|share)\b"
                r"[^.!?;\n]{0,55}\b(?:this|the|attached)\s+"
                r"(?:timetable|schedule|report|file|document|letter|notice)\b",
                lower_text,
            )
            and not re.search(
                r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|"
                r"sunday)\b|\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b|"
                r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
                lower_text,
            )
        )
        negated_output_roles = _source_negated_output_roles(source_text)
        parent_notice_negated = _parent_notice_explicitly_negated(source_text)
        if parent_notice_negated:
            external_requests.discard("guardian")
            external_release_specs = [
                item for item in external_release_specs
                if str(item.get("recipient_type") or "") != "guardian"
            ]
        official_record_mutation = _official_record_mutation_requested(source_text)
        internal_repository_write = internal_school_repository_write_requested(
            source_text
        )
        structured_outputs = [
            deepcopy(item) for item in (situation.get("requested_outputs") or [])
            if isinstance(item, dict)
            and str(item.get("artifact_role") or "").strip().lower()
            not in negated_output_roles
        ]
        if parent_notice_negated:
            structured_outputs = [
                item for item in structured_outputs
                if str(item.get("artifact_role") or "").strip().lower()
                not in {"private_parent_notice", "school_parent_notice"}
            ]
        explicit_broad_parent_audience = bool(re.search(
            r"\b(?:all|every)\s+parents?\b|\bparent\s+(?:group|community)\b|"
            r"\bschool\s+community\b|\b(?:all|whole)\s+school\b|"
            r"\b(?:bilingual\s+)?school\s+notice\b|"
            r"\b(?:year\s+\d+\s+)?class\s+(?:whatsapp\s+)?group\b|"
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
                if (
                    str(item.get("artifact_role") or "").lower()
                    in {"school_parent_notice", "private_parent_notice"}
                    and (
                        str(item.get("artifact_role") or "").lower()
                        == "school_parent_notice"
                        or str(item.get("audience") or "").lower()
                        in {"school_community", "public"}
                    )
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
        source_named_output_roles = {
            str(item.get("artifact_role") or "").strip().lower()
            for item in structured_outputs
            if item.get("source_named") is True
        }
        source_named_output_audiences = {
            str(item.get("audience") or "").strip().lower()
            for item in structured_outputs
            if item.get("source_named") is True
        }
        entries: dict[str, dict] = {}
        obligation_by_id = {
            str(item.get("obligation_id") or ""): item
            for item in (
                (situation.get("intent_contract") or {}).get("obligations")
                or []
            )
            if isinstance(item, dict) and item.get("obligation_id")
        }
        origin_rank = {
            "system_recommendation": 0,
            "semantic_candidate": 0,
            "policy_required": 1,
            "explicit_request": 2,
            "safe_substitution": 3,
        }

        def add(role: str, requirement: str, reason: str, **contract: Any) -> None:
            if role not in self.catalog:
                return
            if role in negated_output_roles:
                return
            if parent_notice_negated and role in {
                "private_parent_notice", "school_parent_notice",
            }:
                return
            contract.setdefault("selection_origin", "policy_required")
            current = entries.get(role)
            if current is None:
                entries[role] = {
                    "role": role, "requirement": requirement,
                    "reason": reason, "contract": deepcopy(contract),
                }
                return
            if requirement == "required" and current["requirement"] != "required":
                current.update({"requirement": requirement, "reason": reason})
            current_origin = str(
                current["contract"].get("selection_origin") or ""
            )
            incoming_origin = str(contract.get("selection_origin") or "")
            if origin_rank.get(current_origin, -1) > origin_rank.get(
                incoming_origin, -1
            ):
                contract["selection_origin"] = current_origin
            current["contract"].update(deepcopy(contract))

        broad_internal_staff_audience = bool(re.search(
            r"\b(?:all|every)[-\s]+(?:school[-\s]+)?teachers?\b|"
            r"\bevery[-\s]+instructor\b|"
            r"\b(?:all|whole|entire)[-\s]+(?:school[-\s]+|teaching[-\s]+)?staff\b|"
            r"\b(?:all|whole|entire)[-\s]+(?:teaching[-\s]+)?(?:team|faculty)\b|"
            r"\b(?:faculty|staff|teacher)[-\s]+wide\b|"
            r"\bwhole\s+staff\b",
            lower_text,
        )) or bool(
            "staff_internal_notice" in source_named_output_roles
            and (
                explicit_broad_parent_audience
                or bool(source_named_output_audiences.intersection({
                    "school_community", "public",
                }))
            )
        )
        broad_audience = bool(
            explicit_broad_parent_audience
            or broad_internal_staff_audience
            or external_requests.intersection({
                "school_community", "public_media",
            })
            or any(
                str(item.get("recipient_type") or "") in {
                    "school_community", "public_media",
                }
                for item in external_release_specs
            )
            or source_named_output_audiences.intersection({
                "school_community", "public",
            })
            or source_named_output_roles.intersection({
                "school_parent_notice", "public_communication_draft",
            })
            or re.search(
                r"\b(?:(?:public|school|community)\s+)?newsletter\b|"
                r"\b(?:public|school)\s+(?:bulletin|website|facebook\s+page)\b",
                lower_text,
            )
        )
        unsupported_evidence_contradiction = (
            _unsupported_claim_without_evidence(lower_text)
        )
        semantic_outcome_claim = bool(
            "unsupported_fact_invention" in concepts
            and re.search(
                r"(?:improv(?:e|ed|ement)|increase(?:d)?|reduc(?:e|ed|tion)|"
                r"decreas(?:e|ed)|achiev(?:e|ed|ement)|success(?:ful|fully)?|"
                r"effective(?:ness)?|outcome|impact|attendance|baseline|"
                r"follow[- ]up|measur(?:e|ed|ement)|collect(?:ed|ion)|"
                r"record(?:ed|ing))|%",
                lower_text,
            )
        )
        if "unsupported_fact_invention" in concepts and re.search(
            r"\b(?:improv(?:e|ed|ement)|increase(?:d)?|reduc(?:e|ed|tion)|"
            r"decreas(?:e|ed)|achiev(?:e|ed|ement)|success(?:ful|fully)?|"
            r"effective(?:ness)?|outcome|impact|attendance|baseline|"
            r"follow[- ]up|measur(?:e|ed|ement)|collect(?:ed|ion)|"
            r"record(?:ed|ing))\b|%",
            lower_text,
        ):
            # Compatibility floor for older source files that carried a
            # malformed word-boundary character in the preceding regex.
            semantic_outcome_claim = True
        unsupported_metric_claim = bool(
            unsupported_evidence_contradiction or semantic_outcome_claim
        )
        fact_invention_request = bool(
            fact_invention_or_no_tbc_request(lower_text)
            or (
                "unsupported_fact_invention" in concepts
                and not unsupported_metric_claim
            )
        )
        unsupported_claim = bool(
            unsupported_metric_claim or fact_invention_request
        )
        investment_advice = institutional_investment_request(lower_text)
        investment_return = institutional_return_prediction(lower_text)
        institutional_finance_request = investment_advice or investment_return
        donor_preference = donor_preference_request(lower_text)
        approval_bypass = bool(
            approval_bypass_attempt(lower_text)
            or re.search(
                r"\b(?:principal|headteacher|head\s+teacher|boss)\b"
                r"[\s\S]{0,180}\b(?:verbally|orally|already)\s+approved\b"
                r"[\s\S]{0,180}\b(?:upload|send|publish|post|submit|release)\b",
                source_text,
                re.IGNORECASE,
            )
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
        status_bias = bool(
            _socioeconomic_differential_harm(lower_text, concepts)
            or _status_based_content_manipulation(source_text)
        )
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
                r"list(?:ed|ing)?|include(?:d|s|ing)?|show(?:n|ing)?|each\s+(?:student|pupil|learner))\b|"
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
        # Pseudonymous initials and medication/result literals are still
        # person-level sensitive data.  They deliberately sit outside the
        # general name extractor, so keep this closed backstop at the broad
        # disclosure boundary rather than treating initials as ordinary names.
        pseudonymous_sensitive_broadcast = bool(
            broad_audience
            and re.search(
                r"(?<!\w)(?:[A-Za-z]\.){2,5}(?!\w)", source_text,
            )
            and re.search(
                r"\b\d{1,3}\s*/\s*\d{1,3}\b|"
                r"\b(?:ritalin|methylphenidate|adhd|diagnos(?:is|ed)|"
                r"failed?|marks?|scores?|medication)\b",
                source_text,
                re.IGNORECASE,
            )
        )
        raw_individual_sensitive_broadcast = bool(
            broad_audience
            and (
                source_has_individual_sensitive_detail(source_text)
                or pseudonymous_sensitive_broadcast
            )
        )
        # A staff memo about the administrative act of submitting marks is
        # not itself a disclosure of any pupil's marks. Some providers label
        # the topic student_sensitive_data merely because "markah" appears.
        # Source-level person evidence still wins, so named pupils, actual
        # scores, failures or other individual details remain governed RED.
        individual_marks_disclosure_cue = bool(re.search(
            r"\b(?:student|pupil|learner|child|murid|pelajar|individual|each|"
            r"name|names|named|nama|gagal|failed?|weak|lemah)\b|"
            r"\b[A-Z][A-Za-z'’-]{1,40}['’]s\s+(?:marks?|scores?|results?)\b|"
            r"\b(?:marks?|scores?|results?|markah)\b[^.!?;\n]{0,8}"
            r"\b\d+(?:\.\d+)?(?:\s*/\s*\d+)?\b",
            source_text,
            re.IGNORECASE,
        ))
        administrative_marks_submission_notice = bool(
            re.search(
                r"\b(?:submit|submission|hand\s+in|enter|key\s+in|record|"
                r"penghantaran|hantar|serah(?:kan)?|masuk(?:kan)?)\b"
                r"[^.!?;\n]{0,70}\b(?:marks?|scores?|results?|markah)\b|"
                r"\b(?:marks?|scores?|results?|markah)\b"
                r"[^.!?;\n]{0,70}\b(?:submit|submission|hand\s+in|enter|"
                r"key\s+in|record|penghantaran|hantar|serah(?:kan)?|"
                r"masuk(?:kan)?)\b",
                lower_text,
            )
            and re.search(
                r"\b(?:memo|notice|notis|reminder|peringatan|deadline|"
                r"due|before|sebelum|tarikh\s+akhir)\b",
                lower_text,
            )
            and not individual_marks_disclosure_cue
        )
        sensitive_broadcast = bool(
            broad_audience and (
                (
                    raw_individual_sensitive_broadcast
                    and not administrative_marks_submission_notice
                )
                or (
                    explicit_individual
                    and concepts.intersection({
                        "public_pii", "health_or_discipline",
                        "student_sensitive_data",
                    })
                )
                or (
                    broad_internal_staff_audience
                    and "student_sensitive_data" in concepts
                    and not administrative_marks_submission_notice
                )
            )
        )
        persistent_sensitive = "persistent_sensitive_learning" in concepts
        input_decision = "NO_OVERRIDE"
        input_reasons: list[str] = []
        transformations: list[str] = []
        unsupported_transform = (
            "Replace the unsupported outcome claim with an evidence-status "
            "report and a measurement plan."
        )
        fact_invention_transform = (
            "Keep the requested task type, use only reported or verified facts, "
            "and mark every missing or unverified field TBC."
        )
        investment_transform = (
            "Do not select securities or predict returns for school funds. "
            "Provide a finance-governance memo requiring the current mandate, "
            "risk limits, authorised approval and qualified professional advice."
        )
        donor_transform = (
            "Preserve the requested gratitude and event communications, but remove "
            "donation-based ranking, priority and preferential seating; use equal "
            "recognition and neutral allocation criteria."
        )
        approval_transform = (
            "Treat asserted or verbal approval as unverified. Prepare the draft, "
            "but retain the independent human gate before any external release."
        )
        status_transform = (
            "Use observed conduct, verified evidence, applicable school rules and proportionate support only."
        )
        privacy_transform = (
            "Use an anonymous class-level notice and separate one-to-one guardian communication where authorised."
        )
        staff_privacy_transform = (
            "Replace the broad staff disclosure with a restricted, need-to-know "
            "student support plan using only observed conduct, verified evidence "
            "and proportionate support."
        )
        memory_transform = (
            "Keep case data task-local and retain only a reviewed non-personal procedure."
        )
        if unsupported_metric_claim:
            input_decision = "INFEASIBLE"
            input_reasons.append(
                "The requested outcome claim is unsupported by collected evidence and cannot be presented as fact."
            )
            transformations.append(unsupported_transform)
        if fact_invention_request:
            input_decision = "INFEASIBLE"
            input_reasons.append(
                "Missing or unverified case facts cannot be converted into confirmed facts or hidden by removing TBC."
            )
            transformations.append(fact_invention_transform)
        if institutional_finance_request:
            input_decision = "INFEASIBLE"
            input_reasons.append(
                "The agent cannot select investments or promise returns for school funds without a verified mandate and qualified authority."
            )
            transformations.append(investment_transform)
        if donor_preference:
            if input_decision != "INFEASIBLE":
                input_decision = "RED"
            input_reasons.append(
                "Donation amount cannot be used to rank people or grant preferential access or seating."
            )
            transformations.append(donor_transform)
        if approval_bypass:
            if input_decision != "INFEASIBLE":
                input_decision = "RED"
            input_reasons.append(
                "An asserted, verbal or urgent instruction cannot bypass the independent approval gate."
            )
            transformations.append(approval_transform)
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
            transformations.append(
                staff_privacy_transform
                if broad_internal_staff_audience else privacy_transform
            )
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
                {
                    "artifact_role": role,
                    # The legacy closed-list field is already the semantic
                    # provider's explicit pack choice. Keep it as an operator-
                    # visible/default-selected recommendation, while reserving
                    # ``required`` for source-named or policy-backed work.
                    "semantic_pack_choice": True,
                }
                for role in (situation.get("requested_deliverables") or [])
                if role in self.catalog
            )
        # When source text explicitly names one or more broad communications,
        # do not let a semantic model inject unrelated internal files and then
        # have privacy transformation disguise them as extra notices. Source-
        # named internal artifacts (for example an incident report beside a
        # Facebook draft) remain intact.
        source_named_outputs = [
            item for item in requested_outputs if item.get("source_named") is True
        ]
        if sensitive_broadcast and source_named_outputs:
            requested_outputs = source_named_outputs
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
        has_explicit_outputs = bool(
            int(
                ((situation.get("intent_contract") or {}).get("explicit_count"))
                or 0
            )
            or selected_deliverable_ids
            or any(
                item.get("source_named") is True
                or (
                    str(situation.get("compiler_source") or "").startswith(
                        "fallback"
                    )
                    and item.get("semantic_pack_choice") is not True
                )
                for item in requested_outputs
            )
        )
        if not requested_outputs and not custom_deliverables:
            if institutional_finance_request:
                requested_outputs = [{"artifact_role": "finance_procurement_memo"}]
            elif unsupported_metric_claim:
                requested_outputs = [{"artifact_role": "evidence_status_report"}]
            elif "public_media" in external_requests:
                requested_outputs = [{
                    "artifact_role": "public_communication_draft",
                    "audience": "public",
                    "recipient_type": "public_media",
                }]
            elif "education_authority" in external_requests or (
                "education_authority" in stakeholders
                and requested_action in {"send", "submit", "message", "contact"}
            ):
                requested_outputs = [{"artifact_role": "education_authority_request"}]
            elif explicit_broad_parent_audience or (
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
                requested_outputs = [{"artifact_role": "user_titled_document"}]

        for output in requested_outputs:
            original_role = str(output.get("artifact_role") or "school_document").lower()
            if unsupported_metric_claim and not institutional_finance_request:
                add(
                    "evidence_status_report", "required",
                    "truthful replacement for an unsupported outcome claim",
                    selection_origin="safe_substitution",
                    claim_policy="evidence_gap_explicit_no_unsupported_metrics",
                    source_fact_ids=fact_ids,
                    source_obligation_id=str(
                        output.get("source_obligation_id") or ""
                    )[:180],
                    safe_transformation=unsupported_transform,
                    action_data_use_concepts=[],
                )
                add(
                    "measurement_plan", "required",
                    "create a path to valid evidence before any future claim",
                    selection_origin="safe_substitution",
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
            if output.get("semantic_pack_choice") is True:
                contract["default_selected"] = True
            if output.get("source_obligation_id"):
                contract["source_obligation_id"] = str(
                    output.get("source_obligation_id")
                )[:180]
            obligation = obligation_by_id.get(
                str(output.get("source_obligation_id") or "")
            )
            selection_origin = (
                "explicit_request"
                if output.get("source_named") is True
                or str(situation.get("compiler_source") or "").startswith(
                    "fallback"
                )
                or (obligation and obligation.get("explicit") is True)
                else "system_recommendation"
            )
            contract["selection_origin"] = selection_origin
            if (
                role in {
                    "education_authority_report", "education_authority_request",
                }
                and re.search(
                    r"\banonymous\s+report\b|\breport\b[^.!?;\n]{0,40}"
                    r"\bwithout\s+(?:a\s+)?name\b|"
                    r"\blaporan\s+tanpa\s+nama\b",
                    source_text,
                    re.IGNORECASE,
                )
            ):
                contract.update({
                    "safe_transformation": (
                        "Use an anonymous, non-identifying authority report; "
                        "include only reported facts and mark unknowns TBC."
                    ),
                    "excluded_data_concepts": [
                        "person_identifier", "public_pii",
                        "student_sensitive_data",
                    ],
                    "claim_policy": "anonymous_reported_facts_only",
                    "action_data_use_concepts": [],
                    "selection_origin": (
                        "explicit_request"
                        if selection_origin == "explicit_request"
                        else "safe_substitution"
                    ),
                })
            if output.get("channel"):
                contract["channel"] = str(output.get("channel"))[:40].lower()
            if output.get("label"):
                contract["requested_label"] = str(output.get("label"))[:120]
            if output.get("audience"):
                contract["requested_audience"] = str(output.get("audience"))[:40]
            if output.get("recipient_type"):
                contract["requested_recipient_type"] = str(output.get("recipient_type"))[:80]
            operational_notice_text = " ".join((
                str(output.get("label") or ""),
                str(output.get("purpose") or ""),
                source_text,
            ))
            if role == "school_document" and re.search(
                r"\b(?:timetable|schedule|room|class(?:room)?)\b[^.!?;\n]{0,80}"
                r"\b(?:change|relocation|temporary|notice)\b|"
                r"\b(?:temporary|change|relocation)\b[^.!?;\n]{0,80}"
                r"\b(?:timetable|schedule|room|class(?:room)?|notice)\b",
                operational_notice_text,
                re.IGNORECASE,
            ):
                contract["custom_template_key"] = "operational_notice"
            if (
                (
                    explicit_broad_parent_audience
                    or (
                        output.get("source_named") is True
                        and str(output.get("audience") or "").lower()
                        == "school_community"
                    )
                )
                and role == "private_parent_notice"
            ):
                # A message for the whole parent community is not a private
                # one-family letter.  Keep the audience boundary visible in
                # both the artifact role and its deterministic policy.
                role = "school_parent_notice"
            broad_output_transform = bool(
                original_role in {
                    "school_parent_notice", "public_communication_draft",
                }
                or str(output.get("audience") or "").lower() in {
                    "school_community", "public",
                }
            )
            unsafe_staff_broadcast = bool(
                sensitive_broadcast
                and broad_audience
                and broad_internal_staff_audience
                and original_role in {
                    "school_document", "staff_internal_notice",
                    "school_parent_notice", "public_communication_draft",
                }
            )
            if unsafe_staff_broadcast:
                # A privacy-unsafe broad internal document is not meaningfully
                # fixed by changing it into an unrelated parent notice.
                # Preserve the user's legitimate school purpose, narrow access
                # to the authorised case team, and strip discriminatory data.
                role = (
                    "discipline_investigation_report"
                    if family == "discipline_behaviour"
                    else "safeguarding_action_plan"
                    if family == "safeguarding_welfare"
                    else "student_support_plan"
                )
                contract.update({
                    "purpose": (
                        "Prepare a restricted, need-to-know student support "
                        "coordination plan without broadcasting protected details."
                    ),
                    "requested_audience": "authorised_support_team",
                    "requested_recipient_type": "authorised_support_team",
                    "restricted_internal_audience": True,
                    "audience_boundary": "authorised_support_team",
                    "safe_transformation": (
                        "Replace the broad staff disclosure with a restricted, "
                        "need-to-know case document that preserves the legitimate "
                        "investigation or support goal. Use observed conduct, "
                        "verified evidence and proportionate measures only; omit "
                        "counselling notes, family finances and other unnecessary "
                        "protected details."
                    ),
                    "excluded_data_concepts": [
                        "public_pii", "counselling_notes", "socioeconomic_data",
                        "student_sensitive_data", "differential_monitoring",
                    ],
                    "claim_policy": "restricted_verified_support_only",
                    "action_data_use_concepts": [],
                    "selection_origin": (
                        "explicit_request"
                        if selection_origin == "explicit_request"
                        else "safe_substitution"
                    ),
                })
            if (
                sensitive_broadcast and broad_output_transform
                and not unsafe_staff_broadcast
            ):
                # Keep the channel the user asked for. A privacy-unsafe
                # Facebook request becomes an anonymised Facebook/public draft,
                # not an unrelated parent notice.
                role = (
                    "public_communication_draft"
                    if original_role == "public_communication_draft"
                    or str(output.get("audience") or "").lower() == "public"
                    else "school_parent_notice"
                )
                channel_privacy_transform = (
                    "Keep this as a privacy-safe public/Facebook draft; remove "
                    "student identifiers and person-level sensitive details."
                    if role == "public_communication_draft"
                    else privacy_transform
                )
                contract.update({
                    "safe_transformation": channel_privacy_transform,
                    "excluded_data_concepts": [
                        "public_pii", "health_or_discipline",
                        "student_sensitive_data", "individual_marks",
                        "individual_weakness_reasons",
                    ],
                    "claim_policy": "anonymous_aggregate_or_general_support_only",
                    "action_data_use_concepts": [],
                    "selection_origin": (
                        "explicit_request"
                        if selection_origin == "explicit_request"
                        else "safe_substitution"
                    ),
                })
            if fact_invention_request:
                contract.update({
                    "safe_transformation": " ".join(filter(None, [
                        str(contract.get("safe_transformation") or "").strip(),
                        fact_invention_transform,
                    ])),
                    "claim_policy": "reported_facts_only_tbc_for_unknowns",
                })
            if institutional_finance_request and original_role in {
                "school_document", "finance_procurement_memo",
            }:
                role = "finance_procurement_memo"
                contract.update({
                    "safe_transformation": investment_transform,
                    "claim_policy": "governance_options_only_no_investment_recommendation",
                    "excluded_data_concepts": [
                        "specific_security_selection", "financial_return_prediction",
                    ],
                    "action_data_use_concepts": [],
                })
            if donor_preference:
                exclusions = set(contract.get("excluded_data_concepts") or [])
                exclusions.update({
                    "donation_ranking", "donation_based_preference",
                })
                contract.update({
                    "safe_transformation": " ".join(filter(None, [
                        str(contract.get("safe_transformation") or "").strip(),
                        donor_transform,
                    ])),
                    "excluded_data_concepts": sorted(exclusions),
                    "claim_policy": "equal_recognition_no_donation_based_preference",
                    "action_data_use_concepts": [],
                })
            if approval_bypass and role in {
                "school_parent_notice", "private_parent_notice",
                "public_communication_draft", "external_stakeholder_message",
                "education_authority_request", "education_authority_report",
            }:
                contract["safe_transformation"] = " ".join(filter(None, [
                    str(contract.get("safe_transformation") or "").strip(),
                    approval_transform,
                ]))
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
            if not (sensitive_broadcast and broad_output_transform) and not (
                status_bias or safe_status_constraint or donor_preference
            ):
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
            add(
                role,
                (
                    "required"
                    if str(contract.get("selection_origin") or selection_origin)
                    in {"explicit_request", "safe_substitution"}
                    else "recommended"
                ),
                (
                    "explicitly requested by the user"
                    if selection_origin == "explicit_request"
                    else "semantic system recommendation"
                ),
                **contract,
            )

        # Predicate-backed necessities only. Mere personal data is not a data
        # breach; a routine official message is not a regulatory incident.
        resolved_immediate_danger = bool(
            str(situation.get("immediate_danger_status") or "").casefold()
            == "no"
            or _explicitly_resolved_or_contained_safety(source_text)
        )
        if (
            "active_danger" in signals
            and str(situation.get("immediate_danger_status") or "").casefold()
            != "no"
            and not _explicitly_no_unmet_emergency(source_text)
            and not _explicitly_contained_structural_hazard(source_text)
        ):
            resolved_immediate_danger = False
        if "injury_or_illness" in signals:
            add("internal_incident_report", "required", "record the reported incident")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian update")
            if severity == "critical":
                add(
                    "medical_handover_script", "required",
                    "support an accurate handover if a human contacts medical services",
                )
            elif not has_explicit_outputs:
                add(
                    "medical_handover_script",
                    (
                        "required"
                        if severity == "high" and not resolved_immediate_danger
                        else "recommended"
                    ),
                    "support an accurate handover if a human contacts medical services",
                    selection_origin="system_recommendation",
                    # A recommended medical handover is a visible option, not
                    # an assumed action. High/critical unresolved emergencies
                    # are already promoted to required by the branches above.
                    default_selected=False,
                )
        staff_conflict_source = bool(re.search(
            r"\b(?:teacher|teachers|staff|employee|employees)\b"
            r"[^.!?;\n]{0,100}\b(?:argu(?:e|ed|ing|ment)|conflict|"
            r"dispute|fight|fighting|altercation|confrontation)\b|"
            r"\b(?:argu(?:e|ed|ing|ment)|conflict|dispute|fight|fighting|"
            r"altercation|confrontation)\b[^.!?;\n]{0,100}"
            r"\b(?:teacher|teachers|staff|employee|employees)\b",
            lower_text,
        ))
        staff_only_conflict = bool(
            family in {"staffing_hr", "discipline_behaviour"}
            and staff_conflict_source
            and (
                (people and people.issubset({"staff"}))
                or (
                    not people
                    and not re.search(
                        r"\b(?:student|pupil|child|murid|pelajar)\b|"
                        r"(?:学生|學生|孩子)",
                        lower_text,
                    )
                )
            )
        )
        if staff_only_conflict:
            if (
                "discipline_investigation_report" in entries
                and "discipline_investigation_report"
                not in source_named_output_roles
            ):
                # Model-inferred discipline paperwork duplicates the governed
                # internal incident record for an ordinary staff-only conflict.
                # Preserve it only when the operator explicitly named it.
                entries.pop("discipline_investigation_report", None)
            add(
                "internal_incident_report", "required",
                "record the reported staff conflict for authorised leadership review",
                claim_policy="reported_facts_and_proposed_handling_only",
                action_data_use_concepts=[],
            )
        if "active_danger" in signals:
            add("internal_incident_report", "required", "record the reported safety incident and chronology")
            add("site_safety_checklist", "required", "support immediate human safety response")
            add("emergency_contact_script", "required", "prepare verified facts for emergency services")
        elif severity in {"critical", "high"} and signals.intersection({
            "person_missing", "safeguarding_concern",
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
            add("internal_incident_report", "required", "record the missing-person incident and chronology")
            add("student_accountability_checklist", "required", "account for students and last-known facts")
            add("site_safety_checklist", "required", "support the immediate controlled search boundary")
            add("emergency_contact_script", "required", "prepare verified facts if a human escalates urgently")
            if "student" in people or "minor_involved" in signals:
                add("private_parent_notice", "required", "prepare a private guardian notification")
        if "safeguarding_concern" in signals and (
            (family == "safeguarding_welfare" and not sensitive_broadcast)
            or "person_missing" in signals
            or _actual_safeguarding_concern(source_text)
        ):
            add("internal_incident_report", "required", "record the reported safeguarding concern without assigning blame")
            add("safeguarding_action_plan", "required", "protect the affected pupil while facts are assessed")
            add("evidence_preservation_log", "recommended", "preserve evidence without assigning blame")
        if "evidence_preservation_needed" in signals:
            add(
                "evidence_preservation_log",
                (
                    "required"
                    if signals.intersection({
                        "data_security_incident", "person_missing",
                        "safeguarding_concern",
                    })
                    else "recommended"
                ),
                "preserve source-grounded evidence and decision chronology without assigning blame",
                selection_origin=(
                    "policy_required"
                    if signals.intersection({
                        "data_security_incident", "person_missing",
                        "safeguarding_concern",
                    })
                    else "system_recommendation"
                ),
            )
        facility_control_required = bool(
            family == "facilities_environment"
            and (
                "active_danger" in signals
                # A semantic model may conservatively label any planned
                # service interruption "medium".  That alone is not evidence
                # of a facility hazard and must not turn a one-document notice
                # request into an extra mandatory checklist.  High/critical
                # severity or explicit hazard language still promotes it.
                or severity in {"critical", "high"}
                or re.search(
                    r"\b(?:unsafe|danger(?:ous)?|hazard|damag(?:e|ed)|"
                    r"collapse|fall|fell|fallen|falling|crack(?:ed)?|"
                    r"flood(?:ed|ing)?|leak(?:ed|ing)?|"
                    r"burst|broken|exposed\s+wir|electric(?:al)?\s+fault|"
                    r"gas\s+leak|smoke|fire|contaminat(?:ed|ion)|"
                    r"sewage|pest\s+infestation)\b|"
                    r"\b(?:bahaya|tidak\s+selamat|rosak|retak|banjir|"
                    r"bocor|paip\s+pecah|wayar\s+terdedah|kebakaran|"
                    r"tercemar|kumbahan)\b",
                    lower_text,
                )
            )
        )
        if facility_control_required:
            if (
                str(situation.get("phase") or "") != "planned"
                and not has_explicit_outputs
            ):
                add(
                    "internal_incident_report", "required",
                    "record the reported facility incident and controls",
                    claim_policy="reported_condition_and_proposed_controls_only",
                    action_data_use_concepts=[],
                )
            if "staff" in people or "school_staff" in stakeholders:
                add(
                    "staff_internal_notice", "recommended",
                    "prepare an internal operational notice for affected staff",
                    selection_origin="system_recommendation",
                    claim_policy="reported_condition_and_proposed_controls_only",
                    action_data_use_concepts=[],
                )
            add(
                "site_safety_checklist", "required",
                "inspect and control access to the affected school facility",
                claim_policy="reported_condition_and_proposed_controls_only",
                action_data_use_concepts=[],
            )
        elif family == "facilities_environment" and "service_disruption" in signals:
            # A routine, low-severity planned-maintenance notice does not need
            # a second mandatory file. Keep the checklist available as an
            # optional recommendation; actual damage or danger above still
            # promotes it to a required safety control.
            add(
                "site_safety_checklist", "recommended",
                "optional readiness check for the planned facility closure",
                claim_policy="reported_condition_and_proposed_controls_only",
                action_data_use_concepts=[],
            )
            if "staff" in people or "school_staff" in stakeholders:
                add(
                    "staff_internal_notice", "recommended",
                    "prepare an internal operational notice for affected staff",
                    selection_origin="system_recommendation",
                    claim_policy="reported_condition_and_proposed_controls_only",
                    action_data_use_concepts=[],
                )
        transport_collision = school_transport_collision(source_text)
        transport_plan_explicit = bool(re.search(
            r"\b(?:transport|bus|controlled\s+response|response)\s+plan\b|"
            r"\bpelan\s+(?:respons|pengangkutan|bas)\b|"
            r"(?:交通|巴士|校车|校車)(?:应对|應對|响应|響應)计划",
            lower_text,
        ))
        if "transport_operation" in signals:
            if transport_collision:
                add(
                    "internal_incident_report", "required",
                    "record the reported school-transport collision",
                )
                if "minor_involved" in signals or "student" in people:
                    add(
                        "private_parent_notice", "required",
                        "prepare a private guardian update for affected pupils",
                    )
            add(
                "transport_response_plan",
                "required" if transport_collision or not has_explicit_outputs
                else "recommended",
                "coordinate the transport impact",
                selection_origin=(
                    "policy_required"
                    if transport_collision or not has_explicit_outputs
                    else "system_recommendation"
                ),
            )
            if (
                not transport_collision
                and not has_explicit_outputs
                and not transport_plan_explicit
                and ("student" in people or "minor_involved" in signals)
                and (
                    "guardian_notification_relevant" in signals
                    or "guardian" in stakeholders
                )
                and not parent_notice_negated
            ):
                add(
                    "private_parent_notice", "recommended",
                    "prepare a draft delay and supervision update for affected families",
                    selection_origin="system_recommendation",
                    default_selected=True,
                    claim_policy="reported_facts_and_proposed_coordination_only",
                    action_data_use_concepts=[],
                )
            if "transport_provider" in stakeholders:
                add(
                    "external_stakeholder_message", "recommended",
                    "prepare a draft coordination message for the transport provider",
                    selection_origin="system_recommendation",
                    default_selected=False,
                    claim_policy="reported_facts_and_proposed_coordination_only",
                    action_data_use_concepts=[],
                )
        if "food_water_exposure" in signals:
            add("food_safety_response", "required", "contain and document the exposure")
            if (
                "injury_or_illness" in signals
                and ("student" in people or "minor_involved" in signals)
            ):
                add(
                    "private_parent_notice", "required",
                    "prepare a minimum-necessary private guardian health notice",
                )
        urgent_guardian_notice = bool(
            signals.intersection({"injury_or_illness", "person_missing"})
            or transport_collision
            or (
                "safeguarding_concern" in signals
                and (
                    family == "safeguarding_welfare"
                    or _actual_safeguarding_concern(source_text)
                )
            )
        )
        if "guardian_notification_relevant" in signals and (
            "student" in people or "minor_involved" in signals
        ) and (
            urgent_guardian_notice
            or ("transport_operation" in signals and not has_explicit_outputs)
        ) and not {"private_parent_notice", "school_parent_notice"}.intersection(entries):
            add(
                "private_parent_notice", "required",
                "prepare a minimum-necessary private guardian update",
            )
        if "event_operation" in signals and not has_explicit_outputs:
            add(
                "event_action_plan", "required",
                "coordinate the requested event operation",
                selection_origin="system_recommendation",
            )
            add(
                "external_stakeholder_message", "required",
                "prepare the affected organiser or guest message",
                selection_origin="system_recommendation",
            )
        students_explicitly_absent = _has_affirmed_source_statement(
            source_text,
            re.compile(
                r"\b(?:no|without\s+any)\s+(?:students?|pupils?|children)\s+"
                r"(?:are\s+|were\s+|was\s+)?(?:present|involved|affected)\b|"
                r"(?:没有|沒有)(?:学生|學生|孩子)(?:在场|在場|参与|參與)|"
                r"\btiada\s+(?:murid|pelajar)\s+(?:hadir|terlibat)\b",
                re.IGNORECASE,
            ),
        )
        if (
            signals.intersection({"evacuation_accountability", "active_danger"})
            and not students_explicitly_absent
            and (
                "student" in people
                or "minor_involved" in signals
                or not people
                or "unknown" in people
            )
        ):
            add("student_accountability_checklist", "required", "support controlled accountability")
        if "data_security_incident" in signals:
            add("cyber_incident_response", "required", "contain the actual cyber or data incident")
            add("evidence_preservation_log", "required", "preserve technical and decision evidence")
            add("regulatory_notification_assessment", "required", "assess current notification duties")
        if "public_interest" in signals and not unsupported_metric_claim:
            add(
                "public_communication_draft",
                "recommended" if has_explicit_outputs else "required",
                "prepare a privacy-safe holding draft for existing media or public attention",
                selection_origin=(
                    "system_recommendation"
                    if has_explicit_outputs else "policy_required"
                ),
                safe_transformation=(
                    "Use only verified, non-identifying operational facts; do not "
                    "name affected students or claim an external release."
                ),
                claim_policy="anonymous_verified_holding_text_only",
                action_data_use_concepts=[],
            )
        # Mentioning a fund while recording PTA minutes does not create a
        # second finance workflow. The requested minutes can record the topic;
        # a separate finance memo is reserved for an actual finance decision,
        # reconciliation problem, procurement action or investment request.
        finance_is_record_context = bool(
            "meeting_minutes" in output_roles
            and requested_action in {"draft", "write", "prepare", "report"}
            and not institutional_finance_request
        )
        if "financial_value_involved" in signals and not finance_is_record_context:
            if institutional_finance_request:
                add(
                    "finance_procurement_memo", "required",
                    "replace investment selection with a school-finance governance assessment",
                    safe_transformation=investment_transform,
                    claim_policy="governance_options_only_no_investment_recommendation",
                    excluded_data_concepts=[
                        "specific_security_selection", "financial_return_prediction",
                    ],
                    action_data_use_concepts=[],
                )
            else:
                add("finance_procurement_memo", "required", "govern the financial-value decision")
        if family == "records_regulatory" and "possible_regulatory_trigger" in signals:
            add("regulatory_notification_assessment", "required", "assess a genuine reporting trigger")
        if severity in {"critical", "high"}:
            add("regulatory_notification_assessment", "recommended", "check current reporting requirements")
            add("post_incident_review", "recommended", "support controlled follow-up")
        family_default_roles = set(
            self.policy.get("family_packs", {}).get(family) or []
        )
        has_contextual_core = any(
            entry.get("requirement") == "required"
            or role in family_default_roles
            or str(
                (entry.get("contract") or {}).get("selection_origin") or ""
            ) in {"explicit_request", "safe_substitution"}
            or (entry.get("contract") or {}).get("default_selected") is True
            for role, entry in entries.items()
        )
        if not has_contextual_core and not official_record_mutation:
            # Preserve configured priority; ``family_default_roles`` above is
            # only for membership checks.
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
            if role and not unsupported_metric_claim and not existing.intersection(entries):
                add(role, "required", "a governed draft is required before external release")

        for release_spec in external_release_specs:
            linked_role = str(
                release_spec.get("linked_artifact_role") or ""
            ).strip().lower()
            channel = str(release_spec.get("channel") or "").strip().lower()
            if linked_role in entries and channel:
                entries[linked_role]["contract"].setdefault("channel", channel)

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

        # A pure broad-release request with no separately requested internal
        # document used to receive both the privacy-safe companion notice and
        # a generic ``user_titled_document`` inserted by the family default.
        # That redundant internal fallback could echo the very PII rejected
        # from the community draft. Keep only the governed safe companion and
        # its release gate; explicit internal reports remain untouched.
        if (
            sensitive_broadcast
            and not has_explicit_outputs
            and external_requests.intersection({
                "school_community", "public_media",
            })
        ):
            generic_entry = entries.get("user_titled_document")
            if generic_entry and str(
                generic_entry["contract"].get("selection_origin") or ""
            ) == "policy_required":
                entries.pop("user_titled_document", None)

        # Policy-required incident, guardian and medical artifacts may be
        # inserted after semantic requested-output reconciliation.  Apply the
        # same status-manipulation contract to every resulting artifact so a
        # late-added internal report cannot retain ``Dato'/PIBG`` or the
        # instruction to soften/omit facts merely because it was not one of
        # the model's original output objects.
        if status_bias or safe_status_constraint:
            for entry in entries.values():
                contract = entry["contract"]
                exclusions = set(contract.get("excluded_data_concepts") or [])
                exclusions.update({"socioeconomic_data", "differential_treatment"})
                existing_transform = str(
                    contract.get("safe_transformation") or ""
                ).strip()
                boundary_transform = (
                    status_transform if status_bias else
                    "Honor the user's explicit prohibition on status-based treatment."
                )
                contract.update({
                    "safe_transformation": " ".join(
                        item for item in (
                            existing_transform, boundary_transform,
                        ) if item
                    ),
                    "excluded_data_concepts": sorted(exclusions),
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
        if not explicit_language_request:
            # The semantic model may infer helpful languages, but adding a
            # language changes the requested artifact. Source text owns this
            # format contract; ordinary English/Malay/Chinese prompts remain
            # monolingual unless the user explicitly asks otherwise.
            requested_pack_languages = []
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
                    r"berhampiran|ibu\s+bapa|penjaga|laporan|draf|hantar|"
                    r"serahkan|kemukakan|kelulusan|emel|hubungi|maklumkan|"
                    r"siarkan|kepada|pejabat\s+pendidikan|"
                    r"kementerian\s+pendidikan)\b",
                    lower_text,
                )
                requested_pack_languages = ["ms"] if len(malay_cues) >= 2 else ["en"]
        # Added safety/accountability artifacts must follow the same language
        # contract as the explicitly requested files. Otherwise a provider
        # fallback creates a half-Malay/half-English response pack.
        for entry in entries.values():
            if (
                not explicit_language_request
                or not entry["contract"].get("requested_languages")
            ):
                entry["contract"]["requested_languages"] = list(
                    requested_pack_languages
                )

        selected_set = set(selected_deliverable_ids or [])
        has_selection = selected_deliverable_ids is not None
        contextual_recommendation_roles = set(family_default_roles)
        if str(situation.get("phase") or "") in {
            "just_occurred", "post_incident", "follow_up",
        }:
            contextual_recommendation_roles.add("post_incident_review")
        if severity in {"critical", "high"}:
            contextual_recommendation_roles.add(
                "regulatory_notification_assessment"
            )
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
                else (
                    item["deliverable_id"] in selected_set
                    if has_selection
                    else (
                        item.get("selection_origin") == "system_recommendation"
                        and item.get("default_selected") is not False
                        and (
                            item.get("default_selected") is True
                            or
                            item.get("reason") != "semantic system recommendation"
                            or role in contextual_recommendation_roles
                        )
                    )
                )
            )
            deliverables.append(item)

        for custom in (custom_deliverables or [])[:12]:
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
            requested_custom_role = str(
                custom.get("artifact_role") or ""
            ).strip().lower()
            role = (
                requested_custom_role
                if requested_custom_role in self.catalog
                else "public_communication_draft" if audience == "public"
                else "school_parent_notice" if audience == "school_community"
                else "user_titled_document"
            )
            custom_id = _new_id("custom")
            item = self._catalog_item(role, "user_added", "added by the user")
            explicit_obligation = bool(
                custom.get("source_obligation_id")
                or custom.get("explicit") is True
            )
            item.update({
                "deliverable_id": custom_id, "label": label,
                "filename": self._custom_filename(label), "audience": audience,
                "recipient_type": recipient_type,
                "requirement": (
                    "explicit_user_request" if explicit_obligation
                    else "user_added"
                ),
                "required": explicit_obligation, "selected": True,
                "source_fact_ids": fact_ids, "action_data_use_concepts": [],
                "requested_languages": [
                    str(language).lower()
                    for language in (
                        custom.get("languages") or requested_pack_languages
                    )
                    if str(language).lower() in {"en", "ms", "zh"}
                ],
            })
            if custom.get("purpose"):
                item["purpose"] = str(custom.get("purpose"))[:500]
            if custom.get("source_obligation_id"):
                item["source_obligation_id"] = str(
                    custom.get("source_obligation_id")
                )[:180]
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
        if internal_repository_write:
            linked_role = (
                "school_document" if "school_document" in entries
                else next(iter(entries), "")
            )
            deliverables.append({
                "deliverable_id": "internal_repository_publish_gate",
                "artifact_role": "internal_repository_publish_gate",
                "label": "Request controlled update to the school staff repository",
                "filename": "",
                "kind": "system_action",
                "mode": "internal_repository_publish",
                "audience": "internal",
                "recipient_type": "school_staff_repository",
                "requirement": "explicit_user_request",
                "required": True,
                "selected": True,
                "reason": (
                    "the user explicitly requested a persistent internal "
                    "school-content update"
                ),
                "source_policy": "human_authorised_internal_publish_only",
                "linked_deliverable_id": linked_role,
                "system_operation": "publish_internal_school_resource",
                "purpose": (
                    "publish the reviewed draft to the named internal school "
                    "repository only after human approval"
                ),
                "expected_effect": (
                    "update the internal school repository without claiming "
                    "that publication already occurred"
                ),
                "approval_boundary": "human_required_before_internal_publish",
                "action_data_use_concepts": ["internal_repository_write"],
            })

        def resolve_release_link(recipient: str, proposed: str | None) -> str:
            linked = str(proposed or "").strip()
            if linked in entries:
                return linked
            recipient_link = next(
                (
                    role for role in sorted(
                        recipient_existing_roles.get(recipient, set())
                    )
                    if role in entries
                ),
                "",
            )
            if recipient_link:
                return recipient_link
            # A safe transformation can replace a requested official report
            # with an evidence-status report. The release gate must follow the
            # artifact that actually exists, never a superseded/nonexistent
            # proposal id.
            preferred_safe_roles = {
                "education_authority": (
                    "education_authority_report",
                    "evidence_status_report",
                    "regulatory_notification_assessment",
                    "internal_incident_report",
                ),
                "guardian": (
                    "private_parent_notice",
                    "school_parent_notice",
                ),
                "public_media": ("public_communication_draft",),
                "school_community": ("school_parent_notice",),
            }.get(recipient, ())
            return next(
                (role for role in preferred_safe_roles if role in entries),
                linked,
            )

        gated_recipients: set[str] = set()
        for spec in external_release_specs:
            recipient = str(spec.get("recipient_type") or "").strip().lower()
            channel = str(spec.get("channel") or "other").strip().lower()[:40]
            linked_role = resolve_release_link(
                recipient,
                spec.get("linked_artifact_role")
                or recipient_draft_role.get(recipient),
            )
            if recipient not in external_requests:
                continue
            gated_recipients.add(recipient)
            audience = {
                "public_media": "public", "guardian": "private_recipient",
                "school_community": "school_community",
                "event_organizer": "private_recipient",
                "transport_provider": "private_recipient", "vendor": "private_recipient",
                "external_stakeholder": "private_recipient",
            }.get(recipient, "external_agency")
            deliverables.append({
                "deliverable_id": f"external_release_{recipient}_{channel}",
                "artifact_role": "external_release_gate",
                "label": (
                    f"Request {channel} release to "
                    f"{recipient.replace('_', ' ')}"
                ),
                "filename": "", "kind": "external_action", "mode": "external_release",
                "audience": audience, "recipient_type": recipient,
                "channel": channel,
                "requirement": "explicit_user_request", "required": True,
                "selected": True, "reason": "the user explicitly requested this external channel",
                "source_policy": "governed_release_only",
                "linked_deliverable_id": linked_role,
                "release_prerequisite_missing": release_references_missing_payload,
                "release_prerequisite_reason": (
                    "referenced_release_payload_or_attachment_not_supplied"
                    if release_references_missing_payload else ""
                ),
                "action_data_use_concepts": ["external_release"],
            })
        for recipient in sorted(external_requests - gated_recipients):
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
                "channel": "other",
                "requirement": "explicit_user_request", "required": True,
                "selected": True, "reason": "the user explicitly requested an external action",
                "source_policy": "governed_release_only",
                "linked_deliverable_id": resolve_release_link(
                    recipient, recipient_draft_role.get(recipient),
                ),
                "release_prerequisite_missing": release_references_missing_payload,
                "release_prerequisite_reason": (
                    "referenced_release_payload_or_attachment_not_supplied"
                    if release_references_missing_payload else ""
                ),
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
        source_text = str(situation.get("case_summary") or "")
        source_physical, _ = _physical_incident_flags(source_text)
        semantic_signals = set(situation.get("signals") or [])
        semantic_safety = bool(
            not str(situation.get("compiler_source") or "").startswith("fallback")
            and semantic_signals.intersection({
                "active_danger", "injury_or_illness", "person_missing",
                "safeguarding_concern", "external_help_may_be_required",
            })
        )
        source_safety = bool(
            semantic_safety
            or source_physical
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
        source_resolution = _explicitly_resolved_or_contained_safety(source_text)
        if (
            "active_danger" in semantic_signals
            and not _explicitly_no_unmet_emergency(source_text)
            and not _explicitly_contained_structural_hazard(source_text)
        ):
            # Safe/supervised pupils do not resolve a distinct active hazard
            # such as smoke, fire or traffic exposure. Ask the bounded human
            # question unless that hazard itself was expressly contained.
            source_resolution = False
        # 2026-08-21: this used to ALSO require the compiler's own unknowns
        # list to carry a "life_safety" (or "danger_still_present") tag
        # before asking. That tag is model-assigned per request and not
        # reliable — the same active_danger/critical-severity case got
        # tagged "life_safety" on one run and "content_only" on the next,
        # silently skipping this question on a genuinely dangerous case.
        # source_safety below is already the real signal (deterministic
        # hazard/safeguarding regex OR semantic danger signals); severity
        # plus source_safety is sufficient on its own — do not gate a
        # life-safety question on a label the model may or may not choose
        # to apply.
        if (
            not answers.get("immediate_danger")
            and situation["severity"] in {"critical", "high"}
            and source_safety
            and not source_resolution
        ):
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
                or (action.metadata or {}).get("system_level_change_action")
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
                operation=str(
                    item.get("system_operation") or "update_official_record"
                ),
                target=str(
                    item.get("recipient_type") or "student_information_system"
                ),
                purpose=str(
                    item.get("purpose")
                    or "request a controlled official student-record change"
                ),
                expected_effect=str(
                    item.get("expected_effect")
                    or "change the official record only after independent "
                    "human approval"
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
            "source_obligation_id": item.get("source_obligation_id") or "",
            "intent_contract_id": (
                (pack.get("intent_contract") or {}).get("contract_id") or ""
            ),
            "linked_deliverable_id": item.get("linked_deliverable_id"),
            "release_prerequisite_missing": bool(
                item.get("release_prerequisite_missing")
            ),
            "release_prerequisite_reason": (
                item.get("release_prerequisite_reason") or ""
            ),
            "channel": item.get("channel") or "",
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
            "restricted_internal_audience": bool(
                item.get("restricted_internal_audience")
            ),
            "audience_boundary": item.get("audience_boundary") or "",
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
            system_role = str(item.get("artifact_role") or "")
            action.metadata.update({
                "official_record_change_action": (
                    system_role == "official_record_change_gate"
                ),
                "system_level_change_action": True,
                "system_level_change": True,
                "release_state": "not_applicable",
                "approval_boundary": (
                    item.get("approval_boundary")
                    or "human_required_before_record_change"
                ),
                "data_use_concepts": (
                    item.get("action_data_use_concepts")
                    or ["official_record_change"]
                ),
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
