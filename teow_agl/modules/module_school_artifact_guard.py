"""School-output contracts for open, governed school-administration tasks.

The live planner may propose several deliverables in one turn.  This module
keeps those deliverables separate and makes the competition default explicit:

* the conversational reply is a short cover message;
* every text artifact is an UTF-8 Markdown file;
* every artifact has its own role, audience and stable action id;
* deterministic checks reject cross-file contamination and common unsupported
  "completed action" claims before Module 110 can show VERIFIED.

The LLM may draft prose, but these functions own the output boundary.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import urlparse

from ..models import CandidateAction, CandidatePlan, ExecutionResult, TaskEnvelope
from .module_school_release_intent import (
    infer_explicit_external_recipients,
    negated_external_recipients,
    release_is_globally_negated,
    requests_external_release,
)
from .module_school_privacy import (
    requires_broad_redaction,
    source_direct_pii_values,
    source_has_individual_sensitive_detail,
    source_identifiers,
    source_individual_mark_values,
)


_TEXT_FILE_TOOLS = {"docx", "report", "fs"}
_GENERIC_STEMS = {"doc", "document", "report", "draft", "output", "file"}

# Pseudonymous initials are deliberately not treated as ordinary names by the
# general privacy extractor.  For broad outputs they are identifiers all the
# same, and exact medication/result literals must not survive an anonymous
# transformation.  Keep this small literal backstop beside post-generation
# verification so a provider cannot reintroduce what compilation removed.
_PSEUDONYMOUS_INITIALS = re.compile(
    r"(?<!\w)((?:[A-Za-z]\.){2,5})(?!\w)"
)
_FRACTIONAL_RESULT = re.compile(r"(?<!\d)(\d{1,3}\s*/\s*\d{1,3})(?!\d)")
_SENSITIVE_MEDICATION = re.compile(
    r"\b(ritalin|methylphenidate)\b", re.IGNORECASE,
)
_SENSITIVE_DIAGNOSIS_LITERAL = re.compile(
    r"\b(adhd|autis(?:m|tic)|dyslex(?:ia|ic)|dyscalculia|dysgraphia|"
    r"asperger(?:'s)?|down(?:'s)?\s+syndrome|cerebral\s+palsy|"
    r"epilep(?:sy|tic)|diabet(?:es|ic)|asthma(?:tic)?|"
    r"anxiety|depression|bipolar|schizophrenia|"
    r"hearing\s+impairment|visual\s+impairment)\b",
    re.IGNORECASE,
)


_HARD_VALIDATION_MARKERS = (
    "broad_notice_contains_",
    "restricted_staff_boundary",
    "excluded_",
    "source_identifier",
    "source_pii",
    "source_medication",
    "source_health_detail",
    "individual_mark",
    "policy:",
    "provider_unavailable",
    "semantic_grounding_audit_unavailable",
    "goal_alignment_audit_unavailable",
)

_EXTERNAL_DRAFT_ROLES = {
    "private_parent_notice",
    "school_parent_notice",
    "public_communication_draft",
    "external_stakeholder_message",
    "education_authority_request",
    "education_authority_report",
}
_INTERNAL_RELEASE_SECTION = re.compile(
    r"(?ims)^\s*#{1,6}\s+(?:proposed\s+arrangements\s*[-—:]\s*"
    r"subject\s+to\s+(?:school\s+)?approval|approval\s+request|"
    r"release\s+(?:approval|control)|internal\s+(?:approval|review))"
    r"\s*\n.*?(?=^\s*#{1,6}\s+|^\s*---\s*$|\Z)"
)
_INTERNAL_RELEASE_PARAGRAPH = re.compile(
    r"(?ims)^\s*(?:\*\*)?(?:approval\s+request|release\s+approval)"
    r"(?:\*\*)?\s*:.*?(?=\n\s*\n|\Z)"
)
_INTERNAL_RELEASE_SENTENCE = re.compile(
    r"(?is)(?:we|i)\s+(?:request|seek|require|need)\s+"
    r"(?:human\s+)?(?:approval|authorisation|authorization)"
    r"[^.!?\n]{0,180}\b(?:send|email|publish|share|release|post)\b"
)


def strip_internal_release_control(
    action: CandidateAction,
    content: str,
) -> str:
    """Keep governance workflow instructions out of recipient-facing prose.

    A release request belongs in the separate governed action/gate.  Some live
    models append an internal ``Approval request`` to the email or notice that
    will eventually be sent to the recipient.  Removing that control block is
    a presentation-boundary repair; it does not approve or execute anything.
    """
    role = str((action.metadata or {}).get("artifact_role") or "").lower()
    text = str(content or "")
    if role not in _EXTERNAL_DRAFT_ROLES or not text:
        return text
    text = _INTERNAL_RELEASE_SECTION.sub("", text)
    text = _INTERNAL_RELEASE_PARAGRAPH.sub("", text)
    paragraphs = re.split(r"(\n\s*\n)", text)
    for index in range(0, len(paragraphs), 2):
        paragraph = paragraphs[index]
        if _INTERNAL_RELEASE_SENTENCE.search(paragraph):
            paragraphs[index] = ""
    text = "".join(paragraphs)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify_school_validation_issues(
    action: CandidateAction,
    issues: dict[str, list[str]] | Iterable[str],
) -> dict[str, list[str]]:
    """Classify generation checks without weakening the governance boundary.

    ``hard_block`` is reserved for privacy, data-use, authorisation and
    unavailable-auditor failures. ``repair_once`` covers quality or grounding
    defects that may be regenerated under the same policy. ``review_note`` is
    deliberately narrow: only non-safety similarity warnings may survive, and
    only after the independent semantic audit has accepted both artifacts.

    The classifier changes what happens *after* a check; it never changes the
    check itself or promotes a failed privacy/policy result to a warning.
    """
    if isinstance(issues, dict):
        flat = [
            f"{layer}:{item}"
            for layer, values in issues.items()
            for item in values
        ]
    else:
        flat = [str(item) for item in issues if str(item)]

    result = {"hard_block": [], "repair_once": [], "review_note": []}
    for issue in flat:
        value = issue.casefold()
        # Action ids may prefix a grouped issue. Inspect the whole string so
        # the policy-layer marker and privacy suffix both remain visible.
        if any(marker in value for marker in _HARD_VALIDATION_MARKERS):
            result["hard_block"].append(issue)
        elif value.startswith("cross_artifact_similarity:"):
            result["review_note"].append(issue)
        else:
            result["repair_once"].append(issue)
    return result

_OFFICIAL_SAFETY_DOMAINS = (
    "moh.gov.my",
    "moe.gov.my",
    "bomba.gov.my",
    "malaysia.gov.my",
    "rmp.gov.my",
    "nadma.gov.my",
)
_URL_CANDIDATE = re.compile(r"https?://[^\s<>\[\]]+", re.IGNORECASE)
_CLINICAL_CEILING_ROLES = {
    "medical_handover_script",
    "emergency_contact_script",
    "fire_rescue_contact_script",
    "site_safety_checklist",
    "food_safety_response",
    "internal_incident_report",
    "school_document",
    # Parent and external-facing drafts can cause direct harm if a model adds
    # treatment technique from general knowledge. They obey the same ceiling
    # as operational safety artifacts.
    "private_parent_notice",
    "school_parent_notice",
    "public_communication_draft",
    "external_stakeholder_message",
    "education_authority_report",
    "education_authority_request",
    "regulatory_notification_assessment",
}
_UNSOURCED_CLINICAL_INSTRUCTION = re.compile(
    r"\b(?:remove|scrape)\s+(?:the\s+)?stinger\b|"
    r"\bapply\s+(?:an?\s+)?(?:cold|ice)\s+(?:pack|compress)\b|"
    r"\bmonitor\b[^\n]{0,45}\b\d+\s*(?:minutes?|mins?|hours?|hrs?)\b|"
    r"\b(?:administer|give|take)\b[^\n]{0,35}\b(?:epinephrine|adrenaline|"
    r"antihistamine|medication|medicine|painkiller)\b|"
    r"\b(?:induce\s+vomiting|apply\s+(?:a\s+)?tourniquet|suck\s+(?:out\s+)?"
    r"(?:the\s+)?venom|immobili[sz]e\s+the\s+limb)\b",
    re.IGNORECASE,
)
_CLINICAL_INSTRUCTION_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|must\s+not|never|only\s+by\s+(?:a\s+)?qualified|"
    r"only\s+if\s+directed|as\s+directed\s+by|follow\s+(?:the\s+)?"
    r"(?:verified|approved|official)\s+(?:sop|protocol|guidance))\b",
    re.IGNORECASE,
)

_ROLE_FILENAMES = {
    "internal_incident_report": "internal_incident_report.md",
    "private_parent_notice": "parent_notification_draft.md",
    "school_parent_notice": "school_parent_notice_draft.md",
    "discipline_investigation_report": "discipline_investigation_report.md",
    "internal_action_plan": "internal_action_plan.md",
    "public_communication_draft": "public_communication_draft.md",
    "emergency_contact_script": "emergency_contact_script.md",
    "fire_rescue_contact_script": "fire_rescue_contact_script.md",
    "medical_handover_script": "medical_handover_script.md",
    "site_safety_checklist": "site_safety_checklist.md",
    "student_accountability_checklist": "student_accountability_checklist.md",
    "regulatory_notification_assessment": "regulatory_notification_assessment.md",
    "education_authority_report": "education_authority_report_draft.md",
    "education_authority_request": "education_authority_request_draft.md",
    "staff_internal_notice": "staff_internal_notice.md",
    "safeguarding_action_plan": "safeguarding_action_plan.md",
    "evidence_preservation_log": "evidence_preservation_log.md",
    "cyber_incident_response": "cyber_incident_response.md",
    "finance_procurement_memo": "finance_procurement_memo.md",
    "event_action_plan": "event_action_plan.md",
    "external_stakeholder_message": "external_stakeholder_message_draft.md",
    "student_support_plan": "student_support_plan.md",
    "transport_response_plan": "transport_response_plan.md",
    "food_safety_response": "food_safety_response.md",
    "post_incident_review": "post_incident_review.md",
    "evidence_status_report": "evidence_status_report.md",
    "measurement_plan": "outcome_measurement_plan.md",
    "speech_or_address": "speech_or_address_draft.md",
    "meeting_minutes": "meeting_minutes_draft.md",
    "duty_roster": "duty_roster_draft.md",
    "timetable_or_schedule": "timetable_or_schedule_draft.md",
    "curriculum_continuity_plan": "curriculum_continuity_plan.md",
    "user_titled_document": "requested_school_document.md",
    "school_document": "school_document_draft.md",
}

_ROLE_AUDIENCE = {
    "internal_incident_report": "internal",
    "discipline_investigation_report": "internal",
    "internal_action_plan": "internal",
    "private_parent_notice": "private_recipient",
    "school_parent_notice": "school_community",
    "public_communication_draft": "public",
    "emergency_contact_script": "external_agency",
    "fire_rescue_contact_script": "external_agency",
    "medical_handover_script": "external_agency",
    "site_safety_checklist": "internal",
    "student_accountability_checklist": "internal",
    "regulatory_notification_assessment": "internal",
    "education_authority_report": "external_agency",
    "education_authority_request": "external_agency",
    "staff_internal_notice": "internal",
    "safeguarding_action_plan": "internal",
    "evidence_preservation_log": "internal",
    "cyber_incident_response": "internal",
    "finance_procurement_memo": "internal",
    "event_action_plan": "internal",
    "external_stakeholder_message": "private_recipient",
    "student_support_plan": "internal",
    "transport_response_plan": "internal",
    "food_safety_response": "internal",
    "post_incident_review": "internal",
    "evidence_status_report": "internal",
    "measurement_plan": "internal",
    "speech_or_address": "school_community",
    "meeting_minutes": "internal",
    "duty_roster": "internal",
    "timetable_or_schedule": "internal",
    "curriculum_continuity_plan": "internal",
    "user_titled_document": "internal",
    "school_document": "internal",
}

_PARENT_AUDIENCE_MARKERS = (
    re.compile(r"(?mi)^#\s+.*(?:parent|guardian).*(?:notice|notification|letter)"),
    re.compile(r"(?mi)^dear\s+(?:parent|guardian|family|\[)"),
)

_FORMAL_LETTER_MARKERS = (
    re.compile(r"(?mi)^dear\s+(?:mr\.?|mrs\.?|ms\.?)"),
    re.compile(r"(?mi)^sincerely,?\s*$"),
    re.compile(r"(?mi)^yours\s+(?:faithfully|sincerely),?\s*$"),
)

_PARENT_LETTER_MARKERS = (
    *_PARENT_AUDIENCE_MARKERS,
    *_FORMAL_LETTER_MARKERS,
)

_INTERNAL_REPORT_MARKERS = (
    re.compile(r"(?mi)^#\s+.*internal.*(?:incident|investigation|report)"),
    re.compile(r"(?mi)^##\s+(?:witness(?:es)?|responsibility|evidence log|internal investigation|prepared by)\s*$"),
    re.compile(r"(?i)for internal (?:use|review) only"),
)

_BRACKET_PLACEHOLDER = re.compile(
    r"\[(?:your\s+name|your\s+position|parent(?:'s)?\s+name|guardian\s+name|"
    r"school\s+name|contact\s+information|today(?:'s)?\s+date|insert[^\]]*)\]",
    re.IGNORECASE,
)

_NEGATIVE_QUALIFIER = re.compile(
    r"\b(?:tbc|unknown|unverified|not\s+(?:yet\s+)?verified|not\s+confirmed|"
    r"not\s+provided|not\s+available|not\s+known|whether|"
    r"to\s+be\s+confirmed|proposed|recommended|"
    r"should|must\s+be\s+verified)\b",
    re.IGNORECASE,
)

# Epistemic-status boundary for operational planning. A draft may contain
# useful options, but details that the user did not supply must never read as
# already-decided school arrangements. These patterns are deliberately about
# evidence-bearing specificity (times, deadlines, channels and quantities),
# not about school scenarios or prompt keywords.
_PROPOSAL_QUALIFIER = re.compile(
    r"\b(?:tbc|unknown|unverified|not\s+(?:yet\s+)?confirmed|"
    r"to\s+be\s+confirmed|proposed|proposal|recommended|recommendation|"
    r"subject\s+to\s+(?:school\s+)?(?:approval|confirmation|review)|"
    r"pending\s+(?:approval|confirmation|review)|optional|option|"
    r"if\s+(?:approved|required|needed|available)|may|could|consider)\b|"
    r"(?:cadangan|dicadangkan|tertakluk\s+kepada\s+kelulusan|"
    r"menunggu\s+pengesahan|akan\s+disahkan)|"
    r"(?:\u5efa\u8bae|\u5efa\u8b70|\u62df\u8bae|\u64ec\u8b70|\u5f85\u6279\u51c6|\u5f85\u78ba\u8a8d|\u5f85\u786e\u8ba4|\u6709\u5f85\u786e\u8ba4|\u6709\u5f85\u78ba\u8a8d)",
    re.IGNORECASE,
)
_PROPOSAL_SECTION = re.compile(
    r"\b(?:proposed|proposal|recommended|recommendation|options?|"
    r"subject\s+to\s+approval|pending\s+approval)\b|"
    r"(?:cadangan|pilihan|tertakluk\s+kepada\s+kelulusan)|"
    r"(?:\u5efa\u8bae|\u5efa\u8b70|\u62df\u8bae|\u64ec\u8b70|\u5f85\u6279\u51c6)",
    re.IGNORECASE,
)
_CLOCK_TIME = re.compile(
    r"(?<!\w)(?:(?:[01]?\d|2[0-3]):\d{2}"
    r"(?:\s*(?:a\.?m\.?|p\.?m\.?))?|"
    r"(?:[01]?\d|2[0-3])\.\d{2}\s*(?:a\.?m\.?|p\.?m\.?)|"
    r"(?:0?[1-9]|1[0-2])\s*(?:a\.?m\.?|p\.?m\.?))(?!\w)",
    re.IGNORECASE,
)
_OPERATIONAL_DEADLINE = re.compile(
    r"\bwithin\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:(?:working|school|calendar)\s+)?(?:hours?|days?|weeks?)\b|"
    r"\bno\s+later\s+than\s+[^.;\n]{1,45}|"
    r"\b(?:dalam\s+tempoh)\s+(?:satu|dua|tiga|\d+)\s+(?:jam|hari|minggu)\b|"
    r"(?:\u5728|\u65bc|\u4e8e)(?:\u4e00|\u4e8c|\u4e09|\u56db|\u4e94|\u516d|\u4e03|\u516b|\u4e5d|\u5341|\d+)(?:\u4e2a|\u500b)?(?:\u5c0f\u65f6|\u5c0f\u6642|\u5929|\u5468|\u9031)\u5185",
    re.IGNORECASE,
)
_OPERATIONAL_CHANNEL = re.compile(
    r"\b(?:whats\s*app|telegram|walkie[-\s]?talk(?:ie|y)s?|two[-\s]?way\s+radio|"
    r"direct\s+(?:phone\s+)?line|sms|text\s+message|email\s+(?:group|list)|"
    r"public[-\s]address\s+system|pa\s+system)\b",
    re.IGNORECASE,
)
_OPERATIONAL_QUANTITY = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:tables?|chairs?|sessions?|teams?|groups?|coordinators?|marshals?|"
    r"volunteers?|staff\s+members?|copies|devices?|radios?|rooms?)\b",
    re.IGNORECASE,
)
_OPERATIONAL_ASSIGNMENT = re.compile(
    r"\b(?:the\s+)?(?:principal|headteacher|headmaster|senior\s+assistant|"
    r"coordinator|class\s+teacher|teacher|staff\s+member|security\s+officer|"
    r"marshal)\s+(?:will|shall|must|is\s+to|has\s+to)\s+"
    r"(?:lead|chair|manage|supervise|coordinate|report|brief|contact|notify|"
    r"inspect|approve|record|monitor)\b|"
    r"\bappoint\s+(?:the\s+)?(?:principal|headteacher|headmaster|"
    r"senior\s+assistant|coordinator|teacher|staff\s+member|"
    r"security\s+officer|marshal)\b",
    re.IGNORECASE,
)
_OPERATIONAL_FACILITY = re.compile(
    r"\b(?:use|reserve|allocate|open|close|set\s+up|designate)\s+"
    r"(?:the\s+)?(?:school\s+hall|assembly\s+hall|canteen|library|"
    r"computer\s+lab|science\s+lab|meeting\s+room|prayer\s+room|"
    r"main\s+gate|rear\s+gate|gate\s+\d+|room\s+\d+)\b",
    re.IGNORECASE,
)

# Per-artifact policy contracts are stronger than ordinary source grounding.
# A source may explicitly contain an unsafe instruction (for example, "Ali is
# poor, so monitor him more closely").  Being source-backed must never make
# that instruction admissible after the compiler has excluded the underlying
# data use.  These multilingual matchers therefore run on GENERATED prose,
# after the LLM writes and before any artifact may verify.
_POLICY_SOCIOECONOMIC_DETAIL = re.compile(
    r"\b(?:poor|impoverished|low[- ]income|disadvantaged|deprived)\s+"
    r"(?:family|household|background)\b|"
    r"\b(?:bad|poor|difficult|unstable|challenging)\s+family\s+background\b|"
    r"\b(?:family|household|guardian|parent(?:s)?)(?:['’]s)?\s+"
    r"(?:income|financial\s+(?:status|background)|socioeconomic\s+status)\b|"
    r"\b(?:socioeconomic|socio-economic)\s+(?:status|background)\b|"
    r"\b(?:comes?|came|is|was)\s+from\s+(?:a\s+)?poor\s+family\b|"
    r"\bb40(?:\s+(?:family|household))?\b|"
    r"\b(?:keluarga\s+miskin|keluarga\s+berpendapatan\s+rendah|"
    r"pendapatan\s+(?:keluarga|isi\s+rumah|ibu\s+bapa|penjaga)|"
    r"latar\s+belakang\s+keluarga(?:\s+(?:miskin|bermasalah|susah))?|"
    r"status\s+sosioekonomi|golongan\s+b40|daripada\s+keluarga\s+miskin)\b|"
    r"(?:贫困|貧困|低收入|清寒)家庭|家庭(?:贫困|貧困|收入|经济状况|經濟狀況)|"
    r"家长收入|家長收入|父母收入|家庭背景(?:不好|恶劣|惡劣|困难|困難)?|"
    r"社会经济地位|社會經濟地位|来自低收入家庭|來自低收入家庭|家境(?:贫困|貧困|不好)",
    re.IGNORECASE,
)

_POLICY_DIFFERENTIAL_ACTION = re.compile(
    r"\b(?:monitor|watch|track|observe|supervise|check\s+on)\b"
    r"[^.!?;\n]{0,55}\b(?:more\s+closely|closely|more\s+strictly|strictly|"
    r"more\s+frequently|frequently|extra|additional|heightened|increased|special)\b|"
    r"\b(?:extra|additional|heightened|increased|closer|special)\s+"
    r"(?:monitoring|surveillance|scrutiny|checks?|supervision)\b|"
    r"\b(?:label|flag|classif(?:y|ied)|mark)\b[^.!?;\n]{0,45}"
    r"\b(?:high[- ]risk|problem(?:atic)?\s+student|watch\s*list)\b|"
    r"\b(?:punish|discipline|sanction|penalise|penalize|treat)\b"
    r"[^.!?;\n]{0,50}\b(?:more\s+harshly|more\s+strictly|differently|"
    r"less\s+favourably|less\s+favorably)\b|"
    r"\b(?:single\s+out|differential\s+(?:monitoring|treatment|discipline))\b|"
    r"\b(?:pantau|memantau|dipantau|awasi|mengawasi|diawasi)\b"
    r"[^.!?;\n]{0,55}\b(?:lebih\s+(?:rapat|ketat|kerap)|secara\s+khusus)\b|"
    r"\b(?:pemantauan|pengawasan)\s+(?:tambahan|lebih\s+ketat|dipertingkatkan)\b|"
    r"\b(?:label|tandakan|klasifikasikan)\b[^.!?;\n]{0,45}"
    r"\b(?:berisiko\s+tinggi|bermasalah)\b|"
    r"\b(?:hukum|menghukum|disiplinkan|denda)\b[^.!?;\n]{0,45}"
    r"\b(?:lebih\s+keras|lebih\s+ketat|secara\s+berbeza)\b|"
    r"(?:加强|加強|特别|特別|严密|嚴密)(?:监控|監控|观察|觀察|监督|監督)|"
    r"(?:更密切|更严格|更嚴格|更频繁|更頻繁)(?:地)?(?:监控|監控|观察|觀察|监督|監督)|"
    r"(?:标记|標記|列为|列為)[^。！？；\n]{0,20}(?:高风险|高風險|问题学生|問題學生)|"
    r"(?:更严厉|更嚴厲)[^。！？；\n]{0,20}(?:处罚|處罰|惩罚|懲罰|纪律处分|紀律處分)|"
    r"(?:区别对待|區別對待|差别对待|差別對待)",
    re.IGNORECASE,
)

_POLICY_PROHIBITION = re.compile(
    r"\b(?:do\s+not|don't|must\s+not|should\s+not|may\s+not|cannot|can't|"
    r"never|no\s+(?:differential|different|extra|additional)|prohibit(?:ed|s)?|"
    r"forbid(?:den|s)?|exclude(?:d|s)?|regardless\s+of|without\s+using)\b|"
    r"\b(?:jangan|tidak\s+boleh|tidak\s+harus|dilarang|bukan\s+berdasarkan|"
    r"tanpa\s+menggunakan)\b|"
    r"(?:不得|不应|不應|不要|禁止|不可|不能|不因|不得因|无论|無論)",
    re.IGNORECASE,
)

_POLICY_PERSON_SPECIFIC = re.compile(
    r"\b(?:he|she|him|her|his|hers|this\s+(?:student|pupil|child)|"
    r"the\s+(?:student|pupil|child)(?:['’]s)?)\b|"
    r"\b(?:murid|pelajar)\s+(?:ini|tersebut)\b|\b(?:dirinya|keluarganya)\b|"
    r"(?:该生|該生|这名学生|這名學生|该学生|該學生|他的|她的)",
    re.IGNORECASE,
)

_BROAD_STAFF_AUDIENCE = re.compile(
    r"\b(?:all|every|whole|entire)[-\s]+(?:the[-\s]+)?"
    r"(?:(?:form|subject|class|school)[-\s]+(?:and[-\s]+)?)*"
    r"(?:teachers?|staff|school[-\s]+staff|employees?|educators?)\b|"
    r"\b(?:staff[- ]wide|school[- ]wide)\b|"
    r"\b(?:semua|seluruh)\s+(?:guru|kakitangan|warga\s+sekolah)\b|"
    r"(?:全体|全校|所有)(?:教师|教師|老师|老師|教职员|教職員|员工|員工)",
    re.IGNORECASE,
)

_LIMITED_INTERNAL_BOUNDARY = re.compile(
    r"\b(?:limited|restricted)\s+(?:to\s+)?(?:the\s+)?(?:authori[sz]ed\s+)?"
    r"(?:case\s+team|personnel|staff|officers?)\b|"
    r"\bauthori[sz]ed\s+case\s+team\s+only\b|\bneed[- ]to[- ]know\b|"
    r"\bakses\s+terhad\b|\bterhad\s+kepada\b[^.!?;\n]{0,60}"
    r"\b(?:diberi\s+kuasa|pasukan\s+kes|pegawai)\b|"
    r"(?:仅限|僅限)[^。！？；\n]{0,30}(?:授权|授權)(?:人员|人員|小组|小組|团队|團隊)|"
    r"(?:按需知悉|受限内部|受限內部)",
    re.IGNORECASE,
)

_RESTRICTED_INTERNAL_ROLES = {
    "discipline_investigation_report", "internal_incident_report",
    "staff_internal_notice", "safeguarding_action_plan",
    "evidence_preservation_log",
}


def _policy_clauses(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?。！？；;])|[\r\n]+", str(text or ""))
        if item.strip()
    ]


def _has_nonnegated_policy_match(pattern: re.Pattern, text: str) -> bool:
    return any(
        pattern.search(clause) and not _POLICY_PROHIBITION.search(clause)
        for clause in _policy_clauses(text)
    )


def _contains_excluded_socioeconomic_detail(text: str) -> bool:
    """Detect case-level socioeconomic detail, preserving generic safeguards.

    A generic boundary such as "socioeconomic status must not affect a
    discipline decision" is useful and does not assert a pupil's status.  A
    pupil-specific echo ("his poor family") remains a violation even when the
    sentence says not to use it, because the contract excludes that case fact.
    """
    for clause in _policy_clauses(text):
        if not _POLICY_SOCIOECONOMIC_DETAIL.search(clause):
            continue
        generic_prohibition = bool(_POLICY_PROHIBITION.search(clause))
        person_specific = bool(_POLICY_PERSON_SPECIFIC.search(clause))
        if generic_prohibition and not person_specific:
            continue
        return True
    return False


_EXCLUDED_FACT_ID_HINT = re.compile(
    r"(?:student|pupil|child|person|name|identifier|family|household|"
    r"socio|economic|income|welfare|benefit|housing|ppr|occupation|"
    r"financial|guardian|parent)",
    re.IGNORECASE,
)


def excluded_known_fact_values(action: CandidateAction) -> set[str]:
    """Return source fact values the action contract says not to repeat.

    Semantic intake may reduce a sensitive fact to a short value such as
    ``poor`` or ``Ali``.  Those fragments are too ambiguous for a global text
    regex, but their fact IDs (``family_background`` / ``student_name``) and
    the per-action exclusions make the meaning unambiguous.  This helper keeps
    that structured contract attached to validation and deterministic
    fallback instead of trusting prose-only keyword matching.
    """
    meta = action.metadata or {}
    excluded = {
        str(item).strip().lower()
        for item in (meta.get("excluded_data_concepts") or [])
        if str(item).strip()
    }
    if not excluded.intersection({
        "person_identifier", "student_sensitive_data",
        "socioeconomic_data", "differential_treatment",
    }):
        return set()
    values: set[str] = set()
    for item in meta.get("school_known_facts") or []:
        if not isinstance(item, dict):
            continue
        fact_id = str(item.get("fact_id") or "")
        value = str(item.get("value") or "").strip()
        if not value or not _EXCLUDED_FACT_ID_HINT.search(fact_id):
            continue
        lowered_id = fact_id.casefold()
        is_person = bool(re.search(
            r"(?:student|pupil|child|person|name|identifier)", lowered_id,
        ))
        is_status = bool(re.search(
            r"(?:family|household|socio|economic|income|welfare|benefit|"
            r"housing|ppr|occupation|financial|guardian|parent)",
            lowered_id,
        ))
        if (
            is_person and excluded.intersection({
                "person_identifier", "student_sensitive_data",
            })
        ) or (
            is_status and "socioeconomic_data" in excluded
        ):
            values.add(value)
    return values


def _generated_person_identifiers(text: str) -> set[str]:
    """High-precision names in a generated pupil case artifact."""
    value = str(text or "")
    found = {
        match.group(1)
        for match in re.finditer(
            r"(?i:\b(?:student|pupil|child|murid|pelajar)(?:\s+name)?)\s*"
            r"(?:is|named|:|-)\s*([A-Z][A-Za-z'’-]{1,40})\b",
            value,
        )
    }
    found.update(
        match.group(1)
        for match in re.finditer(
            r"\b([A-Z][A-Za-z'’-]{1,40})\b(?=[^.!?;\n]{0,70}"
            r"(?i:poor\s+family|low[- ]income|family\s+background|"
            r"caught\s+(?:stealing|taking)|stole|misconduct|disciplin|"
            r"monitor(?:ed|ing)?\s+more\s+closely))",
            value,
        )
    )
    non_names = {
        "student", "pupil", "child", "murid", "pelajar", "draft",
        "internal", "reported", "status", "tbc", "school", "discipline",
    }
    return {item for item in found if item.casefold() not in non_names}


def requires_restricted_staff_boundary(
    source_goal: str,
    *,
    role: str = "",
    metadata: dict | None = None,
) -> bool:
    """Fail closed for person-level cases requested for the whole staff.

    "Internal" is not automatically minimum-necessary.  A discipline or
    safeguarding file requested for every teacher is transformed into a
    de-identified, limited-case-team artifact.  Explicitly negated broadcast
    language ("do not send this to all staff") does not activate the rule.
    """
    meta = metadata or {}
    if meta.get("restricted_internal_audience") is True:
        return True
    if str(role or "").strip().lower() not in _RESTRICTED_INTERNAL_ROLES:
        return False
    source = str(source_goal or "")
    if not source_has_individual_sensitive_detail(source):
        return False
    return _has_nonnegated_policy_match(_BROAD_STAFF_AUDIENCE, source)


def school_policy_contract_issues(
    action: CandidateAction,
    content: str,
    source_goal: str,
    *,
    include_boundary: bool = True,
) -> list[str]:
    """Return deterministic violations of one artifact's exclusion contract."""
    meta = action.metadata or {}
    text = str(content or "")
    role = str(meta.get("artifact_role") or "").strip().lower()
    excluded = {
        str(item).strip().lower()
        for item in (meta.get("excluded_data_concepts") or [])
        if str(item).strip()
    }
    found: list[str] = []

    excluded_values = excluded_known_fact_values(action)
    leaked_excluded_values = {
        value for value in excluded_values
        if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text, re.IGNORECASE)
    }
    if leaked_excluded_values:
        found.append("excluded_structured_fact_reintroduced")

    if (
        "socioeconomic_data" in excluded
        and _contains_excluded_socioeconomic_detail(text)
    ):
        found.append("excluded_socioeconomic_data_reintroduced")
    if (
        "differential_treatment" in excluded
        and _has_nonnegated_policy_match(_POLICY_DIFFERENTIAL_ACTION, text)
    ):
        found.append("excluded_differential_treatment_reintroduced")

    restricted = requires_restricted_staff_boundary(
        source_goal, role=role, metadata=meta,
    )
    if restricted or "person_identifier" in excluded:
        source_names = {
            item for item in source_identifiers(source_goal)
            if item.casefold() not in {
                "draft", "prepare", "report", "notice", "student",
                "pupil", "child", "school", "teacher", "teachers",
            }
        }
        exact_leaks = {
            item for item in source_names
            if re.search(rf"(?<!\w){re.escape(item)}(?!\w)", text, re.IGNORECASE)
        }
        generated_names = _generated_person_identifiers(text)
        if exact_leaks or generated_names:
            found.append("restricted_staff_artifact_contains_person_identifier")

    if restricted:
        if _has_nonnegated_policy_match(_BROAD_STAFF_AUDIENCE, text):
            found.append("restricted_staff_artifact_keeps_broad_distribution")
        if include_boundary and text and not _LIMITED_INTERNAL_BOUNDARY.search(text):
            found.append("restricted_staff_boundary_missing")

    return found

# label, unsupported positive assertion in the artifact, positive evidence in
# the source request.  Merely naming an unknown field ("witnesses not
# verified") is not positive evidence.
_GROUNDING_RULES = (
    (
        "emergency_services_contacted_or_arrived",
        re.compile(
            r"(?:emergency services|ambulance|paramedics?|first responders?)"
            r"[^\n.!?]{0,100}(?:contacted|called|arrived|attended|responded|on[- ]scene)"
            r"|(?:contacted|called)[^\n.!?]{0,80}(?:emergency services|ambulance|paramedics?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:emergency services|ambulance|paramedics?|first responders?)"
            r"[^\n.!?]{0,100}(?:contacted|called|arrived|attended|responded|on[- ]scene)"
            r"|(?:contacted|called)[^\n.!?]{0,80}(?:emergency services|ambulance|paramedics?)",
            re.IGNORECASE,
        ),
    ),
    (
        "student_transported_or_admitted",
        re.compile(
            r"\b(?:was|has been|had been|were)\s+(?:transported|admitted)\b"
            r"|\b(?:transported|taken)\s+to\s+(?:a\s+)?(?:hospital|medical facility)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:was|has been|had been|were)\s+(?:transported|taken|admitted)\b"
            r"|\b(?:transported|taken)\s+to\s+(?:a\s+)?(?:hospital|medical facility)",
            re.IGNORECASE,
        ),
    ),
    (
        "medical_assessment_completed",
        re.compile(r"\bassessed by (?:medical|healthcare) (?:staff|professionals?|personnel)\b", re.IGNORECASE),
        re.compile(r"\bassessed by (?:medical|healthcare) (?:staff|professionals?|personnel)\b", re.IGNORECASE),
    ),
    (
        "medical_care_specificity_added",
        re.compile(
            r"\b(?:receiving|received|provided with)\s+(?:appropriate\s+)?"
            r"medical\s+(?:care|attention|treatment)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:receiving|received|provided with)\s+(?:appropriate\s+)?"
            r"medical\s+(?:care|attention|treatment)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "witnesses_or_statements_confirmed",
        re.compile(
            r"\b(?:witness(?:es)?\s+(?:were present|have provided|provided|were interviewed|gave statements?)"
            r"|several individuals were present)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:witness(?:es)?\s+(?:saw|observed|were present|have provided|provided|were interviewed|gave statements?)"
            r"|several individuals were present)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_investigation_confirmed",
        re.compile(
            r"\b(?:the school (?:is|was) cooperating (?:fully )?with (?:the )?(?:police|authorities)"
            r"|(?:police|authorities) (?:are|were) investigating"
            r"|investigation is ongoing)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:the school (?:is|was) cooperating (?:fully )?with (?:the )?(?:police|authorities)"
            r"|(?:police|authorities) (?:are|were) investigating"
            r"|investigation is ongoing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "family_already_contacted",
        re.compile(
            r"\b(?:(?:family|parent|guardian) (?:has been|was|were) (?:contacted|notified|informed)"
            r"|the school has (?:communicated|been in communication) with (?:the )?(?:family|parent|guardian))\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:(?:family|parent|guardian) (?:has been|was|were) (?:contacted|notified|informed)"
            r"|the school has (?:communicated|been in communication) with (?:the )?(?:family|parent|guardian))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "response_protocol_or_safety_action_completed",
        re.compile(
            r"\b(?:emergency response (?:protocol )?was (?:immediately |promptly )?(?:activated|initiated)"
            r"|safety measures have been taken"
            r"|the school is reviewing (?:its )?(?:current )?safety protocols)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:emergency response (?:protocol )?was (?:immediately |promptly )?(?:activated|initiated)"
            r"|safety measures have been taken"
            r"|the school is reviewing (?:its )?(?:current )?safety protocols)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "investigation_or_authority_coordination_underway",
        re.compile(
            r"\b(?:investigation(?: into [^\n.!?]{0,100})? (?:is|was) (?:underway|under way|ongoing)"
            r"|coordination with (?:relevant )?(?:police|authorities) (?:is|was) ongoing)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:investigation(?: into [^\n.!?]{0,100})? (?:is|was) (?:underway|under way|ongoing)"
            r"|coordination with (?:relevant )?(?:police|authorities) (?:is|was) ongoing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "information_gathering_already_underway",
        re.compile(
            r"\b(?:we|the school) (?:are|is) "
            r"(?:(?:\w+ly )?working (?:\w+ly )?to gather|"
            r"(?:\w+ly )?(?:gathering|collecting)) "
            r"(?:all )?(?:the )?"
            r"(?:necessary )?(?:information|facts|details)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:we|the school) (?:are|is) "
            r"(?:(?:\w+ly )?working (?:\w+ly )?to gather|"
            r"(?:\w+ly )?(?:gathering|collecting)) "
            r"(?:all )?(?:the )?"
            r"(?:necessary )?(?:information|facts|details)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "review_or_verification_underway",
        re.compile(
            r"\b(?:(?:the )?(?:details|matter|situation|incident) (?:are|is) "
            r"(?:currently )?under review|(?:we|the school) (?:are|is) "
            r"(?:currently )?(?:reviewing|in the process of (?:reviewing|verifying))|"
            r"verification (?:is|remains) ongoing)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:(?:the )?(?:details|matter|situation|incident) (?:are|is) "
            r"(?:currently )?under review|(?:we|the school) (?:are|is) "
            r"(?:currently )?(?:reviewing|in the process of (?:reviewing|verifying))|"
            r"verification (?:is|remains) ongoing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "necessary_steps_already_underway",
        re.compile(
            r"\b(?:(?:we|the school) (?:are|is)|and are) (?:currently )?taking "
            r"(?:all )?(?:the )?(?:necessary|appropriate) steps\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:(?:we|the school) (?:are|is)|and are) (?:currently )?taking "
            r"(?:all )?(?:the )?(?:necessary|appropriate) steps\b",
            re.IGNORECASE,
        ),
    ),
    (
        "information_gathering_committed",
        re.compile(
            r"\b(?:further|additional|more) (?:information|facts|details) "
            r"will be (?:gathered|collected|obtained)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:further|additional|more) (?:information|facts|details) "
            r"will be (?:gathered|collected|obtained)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "incident_reported_to_school_claimed",
        re.compile(
            r"\b(?:the )?(?:incident|matter|case) (?:was|has been) reported "
            r"to (?:the )?(?:school|school administration|principal|authorities)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:the )?(?:incident|matter|case) (?:was|has been) reported "
            r"to (?:the )?(?:school|school administration|principal|authorities)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "future_update_commitment",
        re.compile(
            r"\b(?:(?:we|the school) will (?:keep (?:you|the family) updated|"
            r"provide (?:you|the family) with (?:further|more|additional) "
            r"(?:information|details|updates)|share (?:further )?updates)|"
            r"(?:further|more|additional)\s+(?:information|details|updates)"
            r"(?:\s*,\s*if\s+needed\s*,)?\s+will be provided"
            r"(?:\s+by\s+(?:the\s+)?school(?:\s+office)?)?|"
            r"updates will be provided)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:(?:we|the school) will (?:keep (?:you|the family) updated|"
            r"provide (?:you|the family) with (?:further|more|additional) "
            r"(?:information|details|updates)|share (?:further )?updates)|"
            r"(?:further|more|additional)\s+(?:information|details|updates)"
            r"(?:\s*,\s*if\s+needed\s*,)?\s+will be provided"
            r"(?:\s+by\s+(?:the\s+)?school(?:\s+office)?)?|"
            r"updates will be provided)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "family_collection_instruction_added",
        re.compile(
            r"\b(?:please|parents?\s+(?:must|should)|you\s+(?:must|should))"
            r"[^\n.!?]{0,80}\b(?:collect|pick\s*up)\b"
            r"[^\n.!?]{0,60}\b(?:child|children|pupil|student)s?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:please|parents?\s+(?:must|should)|you\s+(?:must|should))?"
            r"[^\n.!?]{0,80}\b(?:collect|pick\s*up)\b"
            r"[^\n.!?]{0,60}\b(?:child|children|pupil|student)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "medical_team_communication_confirmed",
        re.compile(
            r"\bthe school is (?:in|maintaining|in constant) communication with (?:the )?medical team\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bthe school is (?:in|maintaining|in constant) communication with (?:the )?medical team\b",
            re.IGNORECASE,
        ),
    ),
)


def _is_text_file_action(action: CandidateAction) -> bool:
    tool = (action.tool or "").lower()
    op = (action.operation or "").lower()
    if tool not in _TEXT_FILE_TOOLS:
        return False
    if tool in {"docx", "report"}:
        return True
    return tool == "fs" and "save" in op


def infer_artifact_role(action: CandidateAction) -> str:
    """Infer a closed artifact role from the action, never from one keyword alone."""
    meta = action.metadata or {}
    canonical = str(meta.get("artifact_role") or "").strip().lower()
    if canonical in _ROLE_FILENAMES:
        return canonical
    text = " ".join(
        str(x or "") for x in (
            meta.get("artifact_role"), action.target, action.purpose,
            meta.get("title"), meta.get("audience"), meta.get("output_scope"),
        )
    ).lower()
    if any(k in text for k in (
        "parent", "guardian", "family notice", "private notification",
    )):
        return "private_parent_notice"
    if "disciplin" in text or "misconduct" in text or "violation" in text:
        return "discipline_investigation_report"
    if "incident" in text or "accident" in text or "safety report" in text:
        return "internal_incident_report"
    if any(k in text for k in ("facebook", "public post", "public notice", "announcement")):
        return "public_communication_draft"
    if any(k in text for k in ("action plan", "support plan", "options memo", "response plan")):
        return "internal_action_plan"
    return "school_document"


def _outputs_root(envelope: TaskEnvelope) -> Path:
    roots = [Path(p) for p in (envelope.workspace_roots or []) if str(p).strip()]
    for root in roots:
        if root.name.lower() == "outputs":
            return root
    return roots[-1] if roots else Path("outputs")


def _target_for(action: CandidateAction, role: str, envelope: TaskEnvelope) -> Path:
    raw = Path(action.target) if str(action.target or "").strip() else Path()
    if (action.metadata or {}).get("coverage_source") == "school_response_pack":
        # Response-pack files are always sandboxed under outputs. Never retain
        # a live planner's absolute path or directory, even when it happens to
        # be elsewhere inside a permitted workspace root.
        deliverable_id = str(
            (action.metadata or {}).get("deliverable_id") or ""
        )
        if deliverable_id.startswith("custom_") and raw.name:
            name = raw.with_suffix(".md").name
        elif role == "school_document" and raw.name:
            name = raw.with_suffix(".md").name
        else:
            name = _ROLE_FILENAMES.get(role, "school_document_draft.md")
        # Isolate arbitrary live cases so a later or concurrent task cannot
        # overwrite an earlier task's downloadable evidence.
        return _outputs_root(envelope) / envelope.task_id / Path(name).name
    stem = raw.stem.lower() if raw.name else ""
    if not raw.name or stem in _GENERIC_STEMS:
        return _outputs_root(envelope) / _ROLE_FILENAMES.get(role, "school_document_draft.md")
    if raw.parent == Path("."):
        return _outputs_root(envelope) / raw.with_suffix(".md").name
    return raw.with_suffix(".md")


def _artifact_label(action: CandidateAction) -> str:
    name = Path(action.target).stem.replace("_", " ").replace("-", " ").strip()
    return name or action.purpose or "school draft"


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text or ""))


def _looks_malay(text: str) -> bool:
    cues = re.findall(
        r"\b(?:sediakan|jangan|murid|pelajar|sekolah|kantin|berhampiran|"
        r"ibu\s+bapa|penjaga|laporan|draf|hantar|masih|berlaku|"
        r"serahkan|kemukakan|kelulusan|emel|hubungi|maklumkan|"
        r"siarkan|kepada|pejabat\s+pendidikan|"
        r"kementerian\s+pendidikan)\b",
        (text or "").casefold(),
    )
    return len(cues) >= 2


def school_cover_message(user_intent: str, filenames: list[str]) -> str:
    """Pre-execution companion that never claims planned files already exist."""
    quoted = ", ".join(f"`{name}`" for name in filenames)
    count = len(filenames)
    noun = "draft" if count == 1 else "drafts"
    if _contains_cjk(user_intent):
        return (
            f"已接收一个包含 {count} 份 Markdown 草稿的治理任务：{quoted}。"
            "系统会逐份生成、治理和验证；此时不代表文件已经成功生成。"
            "任何内容都尚未发送或发布。"
        )
    if _looks_malay(user_intent):
        return (
            f"Tugasan tadbir urus untuk {count} draf Markdown telah diterima: "
            f"{quoted}. Setiap fail akan dijana, ditadbir dan disahkan secara "
            "berasingan; mesej ini belum mendakwa bahawa penjanaan telah berjaya. "
            "Tiada kandungan telah dihantar atau diterbitkan."
        )
    return (
        f"Received a governed task for {count} Markdown {noun}: {quoted}. "
        "Each file will be generated, governed and verified separately; this "
        "message does not claim generation succeeded. Nothing was sent "
        "or published."
    )


def _school_cover_message_legacy(user_intent: str, filenames: list[str]) -> str:
    quoted = ", ".join(f"`{name}`" for name in filenames)
    count = len(filenames)
    if _contains_cjk(user_intent):
        return (
            f"我已准备 {count} 份受治理的 Markdown 草稿：{quoted}。"
            "内容只使用你提供的案例资料；缺失或未核实的信息标为 TBC。"
            "没有发送或发布任何内容，请在任何对外使用前先审核这些草稿。"
        )
    noun = "draft" if count == 1 else "drafts"
    return (
        f"I prepared {count} governed Markdown {noun}: {quoted}. "
        "The files use only the case information supplied; missing or "
        "unverified details are marked TBC. Nothing was sent or published, "
        "so please review the drafts before any external use."
    )


def _is_external_release_action(action: CandidateAction) -> bool:
    meta = action.metadata or {}
    # A named internal repository update is a governed system-level change,
    # not an external release.  Its operation may contain "publish", so this
    # explicit contract must win before the generic verb backstop below.
    if meta.get("system_level_change_action") is True:
        return False
    tool = (action.tool or "").strip().lower()
    operation = (action.operation or "").strip().lower()
    purpose = " ".join((action.purpose or "", action.expected_effect or "")).lower()
    return bool(
        meta.get("external_release_action") is True
        or tool in {"email", "publish"}
        or any(word in operation for word in ("send", "publish", "submit", "release", "message"))
        or any(phrase in purpose for phrase in (
            "send to", "send the", "publish", "submit to", "release to",
            "contact the parent", "contact parent", "notify the parent",
        ))
    )


def _requests_external_release(semantics: dict, user_intent: str = "") -> bool:
    return requests_external_release(semantics, user_intent)


def _release_audience(semantics: dict, user_intent: str) -> str:
    text = (user_intent or "").casefold()
    if re.search(r"\b(?:publish|facebook|publicly|public post)\b|公开|公開|发布|發佈", text):
        return "public"
    if re.search(
        r"\b(?:parent|guardian|family)\b|家长|家長|监护人|監護人|ibu bapa|penjaga",
        text,
    ):
        return "private_recipient"
    return str(semantics.get("audience") or "unknown").lower()


def _external_release_is_negated(user_intent: str) -> bool:
    return release_is_globally_negated(user_intent)


def _requests_public_student_sensitive_detail(user_intent: str) -> bool:
    """High-precision fail-safe when the semantic API returns no concepts."""
    text = (user_intent or "").casefold()
    public = bool(re.search(
        r"\b(?:publish|facebook|public post|post publicly|public announcement)\b|"
        r"公开|公佈|发布|發佈|脸书|臉書",
        text,
    ))
    sensitive = bool(re.search(
        r"\b(?:injur(?:y|ed|ies)|bitten|bite wound|hospitali[sz]ed|diagnosis|"
        r"medical condition|unconscious|bleeding|poison(?:ed|ing)|disciplin(?:e|ary)|"
        r"special needs?)\b|受伤|傷勢|咬伤|咬傷|送院|住院|诊断|診斷|中毒|纪律|紀律|"
        r"cedera|digigit|dimasukkan ke hospital|keracunan|disiplin",
        text,
    ))
    negated = bool(re.search(
        r"\b(?:do not|don't|never)\b[^.!?\n]{0,100}\b(?:include|mention|publish|post|share)\b"
        r"|不要[^。！？\n]{0,60}(?:提及|包括|发布|發佈|公开|公佈)",
        text,
    ))
    return public and sensitive and not negated


def _mark_external_gate(action: CandidateAction, *, audience: str) -> None:
    public = audience == "public"
    community = audience == "school_community"
    action.metadata.update({
        "school_output_contract": True,
        "school_output_contract_version": "1.0",
        "school_content_role": "external_release_gate",
        "artifact_role": "external_release_gate",
        "external_release_action": True,
        "audience": audience or "unknown",
        "output_scope": (
            "public_release" if public
            else "community_release" if community
            else "private_recipient"
        ),
        "release_state": "pending_approval",
        "approval_boundary": "human_required_before_external_release",
    })


def normalize_school_markdown_plan(
    plan: CandidatePlan,
    envelope: TaskEnvelope,
) -> dict:
    """Mutate a live school plan into a role-scoped Markdown contract."""
    semantics = (envelope.metadata or {}).get("school_semantics") or {}
    if not envelope.metadata.get("school_semantics_checked") \
            or semantics.get("school_domain") is not True:
        return {"active": False, "reason": "not_open_school_input"}

    artifacts = [a for a in plan.actions if _is_text_file_action(a)]
    if not artifacts:
        return {"active": False, "reason": "no_text_artifacts"}
    fallback_public_sensitive = _requests_public_student_sensitive_detail(
        envelope.normalized_goal
    )

    used_targets: set[str] = set()
    role_counts: dict[str, int] = {}
    for index, action in enumerate(artifacts, 1):
        role = infer_artifact_role(action)
        # A live planner may call a requested Facebook/public notice merely
        # "school_document". The closed semantic audience is stronger context
        # for a generic artifact, so fail toward the public-content boundary.
        # Explicit internal/parent roles are never overwritten in mixed plans.
        role_source = " ".join((
            str(action.target or ""), str(action.purpose or ""),
            str((action.metadata or {}).get("title") or ""),
            str((action.metadata or {}).get("audience") or ""),
        )).lower()
        if (str(semantics.get("audience") or "").lower() == "public"
                and role != "private_parent_notice"
                and (
                    len(artifacts) == 1
                    or (role == "school_document" and "internal" not in role_source)
                )):
            role = "public_communication_draft"
        role_counts[role] = role_counts.get(role, 0) + 1
        target = _target_for(action, role, envelope)
        base = target
        suffix_n = 2
        while str(target).lower() in used_targets:
            target = base.with_name(f"{base.stem}_{suffix_n}.md")
            suffix_n += 1
        used_targets.add(str(target).lower())

        meta = action.metadata or {}
        existing = str(meta.get("content") or meta.get("body") or "").strip()
        action.tool = "fs"
        action.operation = "save_under_outputs"
        action.target = str(target)
        action.metadata = meta
        meta.pop("body", None)
        if existing:
            meta["content"] = existing
        artifact_id = target.stem
        if role_counts[role] > 1:
            artifact_id = f"{artifact_id}_{role_counts[role]}"
        declared_audience = str(meta.get("audience") or "").strip().lower()
        audience = (
            declared_audience
            if meta.get("coverage_source") == "school_response_pack"
            and declared_audience in {
                "internal", "private_recipient", "school_community",
                "external_agency", "public",
            }
            else _ROLE_AUDIENCE.get(role, "internal")
        )
        source_policy = (
            str(meta.get("source_policy") or "prompt_only")
            if meta.get("coverage_source") == "school_response_pack"
            else "prompt_only"
        )
        artifact_label = (
            str(meta.get("artifact_label") or _artifact_label(action))
            if meta.get("coverage_source") == "school_response_pack"
            else _artifact_label(action)
        )
        meta.update({
            "school_output_contract": True,
            "school_output_contract_version": "1.0",
            "school_content_role": "artifact",
            "artifact_id": artifact_id,
            "artifact_role": role,
            "artifact_label": artifact_label,
            "audience": audience,
            # Response-pack policy is assigned by the Situation Compiler and
            # carries the official-source boundary for this role. Preserve it;
            # only legacy planner-authored artifacts default to prompt-only.
            "source_policy": source_policy,
            "missing_fact_policy": "TBC",
            "release_state": "draft_only",
            "output_scope": (
                "public_draft" if audience == "public"
                else "community_draft" if audience == "school_community"
                else audience
            ),
        })
        safe_public_replacement = bool(
            meta.get("coverage_source") == "school_response_pack"
            and audience in {"public", "school_community"}
            and str(meta.get("safe_transformation") or "").strip()
            and (meta.get("excluded_data_concepts") or [])
            and not (meta.get("response_pack_data_use_concepts") or [])
        )
        if safe_public_replacement:
            # The original request remains RED at the user-input layer. This
            # action is a separate, compiler-authored safe replacement, so its
            # own purpose must describe the transformed action rather than
            # retain a planner phrase such as "name Amir and his diagnosis".
            action.purpose = (
                "Prepare the compiler-approved privacy-safe public "
                "replacement draft."
            )
            action.expected_effect = (
                "Create one anonymous, non-identifying draft for human "
                "review; do not send or publish it."
            )
            meta["data_use_purpose"] = (
                "Create only the privacy-safe replacement while excluding "
                "the prohibited person-level concepts."
            )
            meta["safe_replacement_contract"] = True
        # A response pack carries an explicit per-action data-use contract.
        # Request-level hazards are shown by response_pack.input_governance;
        # copying them into every safe replacement artifact would collapse the
        # system's two governance layers back into one whole-case colour.
        if meta.get("coverage_source") == "school_response_pack":
            concept_source = meta.get("response_pack_data_use_concepts") or []
        elif "data_use_concepts" in meta:
            # An explicit per-action concept list, including an intentionally
            # empty list, is a closed action contract.  Do not replace it with
            # request-level hazards: doing so would block a safe alternative
            # merely because a sibling action was RED or INFEASIBLE.
            concept_source = meta.get("data_use_concepts") or []
        else:
            concept_source = envelope.metadata.get("data_use_concepts") or []
        task_concepts = {
            str(item).strip().lower() for item in concept_source
            if str(item).strip()
        }
        task_concepts.discard("external_release")
        task_concepts.discard("official_record_change")
        task_concepts.discard("financial_value_change")
        task_concepts.discard("persistent_sensitive_learning")
        if audience != "public":
            task_concepts.discard("public_disclosure")
        elif (
            meta.get("coverage_source") == "school_response_pack"
            and (not fallback_public_sensitive or safe_public_replacement)
        ):
            # A privacy-safe public holding draft must be judged on its own
            # body, not contaminated by sensitive concepts needed by internal
            # sibling reports. 101D still scans the generated body and the
            # real publication gate carries the release concepts separately.
            task_concepts.difference_update({
                "public_pii", "health_or_discipline",
                "student_sensitive_data", "student_sensitive_public",
            })
        elif fallback_public_sensitive and not safe_public_replacement:
            task_concepts.update({
                "health_or_discipline", "student_sensitive_data",
                "public_disclosure",
            })
        # Pre-set a step-local list so Runtime.set_default cannot copy the
        # task's eventual release intent into an internal draft action.
        meta["data_use_concepts"] = sorted(task_concepts)

        # Whole-staff access is still a broad disclosure when the file names a
        # pupil or contains discipline, welfare or family-status detail. Keep
        # the useful internal artifact, but narrow it to the authorised case
        # team and close the data contract before synthesis begins. This rule
        # intentionally reads the raw request as a fail-safe when live semantic
        # intake mislabels "all teachers" as ordinary internal use.
        if requires_restricted_staff_boundary(
            envelope.normalized_goal or envelope.raw_goal,
            role=role,
            metadata=meta,
        ):
            meta.update({
                "restricted_internal_audience": True,
                "audience": "internal",
                "audience_boundary": "limited_authorised_case_team",
                "output_scope": "internal",
                "claim_policy": (
                    "anonymous_observed_conduct_and_verified_evidence_only"
                ),
            })
            exclusions = {
                str(item).strip().lower()
                for item in (meta.get("excluded_data_concepts") or [])
                if str(item).strip()
            }
            exclusions.update({
                "person_identifier", "health_or_discipline",
                "student_sensitive_data", "socioeconomic_data",
                "differential_treatment",
            })
            meta["excluded_data_concepts"] = sorted(exclusions)
            boundary_transform = (
                "Prepare a de-identified internal artifact limited to the "
                "authorised case team. Exclude the pupil's identity, family "
                "or socioeconomic status, and any differential monitoring, "
                "labelling or sanction; use observed conduct and verified "
                "evidence only."
            )
            existing_transform = str(
                meta.get("safe_transformation") or ""
            ).strip()
            if boundary_transform not in existing_transform:
                meta["safe_transformation"] = " ".join(
                    item for item in (existing_transform, boundary_transform)
                    if item
                )

    sibling_summary = [
        {
            "action_id": a.action_id,
            "artifact_id": a.metadata.get("artifact_id"),
            "role": a.metadata.get("artifact_role"),
            "target": Path(a.target).name,
        }
        for a in artifacts
    ]
    for action in artifacts:
        action.metadata["sibling_artifacts"] = [
            item for item in sibling_summary if item["action_id"] != action.action_id
        ]

    release_chats = [
        a for a in plan.actions
        if (a.tool or "").lower() == "chat" and _is_external_release_action(a)
    ]
    chats = [
        a for a in plan.actions
        if (a.tool or "").lower() == "chat" and a not in release_chats
    ]
    if chats:
        cover = chats[0]
    else:
        cover = CandidateAction(
            tool="chat", operation="answer", target="",
            purpose="summarise the governed drafts without repeating them",
            expected_effect="show a concise delivery and governance summary",
            reversibility="high", uncertainty="low", requires_governance=True,
            metadata={},
        )
    filenames = [Path(a.target).name for a in artifacts]
    task_concept_set = {
        str(item).strip().lower()
        for item in (envelope.metadata.get("data_use_concepts") or [])
        if str(item).strip()
    }
    if fallback_public_sensitive:
        task_concept_set.update({
            "health_or_discipline", "student_sensitive_data",
            "public_disclosure",
        })
    unsafe_public_request = bool(
        str(semantics.get("audience") or "").lower() == "public"
        and task_concept_set.intersection({
            "public_pii", "health_or_discipline", "student_sensitive_data",
        })
        and "public_disclosure" in task_concept_set
    )
    cover_body = (
        "This request would place student-sensitive information into public "
        "content. Governance will evaluate the proposed public artifact before "
        "any file or external action; nothing has been created, sent, or published."
        if unsafe_public_request
        else school_cover_message(envelope.normalized_goal, filenames)
    )
    cover.metadata.update({
        "body": cover_body,
        "synthesis_skip": True,
        "school_output_contract": True,
        "school_output_contract_version": "1.0",
        "school_content_role": "chat_companion",
        "artifact_id": "chat_companion",
        "artifact_role": "chat_companion",
        "audience": "operator",
        "output_scope": "internal",
        "source_policy": "prompt_only",
        "release_state": "not_applicable",
        "artifact_count": len(artifacts),
        "artifact_filenames": filenames,
        "data_use_concepts": [],
    })

    # Exactly one conversational companion. Draft artifacts execute before
    # release gates, so the UI can inspect useful files first. A response pack
    # may legitimately contain separate guardian / hospital / agency gates;
    # they must not be collapsed into one authorisation.
    artifact_action_ids = {a.action_id for a in artifacts}
    remaining = [a for a in plan.actions if a.action_id != cover.action_id]
    release_actions = [
        a for a in remaining
        if a.action_id not in artifact_action_ids and _is_external_release_action(a)
    ]
    # Extra conversational prose is intentionally collapsed into the cover;
    # non-chat internal actions and every Markdown artifact are preserved.
    ordinary_actions = [
        a for a in remaining
        if a not in release_actions and (a.tool or "").lower() != "chat"
    ]
    negated_recipients = negated_external_recipients(
        envelope.normalized_goal
    )
    gates = [
        action for action in release_actions
        if str(
            (action.metadata or {}).get("recipient_type")
            or action.target or "external_stakeholder"
        ).strip().lower() not in negated_recipients
    ]
    release_negated = _external_release_is_negated(envelope.normalized_goal)
    governed_pack = (envelope.metadata or {}).get("school_response_pack") or {}
    if governed_pack:
        # After the one-question review, the confirmed response pack owns
        # release authority. The original ambiguous verb must not resurrect a
        # GREEN gate when the operator answered “Draft only - do not send”.
        selected_gate_ids = {
            str(item.get("deliverable_id") or "")
            for item in (governed_pack.get("deliverables") or [])
            if item.get("selected") is True
            and item.get("kind") == "external_action"
        }
        gates = [
            action for action in gates
            if str((action.metadata or {}).get("deliverable_id") or "")
            in selected_gate_ids
        ]
        requested_release = bool(selected_gate_ids)
        release_negated = not requested_release
    else:
        requested_release = _requests_external_release(
            semantics, envelope.normalized_goal) and not release_negated
    if requested_release or (gates and not release_negated):
        if not gates:
            structured_outputs = (
                ((semantics.get("situation") or {}).get("requested_outputs"))
                or []
            )
            positive_recipients = infer_explicit_external_recipients(
                envelope.normalized_goal,
                requested_audience=str(
                    semantics.get("audience") or "unknown"
                ),
                requested_outputs=structured_outputs,
            )
            external_recipients = {
                "guardian", "school_community", "medical_services",
                "malaysia_emergency_services_999", "fire_and_rescue",
                "police", "education_authority", "local_authority",
                "event_organizer", "vendor", "transport_provider",
                "public_media", "external_stakeholder",
            }
            for output in structured_outputs:
                if not isinstance(output, dict):
                    continue
                recipient = str(
                    output.get("recipient_type") or ""
                ).strip().lower()
                if recipient not in external_recipients:
                    continue
                if positive_recipients and recipient not in positive_recipients:
                    continue
                gates.append(CandidateAction(
                    tool="chat", operation="answer", target="",
                    purpose="request human approval before external release",
                    expected_effect="pause before any external send or publication",
                    reversibility="high", uncertainty="low",
                    requires_governance=True,
                    metadata={
                        "body": (
                            "The governed draft is ready. Human approval is required "
                            "before any external release; nothing was sent or published."
                        ),
                        "synthesis_skip": True,
                        "recipient_type": recipient,
                        "linked_deliverable_id": str(
                            output.get("artifact_role") or ""
                        ).strip(),
                    },
                ))
            if not gates:
                gates = [CandidateAction(
                    tool="chat", operation="answer", target="",
                    purpose="request human approval before external release",
                    expected_effect="pause before any external send or publication",
                    reversibility="high", uncertainty="low",
                    requires_governance=True,
                    metadata={
                        "body": (
                            "The governed draft is ready. Human approval is required "
                            "before any external release; nothing was sent or published."
                        ),
                        "synthesis_skip": True,
                    },
                )]
        # Legacy open tasks had no recipient model, so duplicate live-planner
        # sends are still coalesced. A structured response pack carries an
        # explicit recipient per gate and keeps each unique recipient separate.
        if not (envelope.metadata or {}).get("school_response_pack"):
            gates = gates[:1]
        unique_gates: list[CandidateAction] = []
        seen_gate_keys: set[tuple[str, str, str]] = set()
        artifact_by_deliverable = {
            str((artifact.metadata or {}).get("deliverable_id") or ""): artifact
            for artifact in artifacts
            if str((artifact.metadata or {}).get("deliverable_id") or "")
        }
        for gate in gates:
            meta = gate.metadata or {}
            audience = str(meta.get("audience") or "").strip().lower()
            if not audience:
                audience = _release_audience(semantics, envelope.normalized_goal)
            recipient = str(meta.get("recipient_type") or gate.target or "").strip().lower()
            if (envelope.metadata or {}).get("school_response_pack"):
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
                    "school_community": "school_community",
                }
                if recipient not in recipient_audience:
                    recipient = "external_stakeholder"
                audience = recipient_audience[recipient]
                linked = artifact_by_deliverable.get(
                    str(meta.get("linked_deliverable_id") or "")
                )
                if linked is not None:
                    linked_audience = str(
                        (linked.metadata or {}).get("audience") or ""
                    ).strip().lower()
                    if linked_audience == "public":
                        audience = "public"
            key = (recipient, audience, str(gate.operation or "").lower())
            if key in seen_gate_keys:
                continue
            seen_gate_keys.add(key)
            _mark_external_gate(gate, audience=audience)
            gate.metadata["recipient_type"] = recipient or "unknown"
            gate_concept_source = (
                (gate.metadata or {}).get("data_use_concepts") or ["external_release"]
                if (envelope.metadata or {}).get("school_response_pack")
                else envelope.metadata.get("data_use_concepts") or []
            )
            gate.metadata["data_use_concepts"] = sorted({
                str(item).strip().lower()
                for item in gate_concept_source
                if str(item).strip()
            } | {"external_release"} | (
                {
                    "health_or_discipline", "student_sensitive_data",
                    "public_disclosure",
                }
                if fallback_public_sensitive and audience == "public" else set()
            ) | ({"public_disclosure"} if audience == "public" else set()))
            unique_gates.append(gate)
        gates = unique_gates
        plan.actions = [cover, *ordinary_actions, *gates]
    else:
        gates = []
        plan.actions = [cover, *ordinary_actions]
    return {
        "active": True,
        "artifacts": sibling_summary,
        "cover_action_id": cover.action_id,
        "release_gate_action_id": gates[0].action_id if gates else None,
        "release_gate_action_ids": [gate.action_id for gate in gates],
    }


def _matched_positive_chunks(pattern: re.Pattern, text: str) -> list[str]:
    chunks: list[str] = []
    for match in pattern.finditer(text or ""):
        # Qualifiers apply to the matched sentence/line only.  A TBC in the
        # preceding bullet must not excuse a later unsupported positive claim.
        left_candidates = [
            text.rfind(mark, 0, match.start()) for mark in ("\n", ".", "!", "?")
        ]
        start = max(left_candidates) + 1
        right_candidates = [
            pos for pos in (
                text.find("\n", match.end()), text.find(".", match.end()),
                text.find("!", match.end()), text.find("?", match.end()),
            ) if pos >= 0
        ]
        end = min(right_candidates) + 1 if right_candidates else len(text)
        chunk = text[start:end]
        # A qualifier only negates a positive match when it appears before or
        # inside the matched assertion. A later TBC may describe a different
        # object ("information will be gathered from TBC staff") and must not
        # excuse the unsupported process claim.
        qualifier_scope = text[start:match.end()]
        if not _NEGATIVE_QUALIFIER.search(qualifier_scope):
            chunks.append(chunk.strip())
    return chunks


def _normalise_operational_literal(value: str) -> str:
    """Canonical form for exact source-evidence comparisons."""
    text = (value or "").casefold()
    text = re.sub(r"\ba\s*\.?\s*m\s*\.?\b", "am", text)
    text = re.sub(r"\bp\s*\.?\s*m\s*\.?\b", "pm", text)
    text = text.replace(".", ":")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _normalise_clock_literal(value: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", (value or "").casefold())
    compact = re.sub(r"a\.?m\.?$", "am", compact)
    compact = re.sub(r"p\.?m\.?$", "pm", compact)
    match = re.search(r"(\d{1,2})(?:[:.](\d{2}))?(am|pm)?", compact)
    if not match:
        return "", ""
    # ``7am`` and ``7:00 am`` are the same source fact.  Bare-hour source
    # notation is common in school notices, so zero-fill the minutes before
    # comparing the generated draft against its evidence.
    base = f"{int(match.group(1)):02d}:{match.group(2) or '00'}"
    return base, str(match.group(3) or "")


def _operational_detail_grounding_issues(
    content: str,
    source_goal: str,
) -> list[str]:
    """Reject unsupported operational specifics stated as settled facts.

    The user can ask for a useful plan without supplying every detail. The
    system may propose those details, but the document must show their
    epistemic status. Exact details already present in the user's source are
    allowed; new details are allowed only when the line or its enclosing
    section is visibly proposed/TBC/subject to approval.
    """
    source = source_goal or ""
    source_norm = _normalise_operational_literal(source)
    source_times = [
        _normalise_clock_literal(match.group(0))
        for match in _CLOCK_TIME.finditer(source)
    ]
    issues: list[str] = []
    proposal_section_level: int | None = None
    pattern_specs = (
        ("unsupported_operational_time", _CLOCK_TIME),
        ("unsupported_operational_deadline", _OPERATIONAL_DEADLINE),
        ("unsupported_communication_channel", _OPERATIONAL_CHANNEL),
        ("unsupported_operational_quantity", _OPERATIONAL_QUANTITY),
        ("unsupported_operational_assignment", _OPERATIONAL_ASSIGNMENT),
        ("unsupported_operational_facility", _OPERATIONAL_FACILITY),
    )

    for raw_line in (content or "").splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", raw_line).strip()
        if not line:
            continue
        heading = re.match(r"^(#{1,6})\s+", line)
        if heading:
            level = len(heading.group(1))
            if _PROPOSAL_SECTION.search(line):
                proposal_section_level = level
            elif (
                proposal_section_level is None
                or level <= proposal_section_level
            ):
                proposal_section_level = None
            continue
        if _PROPOSAL_QUALIFIER.search(line) or proposal_section_level is not None:
            continue

        for label, pattern in pattern_specs:
            for match in pattern.finditer(line):
                literal = match.group(0)
                canonical = _normalise_operational_literal(literal)
                if not canonical:
                    continue
                if label == "unsupported_operational_time":
                    output_base, output_period = _normalise_clock_literal(literal)
                    supported = any(
                        output_base == source_base
                        and (
                            not output_period
                            or not source_period
                            or output_period == source_period
                        )
                        for source_base, source_period in source_times
                    )
                else:
                    supported = canonical in source_norm
                if not supported:
                    value = re.sub(r"\s+", "_", literal.strip().casefold())
                    value = re.sub(r"[^a-z0-9_:-]+", "", value)[:48]
                    issue = f"{label}:{value or 'detail'}"
                    if issue not in issues:
                        issues.append(issue)
    return issues


def _has_official_safety_source(source_goal: str) -> bool:
    """Accept only URLs whose parsed host is an approved government domain.

    Substring checks are unsafe here: an attacker-controlled URL can put an
    official-looking domain in its path, user-info or a longer hostname. The
    hostname must be the official domain itself or one of its subdomains.
    """
    for match in _URL_CANDIDATE.finditer(source_goal or ""):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        candidate = candidate.rstrip(chr(34) + chr(39))
        try:
            host = (urlparse(candidate).hostname or "").rstrip(".").casefold()
        except ValueError:
            continue
        if any(
            host == domain or host.endswith("." + domain)
            for domain in _OFFICIAL_SAFETY_DOMAINS
        ):
            return True
    return False


def _unsourced_clinical_instruction_issues(
    action: CandidateAction,
    content: str,
    source_goal: str,
) -> list[str]:
    """Enforce an official-source ceiling for operational medical advice.

    The agent may record observed symptoms and recommend contacting a trained
    first aider or emergency service. It must not invent treatment technique,
    medication or timed clinical observation from general model knowledge.
    """
    role = str((action.metadata or {}).get("artifact_role") or "")
    safety_context = bool(
        role in _CLINICAL_CEILING_ROLES
        and re.search(
            r"\b(?:injur(?:y|ed)|ill(?:ness)?|medical|allerg|anaphyla|"
            r"bite|bitten|sting|stung|poison|bleed|unconscious|seizure)\b",
            source_goal or "",
            re.IGNORECASE,
        )
    )
    if not safety_context or _has_official_safety_source(source_goal):
        return []
    findings: list[str] = []
    for raw_line in (content or "").splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", raw_line).strip()
        match = _UNSOURCED_CLINICAL_INSTRUCTION.search(line)
        if not match:
            continue
        if _CLINICAL_INSTRUCTION_NEGATION.search(line):
            continue
        label = re.sub(r"\s+", "_", match.group(0).casefold())[:70]
        findings.append(f"unsourced_clinical_instruction:{label}")
    return list(dict.fromkeys(findings))


def validate_school_markdown(
    action: CandidateAction,
    content: str,
    source_goal: str,
) -> dict[str, list[str]]:
    """Return deterministic validation issues grouped by policy layer."""
    text = (content or "").strip()
    role = str((action.metadata or {}).get("artifact_role") or "")
    source = source_goal or ""
    issues: dict[str, list[str]] = {
        "hygiene": [], "role": [], "grounding": [], "policy": [],
    }

    if len(text) < 120:
        issues["hygiene"].append("artifact_too_short")
    if (action.metadata or {}).get("coverage_source") == "school_response_pack":
        short_roles = {
            "private_parent_notice", "school_parent_notice",
            "public_communication_draft", "education_authority_request",
            "emergency_contact_script", "fire_rescue_contact_script",
            "medical_handover_script", "external_stakeholder_message",
            "staff_internal_notice",
        }
        requested_language_set = {
            str(item).strip().lower()
            for item in ((action.metadata or {}).get("requested_languages") or [])
            if str(item).strip()
        }
        # Chinese conveys substantially more information per character than
        # space-delimited English/Malay. Applying the Latin-character floor
        # verbatim rejected a complete 251-character Chinese parent notice.
        # Bilingual drafts still satisfy the full combined floor.
        if requested_language_set == {"zh"}:
            minimum = 250 if role in short_roles else 375
        else:
            minimum = 500 if role in short_roles else 750
        if len(text) < minimum:
            issues["hygiene"].append(
                f"response_pack_artifact_too_short:{len(text)}<{minimum}"
            )
    h1 = re.findall(r"(?m)^#\s+\S.*$", text)
    if len(h1) != 1:
        issues["hygiene"].append(f"expected_one_h1_got_{len(h1)}")
    if "```" in text:
        issues["hygiene"].append("markdown_code_fence_present")
    if _BRACKET_PLACEHOLDER.search(text):
        issues["hygiene"].append("generic_bracket_placeholder_present")
    if re.search(r"\[[^\]\n]{2,120}\]", text):
        issues["hygiene"].append("square_bracket_placeholder_present")
    if Path(action.target).suffix.lower() != ".md":
        issues["hygiene"].append("artifact_not_markdown")

    polarity_source = re.sub(
        r"\b(?:no|none\s+of\s+the)\s+(?:facts?|details?|information)?\s*"
        r"(?:is|are|was|were)?\s*(?:unverified|unknown|missing|unconfirmed)"
        r"(?:\s+(?:or|and)\s+(?:unverified|unknown|missing|unconfirmed))*\b|"
        r"\ball\s+(?:facts?|details?|information)\s+(?:is|are|was|were)\s+verified\b",
        "",
        source,
        flags=re.IGNORECASE,
    )
    needs_tbc = bool(re.search(
        r"\btbc\b|\bunverified\b|not\s+(?:been\s+)?verified|not\s+confirmed|"
        r"\bunknown\b|not\s+provided|missing\s+(?:fact|detail|information)",
        polarity_source, re.IGNORECASE,
    ))
    if needs_tbc and "tbc" not in text.lower():
        issues["hygiene"].append("source_requires_tbc_but_artifact_has_none")

    parent_roles = {"private_parent_notice", "school_parent_notice"}
    if role not in parent_roles:
        formal_external_letter_roles = {
            "external_stakeholder_message",
            "education_authority_request",
            "education_authority_report",
        }
        markers = (
            _PARENT_AUDIENCE_MARKERS
            if role in formal_external_letter_roles
            else _PARENT_LETTER_MARKERS
        )
        if any(rx.search(text) for rx in markers):
            issues["role"].append("parent_letter_content_in_non_parent_artifact")
    if role in parent_roles:
        if any(rx.search(text) for rx in _INTERNAL_REPORT_MARKERS):
            issues["role"].append("internal_report_content_in_parent_notice")
    if (
        role in _EXTERNAL_DRAFT_ROLES
        and strip_internal_release_control(action, text) != text
    ):
        issues["policy"].append(
            "external_draft_contains_internal_release_control"
        )
    audience = str((action.metadata or {}).get("audience") or "").lower()
    excluded = {
        str(item).strip().lower()
        for item in ((action.metadata or {}).get("excluded_data_concepts") or [])
        if str(item).strip()
    }
    issues["policy"].extend(
        school_policy_contract_issues(action, text, source_goal)
    )
    issues["policy"].extend(
        _unsourced_clinical_instruction_issues(action, text, source_goal)
    )
    broad_privacy_contract = requires_broad_redaction(
        source,
        audience=audience,
        role=role,
        excluded_concepts=excluded,
    )
    if broad_privacy_contract:
        source_identifier_values = source_identifiers(source)
        source_mark_values = source_individual_mark_values(source)
        source_pii_values = source_direct_pii_values(source)
        source_pseudonyms = {
            match.group(1) for match in _PSEUDONYMOUS_INITIALS.finditer(source)
        }
        source_fractional_results = {
            re.sub(r"\s+", "", match.group(1))
            for match in _FRACTIONAL_RESULT.finditer(source)
        }
        source_medications = {
            match.group(1) for match in _SENSITIVE_MEDICATION.finditer(source)
        }
        source_diagnosis_literals = {
            match.group(1)
            for match in _SENSITIVE_DIAGNOSIS_LITERAL.finditer(source)
        }
        leaked_identifiers = sorted(
            item for item in source_identifier_values
            if re.search(rf"(?<!\w){re.escape(item)}(?!\w)", text, re.IGNORECASE)
        )
        leaked_pseudonyms = sorted(
            item for item in source_pseudonyms
            if item.casefold() in text.casefold()
        )
        leaked_marks = sorted(
            value for value in source_mark_values
            if re.search(rf"(?<!\d){re.escape(value)}(?!\d)", text)
        )
        compact_text = re.sub(r"\s+", "", text)
        leaked_fractional_results = sorted(
            value for value in source_fractional_results
            if value in compact_text
        )
        leaked_pii = sorted(
            value for value in source_pii_values
            if value.casefold() in text.casefold()
        )
        leaked_medications = sorted(
            value for value in source_medications
            if re.search(rf"\b{re.escape(value)}\b", text, re.IGNORECASE)
        )
        leaked_diagnosis_literals = sorted(
            value for value in source_diagnosis_literals
            if re.search(rf"\b{re.escape(value)}\b", text, re.IGNORECASE)
        )
        if leaked_identifiers or leaked_pseudonyms:
            issues["role"].append("broad_notice_contains_source_identifier")
        if leaked_marks or leaked_fractional_results:
            issues["role"].append("broad_notice_contains_individual_mark")
        if leaked_pii:
            issues["role"].append("broad_notice_contains_source_pii")
        if leaked_medications:
            issues["role"].append("broad_notice_contains_source_medication")
        if leaked_diagnosis_literals:
            issues["role"].append("broad_notice_contains_source_health_detail")

    requested_languages = {
        str(item).lower()
        for item in ((action.metadata or {}).get("requested_languages") or [])
    }
    if len(requested_languages) > 1:
        language_markers = {
            "en": r"(?mi)^#{1,3}\s+(?:english|englis(?:h)? version)\s*$",
            "ms": r"(?mi)^#{1,3}\s+(?:bahasa melayu|bahasa malaysia|malay)\s*$",
            "zh": r"(?mi)^#{1,3}\s+(?:中文|华文|chinese)\s*$",
        }
        for language in sorted(requested_languages):
            marker = language_markers.get(language)
            if marker and not re.search(marker, text):
                issues["hygiene"].append(f"missing_language_section:{language}")
    if (role == "internal_incident_report"
            and re.search(
                r"(?mi)^(?:#{1,6}\s+recommendations?\s*|"
                r"\*\*recommendations?:\*\*\s*)$",
                text,
            )
            and not re.search(
                r"\brecommend|\bsuggest|\bpropose|what\s+(?:should|can)\s+we\s+do|"
                r"action\s+plan|next\s+steps\s+beyond\s+verification",
                source_goal or "", re.IGNORECASE,
            )):
        issues["role"].append("unrequested_recommendations_section")

    for label, output_rx, source_rx in _GROUNDING_RULES:
        output_claims = _matched_positive_chunks(output_rx, text)
        if not output_claims:
            continue
        source_support = _matched_positive_chunks(source_rx, source)
        if not source_support:
            issues["grounding"].append(label)

    temporal_terms = (
        (r"\btoday\b", r"\btoday\b"),
        (r"\bearlier today\b", r"\bearlier today\b"),
        (r"\bhari ini\b", r"\bhari ini\b"),
        (r"今天", r"今天"),
    )
    metadata = action.metadata or {}
    delivery_script = bool(re.search(
        r"\b(?:speech|spoken\s+remarks?|address|emcee\s+script)\b|"
        r"\b(?:ucapan|teks\s+ucapan|skrip\s+pengacara)\b|"
        r"(?:演讲稿|演講稿|致辞|致辭|讲话稿|講話稿)",
        " ".join((
            str(action.purpose or ""),
            str(metadata.get("requested_label") or ""),
            str(metadata.get("purpose") or ""),
        )),
        re.IGNORECASE,
    ))
    if not delivery_script:
        for output_term, source_term in temporal_terms:
            if re.search(output_term, text, re.IGNORECASE) and not re.search(
                source_term, source, re.IGNORECASE
            ):
                issues["grounding"].append("unsupported_relative_date")
                break
    if re.search(
        r"\b(?:we|the school) will (?:continue to )?monitor\b|"
        r"\bmonitoring will continue\b",
        text,
        re.IGNORECASE,
    ) and not re.search(r"\bmonitor\b", source, re.IGNORECASE):
        issues["grounding"].append("unsupported_monitoring_commitment")

    for field in ("location", "venue", "date of incident", "time of incident"):
        match = re.search(
            rf"(?mi)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(field)}(?:\*\*)?\s*:\s*([^\n]+)$",
            text,
        )
        if not match:
            continue
        value = re.sub(r"[*_`]", "", match.group(1)).strip()
        if re.search(r"\btbc\b|unknown|not provided|to be confirmed", value, re.IGNORECASE):
            continue
        value_tokens = {
            token for token in re.findall(r"[a-z0-9]+", value.casefold())
            if token not in {
                "the", "a", "an", "school",
                # Epistemic labels qualify the supplied location; they are
                # not extra location facts and must not create a false
                # unsupported-location failure.
                "reported", "confirmed", "unconfirmed", "approximate",
                "approximately",
            }
        }
        source_tokens = set(re.findall(r"[a-z0-9]+", source.casefold()))
        if value_tokens and not value_tokens.issubset(source_tokens):
            issues["grounding"].append(f"unsupported_{field.replace(' ', '_')}")

    if re.search(
        r"\b(?:promptly|immediately)\s+(?:administered|provided|contacted|called|arrived)\b",
        text,
        re.IGNORECASE,
    ) and not re.search(r"\b(?:promptly|immediately)\b", source, re.IGNORECASE):
        issues["grounding"].append("unsupported_response_timing_adverb")

    issues["grounding"].extend(
        _operational_detail_grounding_issues(text, source)
    )

    return issues


def artifact_similarity(a: str, b: str) -> float:
    def normalise(text: str) -> str:
        text = re.sub(r"(?m)^#{1,6}\s+.*$", " ", text or "")
        # Shared case facts and mandatory governance boilerplate are expected
        # to repeat across a response pack.  They are not evidence that an
        # incident report, a site checklist and a contact script are duplicate
        # artifacts.  Remove those bounded wrapper lines before comparing the
        # role-specific substance.  Exact or near-exact duplicated bodies
        # still normalise to the same text and therefore remain a hard fail.
        text = re.sub(r"(?m)^>.*$", " ", text)
        text = re.sub(
            r"(?mi)^(?:ringkasan\s+perkara|事项摘要|事項摘要|"
            r"user-reported\s+case|reported\s+(?:matter|event|case|request)|"
            r"case\s+context)\s*:\s*.*$",
            " ", text,
        )
        text = re.sub(
            r"(?mi)^-\s*(?:maklumat\s+kes\s+yang\s+disahkan\s+untuk\s+draf\s+ini|"
            r"本草稿可用的已核实资料|本草稿可用的已核實資料)\s*:\s*TBC\s*$",
            " ", text,
        )
        text = re.sub(
            r"Dokumen ini masih berstatus DRAF - BELUM DIHANTAR\..*?"
            r"Tiada mesej telah dihantar atau diterbitkan oleh sistem ini\.",
            " ", text, flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"本文件是尚未发送或发布的草稿。.*?系统没有发送或发布任何内容。|"
            r"本文件是尚未發送或發布的草稿。.*?系統沒有發送或發布任何內容。",
            " ", text, flags=re.DOTALL,
        )
        text = re.sub(r"(?i)\b(?:draft|not sent|tbc|internal|private|review)\b", " ", text)
        return re.sub(r"\s+", " ", text).strip().lower()
    left, right = normalise(a), normalise(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _execution_by_action(executions: Iterable[ExecutionResult]) -> dict[str, ExecutionResult]:
    out: dict[str, ExecutionResult] = {}
    for execution in executions:
        if execution.action_id not in out or execution.status == "success":
            out[execution.action_id] = execution
    return out


def school_artifact_verification_checks(
    envelope: TaskEnvelope,
    plan_actions: list[CandidateAction],
    executions: list[ExecutionResult],
) -> list[dict]:
    """Build strict per-artifact Module 110 checks for open school inputs."""
    artifacts = [
        a for a in plan_actions
        if (a.metadata or {}).get("school_output_contract")
        and (a.metadata or {}).get("school_content_role") == "artifact"
    ]
    if not artifacts:
        return []

    exec_by_id = _execution_by_action(executions)
    contents: dict[str, str] = {}
    completeness_errors: list[str] = []
    contract_errors: list[str] = []
    artifact_ids: set[str] = set()
    targets: set[str] = set()

    for action in artifacts:
        meta = action.metadata or {}
        aid = str(meta.get("artifact_id") or "")
        target = str(action.target or "")
        if not aid or aid in artifact_ids:
            contract_errors.append(f"duplicate_or_missing_artifact_id:{aid or action.action_id}")
        artifact_ids.add(aid)
        if not meta.get("artifact_role") or not meta.get("audience"):
            contract_errors.append(f"missing_role_or_audience:{action.action_id}")
        if meta.get("school_generation_failed"):
            contract_errors.append(f"generation_not_verified:{action.action_id}")
        if (action.tool or "").lower() != "fs" or Path(target).suffix.lower() != ".md":
            contract_errors.append(f"not_fs_markdown:{action.action_id}")
        if target.lower() in targets:
            contract_errors.append(f"duplicate_target:{Path(target).name}")
        targets.add(target.lower())

        execution = exec_by_id.get(action.action_id)
        if execution is None or execution.status != "success":
            completeness_errors.append(f"not_successful:{action.action_id}")
        affected = [Path(p) for p in ((execution.affected_resources if execution else []) or [])]
        matching = [p for p in affected if p.suffix.lower() == ".md"]
        if not matching:
            completeness_errors.append(f"no_markdown_file:{action.action_id}")
        else:
            path = matching[0]
            try:
                disk = path.read_text(encoding="utf-8")
            except Exception:
                disk = ""
            if len(disk.strip()) < 64:
                completeness_errors.append(f"empty_or_unreadable:{path.name}")
            contents[action.action_id] = disk or str(meta.get("content") or "")

    checks: list[dict] = [
        {
            "name": "school.execution_completeness",
            "pass": not completeness_errors,
            "reason": "ok" if not completeness_errors else completeness_errors[0],
            "details": {"errors": completeness_errors, "expected": len(artifacts)},
        },
        {
            "name": "school.artifact_contract",
            "pass": not contract_errors,
            "reason": "ok" if not contract_errors else contract_errors[0],
            "details": {"errors": contract_errors},
        },
    ]

    chats = [
        a for a in plan_actions
        if (a.metadata or {}).get("school_output_contract")
        and (a.metadata or {}).get("school_content_role") == "chat_companion"
    ]
    chat_errors: list[str] = []
    if len(chats) != 1:
        chat_errors.append(f"expected_one_chat_companion_got_{len(chats)}")
    else:
        chat = chats[0]
        execution = exec_by_id.get(chat.action_id)
        body = str(
            (execution.output_summary if execution and execution.status == "success" else "")
            or (chat.metadata or {}).get("body") or ""
        ).strip()
        if not body:
            chat_errors.append("chat_companion_empty")
        if len(body) > 700:
            chat_errors.append(f"chat_companion_too_long:{len(body)}")
        if re.search(
            r"^#{1,6}\s|^dear\s|^sincerely,?\s*$",
            body, flags=re.MULTILINE | re.IGNORECASE,
        ):
            chat_errors.append("chat_contains_artifact_body")
    checks.append({
        "name": "school.chat_companion_scope",
        "pass": not chat_errors,
        "reason": "ok" if not chat_errors else chat_errors[0],
        "details": {"errors": chat_errors},
    })

    hygiene: dict[str, list[str]] = {}
    roles: dict[str, list[str]] = {}
    grounding: dict[str, list[str]] = {}
    policy: dict[str, list[str]] = {}
    review_notes: dict[str, list[str]] = {}
    for action in artifacts:
        issues = validate_school_markdown(
            action, contents.get(action.action_id, str(action.metadata.get("content") or "")),
            envelope.normalized_goal or envelope.raw_goal,
        )
        accepted_notes = {
            str(item)
            for item in (
                (action.metadata or {}).get("school_generation_review_notes")
                or []
            )
        }
        # Review notes are never allowed to suppress policy/privacy findings.
        # At present only the synthesizer's cross-artifact similarity warning
        # can enter this set; grouped per-file guard failures remain blocking.
        if accepted_notes:
            review_notes[action.action_id] = sorted(accepted_notes)
        if issues["hygiene"]:
            hygiene[action.action_id] = issues["hygiene"]
        if issues["role"]:
            roles[action.action_id] = issues["role"]
        if issues["grounding"]:
            grounding[action.action_id] = issues["grounding"]
        if issues["policy"]:
            policy[action.action_id] = issues["policy"]

    checks.extend([
        {
            "name": "school.markdown_hygiene",
            "pass": not hygiene,
            "reason": "ok" if not hygiene else next(iter(hygiene.values()))[0],
            "details": {"issues_by_action": hygiene},
        },
        {
            "name": "school.role_isolation",
            "pass": not roles,
            "reason": "ok" if not roles else next(iter(roles.values()))[0],
            "details": {"issues_by_action": roles},
        },
        {
            "name": "school.fact_grounding",
            "pass": not grounding,
            "reason": "ok" if not grounding else next(iter(grounding.values()))[0],
            "details": {"issues_by_action": grounding},
        },
        {
            "name": "school.policy_contract",
            "pass": not policy,
            "reason": "ok" if not policy else next(iter(policy.values()))[0],
            "details": {"issues_by_action": policy},
        },
        {
            "name": "school.human_review_notes",
            "pass": True,
            "reason": "review_noted" if review_notes else "ok",
            "details": {"notes_by_action": review_notes},
        },
    ])

    similarity_errors: list[dict] = []
    for i, left in enumerate(artifacts):
        for right in artifacts[i + 1:]:
            score = artifact_similarity(
                contents.get(left.action_id, ""), contents.get(right.action_id, ""))
            if score >= 0.72:
                marker = (
                    f"cross_artifact_similarity:{left.action_id}:"
                    f"{right.action_id}:{score:.3f}"
                )
                left_notes = set(
                    (left.metadata or {}).get("school_generation_review_notes")
                    or []
                )
                right_notes = set(
                    (right.metadata or {}).get("school_generation_review_notes")
                    or []
                )
                if marker in left_notes and marker in right_notes:
                    continue
                similarity_errors.append({
                    "left": left.action_id, "right": right.action_id,
                    "ratio": round(score, 3),
                })
    checks.append({
        "name": "school.cross_artifact_similarity",
        "pass": not similarity_errors,
        "reason": "ok" if not similarity_errors else "artifacts_too_similar",
        "details": {"pairs": similarity_errors, "threshold": 0.72},
    })
    return checks


def is_school_output_contract(plan_actions: list[CandidateAction]) -> bool:
    return any((a.metadata or {}).get("school_output_contract") for a in plan_actions)
