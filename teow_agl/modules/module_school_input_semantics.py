"""School-domain semantic preflight for Mixed-Live competition follow-ups.

The LLM is an interpreter, never the policy authority.  It maps unlimited
natural-language phrasings onto a small, closed school-administration schema.
The deterministic 101D/103 governance layers consume the resulting concepts
and remain the only components allowed to choose BLUE/GREEN/RED/INFEASIBLE.
"""
from __future__ import annotations

import re
from typing import Any


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
}

# The LLM interprets free language, but an active case is never evidence by
# itself that a new, self-contained request belongs to the school pack.  These
# anchors are deliberately closed and multilingual; they are used only to
# prevent context capture, never to choose a governance route.
_SCHOOL_ANCHORS = (
    "school", "student", "pupil", "teacher", "parent", "guardian",
    "classroom", "campus", "attendance", "discipline", "disciplinary",
    "school event", "school notice", "school report", "official record",
    "bazaar", "fundraiser", "fundraising", "coupon", "speech competition",
    "sekolah", "murid", "pelajar", "guru", "ibu bapa", "penjaga",
    "canteen", "classroom", "laboratory", "library", "assembly",
    "school bus", "hostel", "dormitory", "principal", "headteacher",
    "recess", "playground", "school hall", "form teacher", "homeroom",
    "prefect", "exam paper", "co-curricular", "cocurricular",
    "kantin", "kelas", "makmal", "perhimpunan", "bas sekolah", "asrama",
    "waktu rehat", "padang sekolah", "guru kelas", "pengawas", "kertas peperiksaan",
    "disiplin", "kehadiran", "jualan amal", "kupon",
    "学校", "学生", "老师", "教师", "家长", "监护人", "纪律", "出勤",
    "违规", "校园", "校内", "义卖", "固本", "餐券", "演讲", "校方",
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
- audience: internal | private_recipient | public | unknown.
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
- explicit_external_actions: stakeholder types only when the user explicitly
  asks to contact, send, publish or submit. Preparing a draft is not an
  external action.

All safety concept fields are booleans. If uncertain, keep the concept false
and lower confidence; use case_relation=ambiguous where appropriate."""
        try:
            # OpenAI has a native strict JSON response mode. The generic
            # chat_json() path asks for JSON in prose but some models may emit
            # fenced JavaScript-like objects with unquoted enum values (the
            # meaning is correct, yet json.loads must reject it). Use the
            # provider's json_object mode so a valid semantic classification
            # is never silently discarded into the deterministic fallback.
            if (getattr(self.chat_llm, "backend", "") == "openai"
                    and hasattr(self.chat_llm, "chat_json_openai")):
                raw = self.chat_llm.chat_json_openai(
                    system, user, max_tokens=900
                )
            else:
                raw = self.chat_llm.chat_json(system, user, max_tokens=900)
        except Exception:
            return self._fallback("llm_error", text, active_workflow_id)
        if not isinstance(raw, dict) or not raw:
            return self._fallback("empty_or_unparseable", text, active_workflow_id)
        normalised = self._normalise(raw)
        return self._apply_domain_boundary(
            normalised, text=text, active_workflow_id=active_workflow_id)

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
        return {
            "checked": True,
            "school_domain": school_domain,
            "case_relation": relation,
            "school_area": area,
            "requested_action": str(raw.get("requested_action") or "")[:80],
            "audience": str(raw.get("audience") or "unknown")[:40],
            "confidence": confidence,
            "rationale": str(raw.get("rationale") or "")[:300],
            "data_use_concepts": concepts,
            "situation": SchoolInputSemantics._normalise_situation(
                raw.get("situation")
            ),
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
        school_domain = _has_anchor(text, _SCHOOL_ANCHORS) or (
            bool(active_workflow_id) and _has_anchor(text, _FOLLOWUP_ANCHORS)
        )
        return {
            "checked": False,
            "school_domain": school_domain,
            "case_relation": (
                "follow_up" if school_domain and active_workflow_id else
                "new_case" if school_domain else "ambiguous"
            ),
            "school_area": "other",
            "requested_action": "",
            "audience": "unknown",
            "confidence": 0.0,
            "rationale": reason,
            "data_use_concepts": [],
            "situation": {},
            "source": "fallback",
        }
