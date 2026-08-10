"""School-domain semantic preflight for Mixed-Live competition follow-ups.

The LLM is an interpreter, never the policy authority.  It maps unlimited
natural-language phrasings onto a small, closed school-administration schema.
The deterministic 101D/103 governance layers consume the resulting concepts
and remain the only components allowed to choose BLUE/GREEN/RED/INFEASIBLE.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .module_school_intent_contract import declared_output_cardinality


_RELATIONS = {"follow_up", "new_case", "unrelated", "ambiguous"}
_AREAS = {
    "student_support", "discipline", "attendance", "health",
    "parent_communication", "school_event", "fundraising_finance",
    "public_communication", "official_records", "general_admin", "other",
}
_CONCEPT_KEYS = (
    "socioeconomic_data", "differential_treatment", "public_pii",
    "health_or_discipline", "student_sensitive_data", "public_disclosure",
    "persistent_sensitive_learning", "external_release",
    "official_record_change", "financial_value_change",
    "unsupported_fact_invention",
)

_SITUATION_FAMILIES = {
    "safety_emergency", "health_medical", "safeguarding_welfare",
    "discipline_behaviour", "attendance_student_movement",
    "facilities_environment", "transport_travel", "food_hygiene",
    "cyber_data", "finance_procurement", "events_cocurricular",
    "teaching_learning_support", "staffing_hr",
    "communications_reputation", "records_regulatory",
    "community_external_party", "general_school_admin",
}
_SITUATION_PHASES = {
    "ongoing", "just_occurred", "post_incident", "planned", "follow_up", "unknown",
}
_SITUATION_SEVERITIES = {"critical", "high", "medium", "low", "unknown"}
_SITUATION_SIGNALS = {
    "active_danger", "injury_or_illness", "minor_involved", "person_missing",
    "safeguarding_concern", "external_help_may_be_required",
    "possible_regulatory_trigger", "public_interest", "personal_data_involved",
    "financial_value_involved", "official_record_involved", "service_disruption",
    "evidence_preservation_needed", "transport_operation", "food_water_exposure",
    "event_operation", "evacuation_accountability", "data_security_incident",
    "guardian_notification_relevant",
}
_SITUATION_STAKEHOLDERS = {
    "school_leadership", "school_staff", "student", "guardian",
    "medical_services", "malaysia_emergency_services_999", "fire_and_rescue",
    "police", "education_authority", "local_authority", "event_organizer",
    "vendor", "transport_provider", "public_media",
    "school_community",
}

_OUTPUT_ROLES = {
    "internal_incident_report", "private_parent_notice",
    "school_parent_notice", "emergency_contact_script",
    "fire_rescue_contact_script", "medical_handover_script",
    "site_safety_checklist", "student_accountability_checklist",
    "regulatory_notification_assessment", "education_authority_report",
    "education_authority_request", "staff_internal_notice",
    "public_communication_draft", "discipline_investigation_report",
    "safeguarding_action_plan", "evidence_preservation_log",
    "cyber_incident_response", "finance_procurement_memo",
    "event_action_plan", "external_stakeholder_message",
    "student_support_plan", "transport_response_plan",
    "food_safety_response", "post_incident_review",
    "evidence_status_report", "measurement_plan", "school_document",
}
_OUTPUT_AUDIENCES = {
    "internal", "private_recipient", "school_community",
    "external_agency", "public",
}
_OUTPUT_LANGUAGES = {"en", "ms", "zh"}

_OUTPUT_ROLE_DEFAULT_AUDIENCE = {
    "private_parent_notice": "private_recipient",
    "school_parent_notice": "school_community",
    "public_communication_draft": "public",
    "education_authority_report": "external_agency",
    "education_authority_request": "external_agency",
    "emergency_contact_script": "external_agency",
    "fire_rescue_contact_script": "external_agency",
    "medical_handover_script": "external_agency",
    "external_stakeholder_message": "external_agency",
}
_OUTPUT_ROLE_DEFAULT_RECIPIENT = {
    "private_parent_notice": "guardian",
    "school_parent_notice": "school_community",
    "public_communication_draft": "public_media",
    "education_authority_report": "education_authority",
    "education_authority_request": "education_authority",
    "emergency_contact_script": "malaysia_emergency_services_999",
    "fire_rescue_contact_script": "fire_and_rescue",
    "medical_handover_script": "medical_services",
    "external_stakeholder_message": "external_stakeholder",
}

# The LLM interprets free language, but an active case is never evidence by
# itself that a new, self-contained request belongs to the school pack.  These
# anchors are deliberately closed and multilingual; they are used only to
# prevent context capture, never to choose a governance route.
_SCHOOL_ANCHORS = (
    "school", "student", "students", "pupil", "pupils", "learner",
    "learners", "teacher", "teachers", "educator", "educators",
    "faculty", "teaching staff", "parent", "parents", "guardian", "guardians",
    "classroom", "campus", "attendance", "discipline", "disciplinary",
    "school event", "school notice", "school report", "official record",
    "bazaar", "fundraiser", "fundraising", "coupon", "speech competition",
    "sekolah", "murid", "pelajar", "guru", "ibu bapa", "penjaga",
    "canteen", "classroom", "laboratory", "library", "assembly",
    "school bus", "hostel", "dormitory", "principal", "headteacher",
    "recess", "playground", "school hall", "form teacher", "homeroom",
    "prefect", "exam paper", "class average", "co-curricular", "cocurricular",
    "timetable", "class schedule", "school calendar", "meeting minutes",
    "staff meeting", "pibg", "pta", "enrolment", "enrollment",
    "promotion list", "class promotion", "ppd", "jpn",
    "district education office", "education authority", "education office",
    "ministry of education",
    "kantin", "kelas", "makmal", "perhimpunan", "bas sekolah", "asrama",
    "waktu rehat", "padang sekolah", "guru kelas", "pengawas", "kertas peperiksaan",
    "disiplin", "kehadiran", "jualan amal", "kupon",
    "pejabat pendidikan", "pejabat pendidikan daerah",
    "kementerian pendidikan",
    "jadual waktu", "minit mesyuarat", "pendaftaran murid", "senarai kenaikan kelas",
    "学校", "学生", "老师", "教师", "家长", "监护人", "纪律", "出勤",
    "违规", "校园", "校内", "义卖", "固本", "餐券", "演讲", "校方",
    "课程表", "課程表", "时间表", "時間表", "会议记录", "會議記錄",
    "家协", "家協", "入学", "入學", "升班名单", "升班名單",
)
_FOLLOWUP_ANCHORS = (
    "it", "this", "that", "these", "those", "them", "still", "again",
    "continue", "remaining", "same", "next", "make it", "send it",
    "the report", "this report", "that report", "same report",
    "the notice", "this notice", "the post", "this post", "the draft",
    "this draft", "the plan", "this plan", "add to", "remove from",
    "revise it", "update it",
    "ini", "itu", "mereka", "masih", "sambung", "teruskan", "lagi",
    "baki", "sama", "ubah", "tambah", "buang", "hantar",
    "这个", "那个", "它", "他们", "还是", "仍然", "依然", "继续",
    "接着", "剩下", "同样", "修改", "加上", "删除", "发给", "不够",
)


def _has_anchor(text: str, anchors: tuple[str, ...]) -> bool:
    value = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    for anchor in anchors:
        token = anchor.casefold()
        if re.search(r"[\u3400-\u9fff]", token):
            if token in value:
                return True
        elif re.search(rf"(?<!\w){re.escape(token)}(?!\w)", value):
            return True
    return False


def _self_contained_external_content_request(text: str) -> bool:
    """Recognise a bounded non-school content task despite a school wrapper.

    A phrase such as ``for a school media lesson`` explains why the user wants
    an article; it does not turn a FIFA tournament report into school
    administration.  This narrow predicate protects the domain boundary
    without rejecting genuine school sports notices, event plans or reports.
    """
    value = re.sub(r"\s+", " ", str(text or "").casefold()).strip()
    authoring = r"(?:write|draft|create|prepare|produce)"
    content = r"(?:report|article|essay|summary|presentation)"
    world_cup = r"(?:(?:fifa\s+)?world\s+cup)"
    world_cup_report = bool(re.search(
        rf"\b{authoring}\b[^.!?\n]{{0,100}}\b{world_cup}\b"
        rf"[^.!?\n]{{0,65}}\b{content}\b|"
        rf"\b{authoring}\b[^.!?\n]{{0,100}}\b{content}\b"
        rf"[^.!?\n]{{0,65}}\b(?:about|on|covering)\b"
        rf"[^.!?\n]{{0,35}}\b{world_cup}\b|"
        rf"\b{authoring}\b[^.!?\n]{{0,100}}\b{world_cup}\b"
        rf"[^.!?\n]{{0,35}}\b(?:tournament\s+)?report\b",
        value,
    ))
    world_cup_fixture = bool(re.search(
        rf"\b{world_cup}\b[^.!?\n]{{0,80}}\b"
        r"(?:match(?:es)?|fixture(?:s)?|schedule|score(?:s)?|result(?:s)?)\b|"
        r"\b(?:next|upcoming)\b[^.!?\n]{0,80}\b"
        rf"{world_cup}\b",
        value,
    ))
    recipe = bool(re.search(
        r"\b(?:write|draft|create|make|give|show|find|suggest|prepare)\b"
        r"[^.!?\n]{0,80}\b(?:a\s+)?recipe\b|"
        r"\brecipe\s+for\b",
        value,
    )) and not _has_anchor(text, _SCHOOL_ANCHORS)
    return world_cup_report or world_cup_fixture or recipe


class SchoolInputSemantics:
    """One-call semantic interpreter with a closed, validation-heavy output."""

    def __init__(self, chat_llm) -> None:
        self.chat_llm = chat_llm

    def classify(self, text: str, *, active_workflow_id: str | None = None) -> dict:
        if self.chat_llm is None or getattr(self.chat_llm, "backend", "mock") == "mock":
            return self._fallback("llm_unavailable", text, active_workflow_id)

        active = active_workflow_id or "none"
        system = (
            "You are the semantic intake layer for a Malaysian public-school "
            "administration agent. Interpret meaning, including paraphrases. "
            "You DO NOT decide whether an action is allowed and you MUST NOT "
            "output BLUE, GREEN, RED, or INFEASIBLE. An active workflow is "
            "context, not proof of domain membership: a self-contained request "
            "about sport, travel, recipes, finance, coding, entertainment, or "
            "another non-school topic is unrelated unless it explicitly asks "
            "for school administration. Return one JSON object only."
        )
        user = f"""Active workflow: {active}
User input: {text}

Return these fields:
- school_domain: boolean; true for school administration, pupils, parents,
  discipline, attendance, school events, fundraising, official records,
  school communications, or a follow-up to the active school workflow.
- case_relation: follow_up | new_case | unrelated | ambiguous. When an active
  workflow exists, pronouns and references such as the pupils/students,
  parents, winners, post, report, bazaar, coupons, stock, or "still" normally
  refer to that case: use follow_up. Use new_case only when the input clearly
  introduces a different school matter.
- school_area: student_support | discipline | attendance | health |
  parent_communication | school_event | fundraising_finance |
  public_communication | official_records | general_admin | other.
- requested_action: short verb phrase such as advise, draft, investigate,
  publish, send, persist, update_record, change_value, or clarify.
- audience: internal | private_recipient | school_community | public | unknown.
  Use school_community for a broad but non-public school audience such as all
  parents, all staff, a class group, or the school community.
- confidence: number from 0 to 1.
- rationale: one short sentence.
- socioeconomic_data: uses income, wealth, occupation, donor capacity, or
  social standing.
- differential_treatment: ranks, prioritises, pressures, rewards, or treats
  people differently based on personal/status data.
- public_pii: exposes identifiers/contact details publicly.
- health_or_discipline: uses health, discipline, special-needs, behaviour, or
  protected disciplinary information.
- student_sensitive_data: uses a named pupil's learning difficulty, weakness,
  support need, behaviour, or private progress.
- public_disclosure: places person-level information into a public post,
  announcement, website, social channel, or broad audience.
- persistent_sensitive_learning: asks the system to remember, profile, train
  on, or reuse person-level sensitive information in future cases.
- external_release: sends, publishes, submits, messages, or releases content
  outside the internal draft workspace.
- official_record_change: writes or changes an official student, attendance,
  discipline, finance, or school record.
- financial_value_change: issues/reprints cash-equivalent coupons or changes
  an authorised price/payment arrangement.
- unsupported_fact_invention: true ONLY when the user asks to guess, fabricate,
  make up, or fill in an unverified fact as though it were known. It MUST be
  false when the user asks to mark unknown facts TBC/to-be-confirmed, leave them
  blank, clarify them, investigate them, or draft only from available evidence.

Also return a `situation` object. It proposes coverage only and MUST NOT contain
governance colours or approval decisions:
- family: safety_emergency | health_medical | safeguarding_welfare |
  discipline_behaviour | attendance_student_movement |
  facilities_environment | transport_travel | food_hygiene | cyber_data |
  finance_procurement | events_cocurricular | teaching_learning_support |
  staffing_hr | communications_reputation | records_regulatory |
  community_external_party | general_school_admin.
- secondary_families: zero or more additional family values when one event
  genuinely spans domains (for example transport plus health, or event plus
  communications). Do not repeat family.
- phase: ongoing | just_occurred | post_incident | planned | follow_up | unknown.
- severity: critical | high | medium | low | unknown.
- signals: zero or more of active_danger, injury_or_illness, minor_involved,
  person_missing, safeguarding_concern, external_help_may_be_required,
  possible_regulatory_trigger, public_interest, personal_data_involved,
  financial_value_involved, official_record_involved, service_disruption,
  evidence_preservation_needed, transport_operation, food_water_exposure,
  event_operation, evacuation_accountability, data_security_incident,
  guardian_notification_relevant. Use these cross-domain signals even when the
  primary family is different; they drive additive response-pack coverage.
- affected_people_types: student | staff | guardian | visitor | unknown.
- stakeholder_candidates: school_leadership, school_staff, student, guardian,
  medical_services, malaysia_emergency_services_999, fire_and_rescue, police,
  education_authority, local_authority, event_organizer, vendor,
  transport_provider, public_media.
- case_summary: one neutral sentence using only the user's reported facts.
- known_facts: array of {{fact_id,value,status}}; status is reported, confirmed,
  unverified, or unknown. Never upgrade a reported fact to confirmed.
- unknowns: array of {{fact_id,impact}}; impact is life_safety,
  governance_boundary, required_deliverables, external_recipient, or
  content_only. Names, dates and wording-only gaps are content_only.
- requested_deliverables: canonical artifact roles only when explicit.
- requested_outputs: one object for EACH output the user explicitly asks for.
  Do not add merely useful extras. Never combine several requested files into
  one object. If three unfamiliar files all map to school_document, return
  three separate school_document objects with distinct labels and purposes.
  Each object has artifact_role, label, explicit,
  purpose, audience, recipient_type, languages and source_fact_ids.
  explicit must be true. If an output is merely recommended rather than
  explicitly requested, omit it from requested_outputs entirely.
  artifact_role must be one of internal_incident_report,
  private_parent_notice, school_parent_notice, emergency_contact_script,
  fire_rescue_contact_script, medical_handover_script,
  site_safety_checklist, student_accountability_checklist,
  regulatory_notification_assessment, education_authority_report,
  education_authority_request, staff_internal_notice,
  public_communication_draft, discipline_investigation_report,
  safeguarding_action_plan, evidence_preservation_log,
  cyber_incident_response, finance_procurement_memo, event_action_plan,
  external_stakeholder_message, student_support_plan,
  transport_response_plan, food_safety_response, post_incident_review,
  evidence_status_report, measurement_plan, or school_document.
  Use school_parent_notice for a circular, WhatsApp-group message or notice to
  all parents; private_parent_notice is only for one pupil's own guardian.
  Use education_authority_request for a request, invitation or support message
  to an education office; education_authority_report is for a formal case
  report. Use school_document only when no specialised role fits.
  languages is a subset of en, ms, zh and must preserve explicit requests such
  as bilingual English and Malay. source_fact_ids names the known_facts that
  the output is expected to use.
- explicit_external_actions: stakeholder types only when the user explicitly
  asks to contact, send, publish or submit. Preparing a draft is not an
  external action.

All safety concept fields are booleans. If uncertain, keep the concept false
and lower confidence; use case_relation=ambiguous where appropriate."""
        try:
            raw = self._request_json(system, user, max_tokens=1800)
        except Exception:
            return self._fallback("llm_error", text, active_workflow_id)
        if not isinstance(raw, dict) or not raw:
            return self._fallback("empty_or_unparseable", text, active_workflow_id)
        normalised = self._normalise(raw)
        bounded = self._apply_domain_boundary(
            normalised, text=text, active_workflow_id=active_workflow_id)
        return self._enforce_declared_output_cardinality(bounded, text=text)

    def _request_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
    ) -> dict:
        """Use the strongest JSON mode exposed by any configured provider."""
        # OpenAI has a native strict JSON response mode. The generic
        # chat_json() path asks for JSON in prose but some models may emit
        # fenced JavaScript-like objects with unquoted enum values (the
        # meaning is correct, yet json.loads must reject it). Keep this choice
        # behind one provider-neutral method so cardinality repair follows the
        # same transport as initial classification.
        if (
            getattr(self.chat_llm, "backend", "") == "openai"
            and hasattr(self.chat_llm, "chat_json_openai")
        ):
            return self.chat_llm.chat_json_openai(
                system, user, max_tokens=max_tokens
            )
        return self.chat_llm.chat_json(system, user, max_tokens=max_tokens)

    def _enforce_declared_output_cardinality(
        self,
        result: dict,
        *,
        text: str,
    ) -> dict:
        """Repair a collapsed explicit file list once, then fail visibly.

        Natural-language interpretation is still delegated to the model.  A
        deterministic first-party count only checks the structural invariant:
        an explicit request for N separate outputs must yield at least N
        requested_output objects.  The extra call happens only when that
        invariant is violated.  If the provider cannot repair it, downstream
        intent coverage remains incomplete rather than incorrectly VERIFIED.
        """
        if result.get("school_domain") is not True:
            return result
        declared_count = declared_output_cardinality(text)
        if declared_count == 0:
            return result

        guarded = dict(result)
        situation = dict(guarded.get("situation") or {})
        outputs = [
            dict(item)
            for item in (situation.get("requested_outputs") or [])
            if isinstance(item, dict)
        ]
        initial_count = len(outputs)
        repair_attempted = initial_count < declared_count
        repair_improved = False
        if repair_attempted:
            repair_system = (
                "You repair only the explicit requested-output list for a "
                "school-administration semantic contract. Do not decide "
                "governance, add optional work, merge files, or write the "
                "files. Return one JSON object only."
            )
            repair_user = f"""User request: {text}
Deterministic source-text floor: at least {declared_count} separate outputs.
Current requested_outputs: {json.dumps(outputs, ensure_ascii=False)}

Return {{"requested_outputs": [...]}} with one object for every explicitly
requested output and at least {declared_count} distinct objects. Preserve
distinct labels and purposes. Use artifact_role="school_document" when no
specialised role fits. Each object must contain artifact_role, label, purpose,
audience, recipient_type, languages, and source_fact_ids. Add no merely useful
extras."""
            try:
                repaired_raw = self._request_json(
                    repair_system, repair_user, max_tokens=1400
                )
            except Exception:
                repaired_raw = {}
            if isinstance(repaired_raw, dict):
                repair_container = repaired_raw.get("situation")
                if not isinstance(repair_container, dict):
                    repair_container = repaired_raw
                repaired = self._normalise_situation(
                    {"requested_outputs": repair_container.get("requested_outputs")}
                ).get("requested_outputs") or []
                if len(repaired) > len(outputs):
                    outputs = [dict(item) for item in repaired]
                    repair_improved = True

        # A source that explicitly asks for one file also owns an upper bound.
        # This prevents a semantic provider from turning one Malay ``satu surat
        # makluman`` into both a community notice and a private-family letter.
        # Only the declared single-output case is collapsed; multi-file floors
        # continue to preserve every semantic slot.
        if declared_count == 1 and len(outputs) > 1:
            source = text.casefold()
            broad_parent = bool(re.search(
                r"\b(?:all\s+parents?|all\s+guardians?|school\s+community|"
                r"semua\s+(?:ibu\s+bapa|penjaga)|seluruh\s+(?:ibu\s+bapa|penjaga))\b",
                source,
            ))
            private_parent = bool(re.search(
                r"\b(?:the|his|her|their)\s+(?:parent|guardian)|"
                r"(?:ibu\s+bapa|penjaga)\s+(?:murid|pelajar)\s+(?:itu|tersebut)\b",
                source,
            )) and not broad_parent

            def single_output_score(item: dict) -> tuple[int, int]:
                role = str(item.get("artifact_role") or "").casefold()
                audience = str(item.get("audience") or "").casefold()
                recipient = str(item.get("recipient_type") or "").casefold()
                score = 0
                if broad_parent and (
                    role == "school_parent_notice"
                    or audience == "school_community"
                    or recipient == "school_community"
                ):
                    score += 20
                if private_parent and (
                    role == "private_parent_notice"
                    or audience == "private_recipient"
                    or recipient == "guardian"
                ):
                    score += 20
                # Prefer a label literally grounded in the request over a
                # provider-added companion, while retaining stable first-item
                # order as the final tie-breaker.
                label = str(item.get("label") or "").casefold().strip()
                if label and label in source:
                    score += 5
                return score, -outputs.index(item)

            outputs = [max(outputs, key=single_output_score)]

        complete = len(outputs) >= declared_count
        for index, output in enumerate(outputs):
            if index < declared_count:
                output["declared_slot_explicit"] = True
        situation["requested_outputs"] = outputs
        situation["declared_output_count"] = declared_count
        situation["observed_output_count"] = len(outputs)
        situation["output_contract_complete"] = complete
        situation["output_contract_status"] = (
            "complete_after_repair" if repair_improved and complete
            else "complete" if complete
            else "incomplete_after_repair" if repair_attempted
            else "incomplete"
        )
        guarded["situation"] = situation
        if repair_improved:
            guarded["source"] = (
                str(guarded.get("source") or "school_semantic_llm")
                + "+cardinality_repair"
            )
        return guarded

    @staticmethod
    def _apply_domain_boundary(
        result: dict,
        *,
        text: str,
        active_workflow_id: str | None,
    ) -> dict:
        """Stop an active school case from capturing an unrelated prompt.

        A bare noun such as ``report`` is intentionally not a follow-up anchor:
        "write a World Cup report" is a complete new request, while "revise the
        report" or "make it shorter" is genuine case continuity.
        """
        if result.get("school_domain") is not True:
            return result
        if _self_contained_external_content_request(text):
            guarded = dict(result)
            guarded.update({
                "checked": True,
                "school_domain": False,
                "case_relation": "unrelated",
                "school_area": "other",
                "data_use_concepts": [],
                "situation": {},
                "rationale": (
                    "deterministic_domain_boundary:"
                    "self_contained_external_content"
                ),
                "source": "school_semantic_llm+boundary_guard",
            })
            return guarded
        school_anchor = _has_anchor(text, _SCHOOL_ANCHORS)
        followup_anchor = bool(active_workflow_id) and _has_anchor(
            text, _FOLLOWUP_ANCHORS)
        confidence = float(result.get("confidence") or 0.0)
        situation = result.get("situation") or {}
        semantic_school_evidence = bool(
            confidence >= 0.80
            and situation.get("family") not in {None, "", "general_school_admin"}
            and (
                set(situation.get("affected_people_types") or []).intersection(
                    {"student", "staff"}
                )
                or set(situation.get("stakeholder_candidates") or []).intersection({
                    "school_leadership", "school_staff", "education_authority",
                })
            )
        )
        # Without an active case there is nothing private to capture, so a
        # high-confidence school classification can stand on its own. With an
        # active case, require either linguistic continuity or structured
        # school evidence; this keeps "World Cup report" outside the pack
        # without trying to enumerate every possible school phrase.
        if (school_anchor or followup_anchor or semantic_school_evidence
                or (not active_workflow_id and confidence >= 0.80)):
            return result
        guarded = dict(result)
        guarded.update({
            "school_domain": False,
            "case_relation": "unrelated",
            "school_area": "other",
            "data_use_concepts": [],
            "situation": {},
            "rationale": "deterministic_domain_boundary:no_school_or_followup_cue",
            "source": "school_semantic_llm+boundary_guard",
        })
        return guarded

    @staticmethod
    def _normalise(raw: dict[str, Any]) -> dict:
        school_domain = raw.get("school_domain") is True
        relation = str(raw.get("case_relation") or "ambiguous").strip().lower()
        if relation not in _RELATIONS:
            relation = "ambiguous"
        area = str(raw.get("school_area") or "other").strip().lower()
        if area not in _AREAS:
            area = "other"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        concepts = [key for key in _CONCEPT_KEYS if raw.get(key) is True]
        # Closed-schema consistency rule. A model can occasionally return a
        # contradictory pair such as school_domain=false + new_case and even
        # attach school safety concepts to a World Cup request. Do not let an
        # out-of-domain label contaminate the active school case or its policy.
        if not school_domain:
            relation = "unrelated"
            area = "other"
            concepts = []
        situation = SchoolInputSemantics._normalise_situation(
            raw.get("situation")
        )
        audience = str(raw.get("audience") or "unknown").strip().lower()
        if audience not in {
            "internal", "private_recipient", "school_community",
            "public", "unknown",
        }:
            audience = "unknown"
        if audience == "unknown":
            output_audiences = {
                str(item.get("audience") or "").strip().lower()
                for item in (situation.get("requested_outputs") or [])
                if isinstance(item, dict)
            }
            # A role-scoped audience is stronger than a missing top-level
            # suggestion. Prefer the widest explicit audience so privacy and
            # release governance fail closed rather than defaulting inward.
            for candidate in (
                "public", "school_community", "private_recipient", "internal",
            ):
                if candidate in output_audiences:
                    audience = candidate
                    break
        return {
            "checked": True,
            "school_domain": school_domain,
            "case_relation": relation,
            "school_area": area,
            "requested_action": str(raw.get("requested_action") or "")[:80],
            "audience": audience[:40],
            "confidence": confidence,
            "rationale": str(raw.get("rationale") or "")[:300],
            "data_use_concepts": concepts,
            "situation": situation,
            "source": "school_semantic_llm",
        }

    @staticmethod
    def _normalise_situation(value: Any) -> dict:
        raw = value if isinstance(value, dict) else {}
        family = str(raw.get("family") or "general_school_admin").strip().lower()
        if family not in _SITUATION_FAMILIES:
            family = "general_school_admin"
        phase = str(raw.get("phase") or "unknown").strip().lower()
        if phase not in _SITUATION_PHASES:
            phase = "unknown"
        severity = str(raw.get("severity") or "unknown").strip().lower()
        if severity not in _SITUATION_SEVERITIES:
            severity = "unknown"
        signals = sorted({
            str(item).strip().lower()
            for item in (raw.get("signals") or [])
            if str(item).strip().lower() in _SITUATION_SIGNALS
        })
        stakeholders = sorted({
            str(item).strip().lower()
            for item in (raw.get("stakeholder_candidates") or [])
            if str(item).strip().lower() in _SITUATION_STAKEHOLDERS
        })
        people = sorted({
            str(item).strip().lower()
            for item in (raw.get("affected_people_types") or [])
            if str(item).strip().lower() in {
                "student", "staff", "guardian", "visitor", "unknown",
            }
        })
        facts = []
        for item in (raw.get("known_facts") or [])[:20]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "reported").strip().lower()
            if status not in {"reported", "confirmed", "unverified", "unknown"}:
                status = "reported"
            facts.append({
                "fact_id": str(item.get("fact_id") or "fact")[:80],
                "value": str(item.get("value") or "")[:160],
                "status": status,
            })
        unknowns = []
        for item in (raw.get("unknowns") or [])[:12]:
            if not isinstance(item, dict):
                continue
            impact = str(item.get("impact") or "content_only").strip().lower()
            if impact not in {
                "life_safety", "governance_boundary", "required_deliverables",
                "external_recipient", "content_only",
            }:
                impact = "content_only"
            unknowns.append({
                "fact_id": str(item.get("fact_id") or "unknown")[:80],
                "impact": impact,
            })
        requested_outputs: list[dict] = []
        for item in (raw.get("requested_outputs") or [])[:12]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("artifact_role") or "").strip().lower()
            if role not in _OUTPUT_ROLES:
                continue
            audience = str(item.get("audience") or "").strip().lower()
            if audience not in _OUTPUT_AUDIENCES:
                audience = _OUTPUT_ROLE_DEFAULT_AUDIENCE.get(role, "internal")
            recipient = str(item.get("recipient_type") or "").strip().lower()
            if recipient not in _SITUATION_STAKEHOLDERS:
                recipient = _OUTPUT_ROLE_DEFAULT_RECIPIENT.get(role) or {
                    "internal": "school_staff",
                    "private_recipient": "guardian",
                    "school_community": "school_community",
                    "external_agency": "education_authority",
                    "public": "public_media",
                }[audience]
            languages = sorted({
                str(language).strip().lower()
                for language in (item.get("languages") or [])
                if str(language).strip().lower() in _OUTPUT_LANGUAGES
            })
            source_fact_ids = [
                str(fact_id).strip()[:80]
                for fact_id in (item.get("source_fact_ids") or [])[:20]
                if str(fact_id).strip()
            ]
            requested_outputs.append({
                "artifact_role": role,
                "label": str(item.get("label") or role.replace("_", " ").title())[:120],
                "purpose": str(item.get("purpose") or "")[:240],
                "audience": audience,
                "recipient_type": recipient,
                "languages": languages,
                "source_fact_ids": source_fact_ids,
                **(
                    {"explicit": item.get("explicit") is True}
                    if isinstance(item.get("explicit"), bool)
                    else {}
                ),
            })

        return {
            "family": family,
            "secondary_families": sorted({
                str(item).strip().lower()
                for item in (raw.get("secondary_families") or [])
                if str(item).strip().lower() in _SITUATION_FAMILIES
                and str(item).strip().lower() != family
            }),
            "phase": phase,
            "severity": severity,
            "signals": signals,
            "affected_people_types": people,
            "stakeholder_candidates": stakeholders,
            "case_summary": str(raw.get("case_summary") or "")[:300],
            "known_facts": facts,
            "unknowns": unknowns,
            "requested_deliverables": [
                str(item).strip().lower()[:80]
                for item in (raw.get("requested_deliverables") or [])[:12]
            ],
            "requested_outputs": requested_outputs,
            "explicit_external_actions": [
                str(item).strip().lower()[:80]
                for item in (raw.get("explicit_external_actions") or [])[:12]
                if str(item).strip().lower() in _SITUATION_STAKEHOLDERS
            ],
        }

    @staticmethod
    def _fallback(
        reason: str,
        text: str = "",
        active_workflow_id: str | None = None,
    ) -> dict:
        external_content = _self_contained_external_content_request(text)
        school_domain = not external_content and (
            _has_anchor(text, _SCHOOL_ANCHORS) or (
                bool(active_workflow_id)
                and _has_anchor(text, _FOLLOWUP_ANCHORS)
            )
        )
        # When the provider is unavailable, a positive school-domain match is
        # deliberately only a lexical floor (the richer case facets remain
        # unchecked).  Conversely, a self-contained request with no school
        # anchor and no active-case follow-up anchor is safely confirmed as
        # outside this domain pack.  This prevents an outage from handing a
        # World Cup, recipe or stock-market request to the generic planner.
        boundary_confirmed = not school_domain
        return {
            "checked": boundary_confirmed,
            "school_domain": school_domain,
            "case_relation": (
                "follow_up" if school_domain and active_workflow_id else
                "new_case" if school_domain else "unrelated"
            ),
            "school_area": "other",
            "requested_action": "",
            "audience": "unknown",
            "confidence": 0.0,
            "rationale": (
                f"{reason}:self_contained_external_content"
                if external_content else reason
            ),
            "data_use_concepts": [],
            "situation": {},
            "source": (
                "fallback+source_domain_boundary"
                if boundary_confirmed else "fallback"
            ),
        }
