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

from ..models import CandidateAction, CandidatePlan, ExecutionResult, TaskEnvelope


_TEXT_FILE_TOOLS = {"docx", "report", "fs"}
_GENERIC_STEMS = {"doc", "document", "report", "draft", "output", "file"}

_ROLE_FILENAMES = {
    "internal_incident_report": "internal_incident_report.md",
    "private_parent_notice": "parent_notification_draft.md",
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
    "school_document": "school_document_draft.md",
}

_ROLE_AUDIENCE = {
    "internal_incident_report": "internal",
    "discipline_investigation_report": "internal",
    "internal_action_plan": "internal",
    "private_parent_notice": "private_recipient",
    "public_communication_draft": "public",
    "emergency_contact_script": "external_agency",
    "fire_rescue_contact_script": "external_agency",
    "medical_handover_script": "external_agency",
    "site_safety_checklist": "internal",
    "student_accountability_checklist": "internal",
    "regulatory_notification_assessment": "internal",
    "education_authority_report": "external_agency",
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
    "school_document": "internal",
}

_PARENT_LETTER_MARKERS = (
    re.compile(r"(?mi)^#\s+.*(?:parent|guardian).*(?:notice|notification|letter)"),
    re.compile(r"(?mi)^dear\s+(?:parent|guardian|mr\.?|mrs\.?|ms\.?|family|\[)"),
    re.compile(r"(?mi)^sincerely,?\s*$"),
    re.compile(r"(?mi)^yours\s+(?:faithfully|sincerely),?\s*$"),
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
            r"\b(?:was|has been|had been|were)\s+(?:transported|taken|admitted)\b"
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
            r"updates will be provided)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:(?:we|the school) will (?:keep (?:you|the family) updated|"
            r"provide (?:you|the family) with (?:further|more|additional) "
            r"(?:information|details|updates)|share (?:further )?updates)|"
            r"updates will be provided)\b",
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


def school_cover_message(user_intent: str, filenames: list[str]) -> str:
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
    requested = str(semantics.get("requested_action") or "").casefold()
    if re.search(
        r"\b(?:send|publish|submit|release|message|contact|notify|email|hantar)\b|"
        r"发送|發送|发布|發佈|提交|联系|聯絡|通知|发给|發給",
        requested,
    ):
        return True
    text = (user_intent or "").casefold()
    return bool(re.search(
        r"\b(?:contact|notify|email|message)\s+"
        r"(?:(?:his|her|their|the|a)\s+|(?:[\w-]+'s)\s+)?"
        r"(?:parent|guardian|family)\b|"
        r"\bsend\b[^\n.!?]{0,100}\bto\s+(?:the\s+)?"
        r"(?:parent|guardian|family|public|facebook)\b|"
        r"\bpublish\b|\bpost\b[^\n.!?]{0,80}\b(?:facebook|publicly|online)\b|"
        r"联系(?:他|她|其|这名学生)?(?:的)?家长|聯絡(?:他|她|其)?(?:的)?家長|"
        r"通知家长|通知家長|发给家长|發給家長",
        text,
    ))


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
    text = (user_intent or "").casefold()
    return bool(re.search(
        r"\b(?:do\s+not|don't|not\s+to|never)\s+(?:\w+\s+){0,3}"
        r"(?:send|publish|submit|release|message|email|contact|notify)\b|"
        r"\b(?:draft\s+only|do\s+not\s+send\s+or\s+publish|"
        r"do\s+not\s+publish\s+or\s+send)\b|"
        r"不要(?:发送|發送|发布|發佈|提交|联系|聯絡|通知)|只(?:做|要)草稿",
        text,
    ))


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
    action.metadata.update({
        "school_output_contract": True,
        "school_output_contract_version": "1.0",
        "school_content_role": "external_release_gate",
        "artifact_role": "external_release_gate",
        "external_release_action": True,
        "audience": audience or "unknown",
        "output_scope": "public_release" if public else "private_recipient",
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
                "internal", "private_recipient", "external_agency", "public",
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
                "public_draft" if audience == "public" else audience
            ),
        })
        task_concepts = {
            str(item).strip().lower()
            for item in (envelope.metadata.get("data_use_concepts") or [])
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
            and not fallback_public_sensitive
        ):
            # A privacy-safe public holding draft must be judged on its own
            # body, not contaminated by sensitive concepts needed by internal
            # sibling reports. 101D still scans the generated body and the
            # real publication gate carries the release concepts separately.
            task_concepts.difference_update({
                "public_pii", "health_or_discipline",
                "student_sensitive_data", "student_sensitive_public",
            })
        elif fallback_public_sensitive:
            task_concepts.update({
                "health_or_discipline", "student_sensitive_data",
                "public_disclosure",
            })
        # Pre-set a step-local list so Runtime.set_default cannot copy the
        # task's eventual release intent into an internal draft action.
        meta["data_use_concepts"] = sorted(task_concepts)

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
    gates = list(release_actions)
    release_negated = _external_release_is_negated(envelope.normalized_goal)
    requested_release = _requests_external_release(
        semantics, envelope.normalized_goal) and not release_negated
    if requested_release or (gates and not release_negated):
        if not gates:
            gates = [CandidateAction(
                tool="chat", operation="answer", target="",
                purpose="request human approval before external release",
                expected_effect="pause before any external send or publication",
                reversibility="high", uncertainty="low", requires_governance=True,
                metadata={
                    "body": (
                        "The governed draft is ready. Human approval is required "
                        "before any external release; nothing has been sent or published."
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
            gate.metadata["data_use_concepts"] = sorted({
                str(item).strip().lower()
                for item in (envelope.metadata.get("data_use_concepts") or [])
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


def validate_school_markdown(
    action: CandidateAction,
    content: str,
    source_goal: str,
) -> dict[str, list[str]]:
    """Return deterministic validation issues grouped by policy layer."""
    text = (content or "").strip()
    role = str((action.metadata or {}).get("artifact_role") or "")
    issues: dict[str, list[str]] = {"hygiene": [], "role": [], "grounding": []}

    if len(text) < 120:
        issues["hygiene"].append("artifact_too_short")
    if (action.metadata or {}).get("coverage_source") == "school_response_pack":
        short_roles = {
            "private_parent_notice", "public_communication_draft",
            "emergency_contact_script", "fire_rescue_contact_script",
            "medical_handover_script", "external_stakeholder_message",
            "staff_internal_notice",
        }
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

    needs_tbc = bool(re.search(
        r"\btbc\b|\bunverified\b|not\s+(?:been\s+)?verified|not\s+confirmed|"
        r"\bunknown\b|not\s+provided|missing\s+(?:fact|detail|information)",
        source_goal or "", re.IGNORECASE,
    ))
    if needs_tbc and "tbc" not in text.lower():
        issues["hygiene"].append("source_requires_tbc_but_artifact_has_none")

    if role != "private_parent_notice":
        if any(rx.search(text) for rx in _PARENT_LETTER_MARKERS):
            issues["role"].append("parent_letter_content_in_non_parent_artifact")
    if role == "private_parent_notice":
        if any(rx.search(text) for rx in _INTERNAL_REPORT_MARKERS):
            issues["role"].append("internal_report_content_in_parent_notice")
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

    source = source_goal or ""
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
            if token not in {"the", "a", "an", "school"}
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

    return issues


def artifact_similarity(a: str, b: str) -> float:
    def normalise(text: str) -> str:
        text = re.sub(r"(?m)^#{1,6}\s+.*$", " ", text or "")
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
    for action in artifacts:
        issues = validate_school_markdown(
            action, contents.get(action.action_id, str(action.metadata.get("content") or "")),
            envelope.normalized_goal or envelope.raw_goal,
        )
        if issues["hygiene"]:
            hygiene[action.action_id] = issues["hygiene"]
        if issues["role"]:
            roles[action.action_id] = issues["role"]
        if issues["grounding"]:
            grounding[action.action_id] = issues["grounding"]

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
    ])

    similarity_errors: list[dict] = []
    for i, left in enumerate(artifacts):
        for right in artifacts[i + 1:]:
            score = artifact_similarity(
                contents.get(left.action_id, ""), contents.get(right.action_id, ""))
            if score >= 0.72:
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
