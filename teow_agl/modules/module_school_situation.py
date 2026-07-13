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


_SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_EXTERNAL_RECIPIENTS = {
    "guardian", "medical_services", "malaysia_emergency_services_999",
    "fire_and_rescue", "police", "education_authority", "local_authority",
    "event_organizer", "vendor", "transport_provider", "public_media",
    "external_stakeholder",
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
            lexical_fallback=not (
                semantics.get("checked") is True and bool(suggested)
            ),
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
            if "student" in affected_people or "minor_involved" in signals:
                signals.add("minor_involved")
                stakeholders.add("guardian")
            if severity == "unknown":
                severity = "medium"
        if "active_danger" in signals:
            signals.add("external_help_may_be_required")
            stakeholders.add("malaysia_emergency_services_999")
            severity = "critical"
            phase = "ongoing"
        if severity in {"critical", "high"}:
            signals.add("possible_regulatory_trigger")
            stakeholders.update({"school_leadership", "education_authority"})

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
            "case_summary": str(suggested.get("case_summary") or text).strip()[:300],
            "known_facts": self._safe_fact_list(suggested.get("known_facts")),
            "unknowns": self._safe_unknowns(suggested.get("unknowns")),
            "requested_deliverables": self._closed_list(
                suggested.get("requested_deliverables"), set(self.catalog)
            ),
            "explicit_external_actions": self._closed_list(
                suggested.get("explicit_external_actions"), _EXTERNAL_RECIPIENTS
            ),
            "compiler_source": semantics.get("source", "unknown"),
            "governance_note": (
                "Situation labels propose coverage only; they never authorise an action."
            ),
        }
        response_pack = self._build_pack(
            text,
            situation,
            selected_deliverable_ids=selected_deliverable_ids,
            custom_deliverables=custom_deliverables,
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
    def _safe_fact_list(value: Any) -> list[dict]:
        out: list[dict] = []
        for item in (value or [])[:20]:
            if not isinstance(item, dict):
                continue
            key = re.sub(r"[^a-z0-9_]+", "_", str(item.get("fact_id") or "fact").lower())[:80]
            status = str(item.get("status") or "reported").lower()
            if status not in {"reported", "confirmed", "unverified", "unknown"}:
                status = "reported"
            out.append({
                "fact_id": key or "fact",
                "value": str(item.get("value") or "")[:160],
                "status": status,
                "source_type": "user",
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

        if not lexical_fallback:
            return family, phase, severity, signals, stakeholders

        # Degradation path only. Live arbitrary inputs normally receive these
        # facets from the semantic LLM; the fallback protects operation during
        # a transient API/JSON failure and never authorises an action.
        low = (text or "").casefold()
        if _contains_any(low, (
            "injur", "hurt", "bleed", "unconscious", "hospital", "ambulance",
            "bitten", "bite", "sick", "poison", "受伤", "咬伤", "中毒",
            "cedera", "digigit", "keracunan",
        )):
            signals.update({"injury_or_illness", "minor_involved"})
            stakeholders.update({"guardian", "medical_services"})
            if family == "general_school_admin":
                family = "health_medical"
        if _contains_any(low, (
            "emergency", "danger", "fire", "smoke", "gas leak", "chemical spill",
            "intruder", "missing student", "snake", "wild animal", "flood",
            "紧急", "危险", "失踪", "火灾", "蛇", "kecemasan", "bahaya", "kebakaran",
        )):
            signals.update({"active_danger", "external_help_may_be_required"})
            stakeholders.add("malaysia_emergency_services_999")
            family = "safety_emergency"
            phase = "ongoing"
            severity = "critical"
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
        if _contains_any(low, ("data breach", "ransomware", "phishing", "leak student data", "资料泄露", "勒索")):
            signals.update({
                "personal_data_involved", "evidence_preservation_needed",
                "data_security_incident",
            })
            family = "cyber_data"
        if _contains_any(low, ("money", "coupon", "procurement", "refund", "cash", "固本", "采购", "退款")):
            signals.add("financial_value_involved")
            if family == "general_school_admin":
                family = "finance_procurement"
        if _contains_any(low, ("discipline", "misconduct", "bully", "fight", "违规", "霸凌", "打架")):
            signals.add("evidence_preservation_needed")
            family = "discipline_behaviour"
        return family, phase, severity, signals, stakeholders

    def _build_pack(
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
        if answers.get("immediate_danger"):
            return None
        unknown_ids = {u.get("fact_id") for u in situation.get("unknowns") or []}
        life_unknown = any(
            u.get("impact") == "life_safety" for u in situation.get("unknowns") or []
        )
        if situation["severity"] in {"critical", "high"} and (
            life_unknown or "danger_still_present" in unknown_ids
        ) and "active_danger" not in set(situation["signals"]):
            return {
                "question_id": "immediate_danger",
                "prompt": "Is there still immediate danger or an unmet medical emergency now?",
                "why": "This changes the immediate safety priority and external-contact package.",
                "options": ["Yes", "No", "Unknown"],
                "allow_tbc": True,
                "scope": "life_safety",
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
        })
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
        )
        if (
            (is_text_artifact or is_planner_external)
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
