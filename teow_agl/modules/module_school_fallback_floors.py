"""Provider-independent safety facets for reported school incidents.

The semantic model normally supplies these facets.  This module is a narrow
degradation floor for cases where the provider is unavailable or returns
invalid JSON.  Its predicates require incident grammar and only add controls;
they never authorise an external action.
"""
from __future__ import annotations

import re
from typing import Any


_MINOR = r"(?:students?|pupils?|children|student|pupil|child|murid|pelajar)"


def _base() -> dict[str, Any]:
    return {
        "signals": set(),
        "suppress_signals": set(),
        "stakeholders": set(),
        "severity": "unknown",
        "family": "",
        "phase": "",
    }


def deterministic_incident_facets(text: str) -> dict[str, Any]:
    """Return closed, high-precision facets observable in ``text``.

    This intentionally covers incident *categories* rather than memorised
    prompts: group illness, structural danger, accidental disclosure,
    staff-on-pupil harm, laboratory exposure, ransomware and flooding.
    """
    value = str(text or "").casefold()
    out = _base()
    signals: set[str] = out["signals"]
    stakeholders: set[str] = out["stakeholders"]
    severity_rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    def escalate(family: str, severity: str, phase: str) -> None:
        """Raise, but never lower, the deterministic incident floor."""
        current = str(out.get("severity") or "unknown")
        if severity_rank[severity] > severity_rank[current]:
            out.update({"family": family, "severity": severity, "phase": phase})
        elif not out.get("family"):
            out["family"] = family
            out["phase"] = phase

    exercise_only = bool(re.search(
        r"\b(?:drill|simulation|tabletop|training|awareness|lesson|poster|"
        r"policy\s+review|practice\s+scenario)\b",
        value,
    ))

    # A drill describes the surrounding activity, not the medical event that
    # actually happened during it.  Keep the exercise from becoming a real
    # fire, while retaining an explicitly reported collapse/faint as a real
    # pupil-health case.  Strong temporal/actuality grammar avoids matching a
    # future role-play where a pupil merely plans or pretends to faint.
    real_health_event_during_exercise = bool(re.search(
        rf"\b(?:during|while)\b[^.!?\n]{{0,70}}\b(?:drill|exercise)\b"
        rf"[^.!?\n]{{0,100}}\b{_MINOR}\b[^.!?\n]{{0,55}}"
        r"\b(?:suddenly\s+|unexpectedly\s+|actually\s+)?"
        r"(?:fainted|collapsed|became\s+unconscious)\b|"
        r"\bsemasa\b[^.!?\n]{0,75}\blatihan\b[^.!?\n]{0,100}"
        r"\b(?:seorang\s+)?(?:murid|pelajar)\b[^.!?\n]{0,55}"
        r"\b(?:tiba-tiba\s+|sebenarnya\s+|telah\s+)?"
        r"(?:pengsan|rebah|tidak\s+sedarkan\s+diri)\b",
        value,
    ))
    if real_health_event_during_exercise:
        signals.update({"injury_or_illness", "minor_involved"})
        stakeholders.update({"guardian", "medical_services"})
        escalate("health_medical", "high", "just_occurred")

    # Several pupils becoming ill after food/water exposure is a real health
    # incident even when the phrase "food poisoning" is absent.
    food_source = bool(re.search(
        r"\b(?:canteen|cafeteria|school\s+lunch|school\s+meal|food|water|"
        r"makanan|kantin)\b",
        value,
    ))
    illness = bool(re.search(
        rf"\b{_MINOR}\b[^.!?\n]{{0,80}}\b(?:vomit(?:ed|ing|s)?|"
        r"fell\s+sick|became\s+ill|diarrh(?:ea|oea)|nause(?:a|ous)|"
        r"stomach\s+(?:pain|ache)|poison(?:ed|ing))\b|"
        rf"\b(?:vomit(?:ed|ing|s)?|fell\s+sick|became\s+ill|"
        r"diarrh(?:ea|oea)|poison(?:ed|ing))\b[^.!?\n]{0,80}"
        rf"\b{_MINOR}\b",
        value,
    ))
    exposure_link = bool(re.search(
        r"\b(?:after|from|following|because\s+of|linked\s+to|suspected\s+from)\b"
        r"[^.!?\n]{0,55}\b(?:lunch|meal|food|water|canteen|cafeteria)\b|"
        r"\b(?:food\s+poisoning|contaminated\s+(?:food|water))\b",
        value,
    ))
    if illness and (food_source and exposure_link):
        signals.update({"injury_or_illness", "minor_involved", "food_water_exposure"})
        stakeholders.update({"guardian", "medical_services", "school_leadership"})
        out.update({"family": "food_hygiene", "severity": "high", "phase": "just_occurred"})

    # Explicit reported injury in common number-before-person word order.
    # This covers e.g. "three pupils are injured" without treating planning
    # or general safety training as an incident.
    reported_group_injury = False
    severe_minor_injury = False
    for injury_clause in re.split(r"[.!?;\n]+", value):
        planned_clause = bool(re.search(
            r"\b(?:plan(?:ned|ning)?|scheduled|next\s+(?:week|month)|"
            r"training|simulation|simulate|scenario|tabletop|exercise|drill|"
            r"role[- ]?play|latihan|simulasi|senario)\b",
            injury_clause,
        ))
        actual_during_exercise = bool(re.search(
            r"\b(?:during|while)\b[^.!?;]{0,80}\b(?:drill|exercise)\b"
            r"[^.!?;]{0,100}\b(?:actually|unexpectedly|suddenly|"
            r"became\s+unconscious|fainted|collapsed)\b|"
            r"\bsemasa\b[^.!?;]{0,80}\blatihan\b[^.!?;]{0,100}"
            r"\b(?:tiba-tiba|sebenarnya|pengsan|rebah|"
            r"tidak\s+sedarkan\s+diri)\b",
            injury_clause,
        ))
        if planned_clause and not actual_during_exercise:
            continue
        reported_group_injury = reported_group_injury or bool(re.search(
            r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
            r"(?:students?|pupils?|children|murid|pelajar)\b"
            r"[^.!?]{0,45}\b(?:is|are|were|was|remain|remains)?\s*"
            r"(?:injured|bleeding|unconscious|hospitali[sz]ed)\b",
            injury_clause,
        ))
        severe_minor_injury = severe_minor_injury or bool(re.search(
            rf"\b{_MINOR}\b[^.!?]{{0,55}}\b"
            r"(?:bleeding(?:\s+heavily)?|unconscious|hospitali[sz]ed|"
            r"pengsan|tidak\s+sedarkan\s+diri|dimasukkan\s+ke\s+hospital)\b",
            injury_clause,
        ))
    if reported_group_injury or severe_minor_injury:
        signals.update({"injury_or_illness", "minor_involved"})
        stakeholders.update({"guardian", "medical_services"})
        if out["severity"] in {"unknown", "low", "medium"}:
            out["severity"] = "high"
        if not out["family"]:
            out["family"] = "health_medical"
        if not out["phase"]:
            out["phase"] = "just_occurred"

    # Structural damage is an emergency only when the source reports a real
    # collapse/fall and a present unsafe or closure consequence.
    structural_event = bool(re.search(
        r"\b(?:roof|ceiling|wall|beam|panel|building|classroom)\b"
        r"[^.!?\n]{0,75}\b(?:collaps(?:e|ed)|fell|fallen|caved\s+in|gave\s+way)\b|"
        r"\b(?:collaps(?:e|ed)|caved\s+in|gave\s+way)\b"
        r"[^.!?\n]{0,75}\b(?:roof|ceiling|wall|beam|panel|building|classroom)\b",
        value,
    ))
    structural_consequence = bool(re.search(
        r"\b(?:unsafe|may\s+still\s+be\s+unsafe|not\s+safe|cordon|"
        r"close(?:d|s|\s+tomorrow)?|closure|cannot\s+be\s+used|"
        r"evacuat(?:e|ed|ion))\b",
        value,
    ))
    structural_uncontrolled = bool(re.search(
        r"\b(?:unsafe|may\s+still\s+be\s+unsafe|not\s+safe|cordon|"
        r"evacuat(?:e|ed|ion)|people?\s+(?:remain|still)\s+inside|"
        r"further\s+(?:collapse|panels?\s+falling))\b",
        value,
    ))
    if structural_event and structural_consequence:
        signals.add("service_disruption")
        stakeholders.add("school_leadership")
        if structural_uncontrolled:
            signals.add("active_danger")
            stakeholders.add("fire_and_rescue")
            out.update({
                "family": "facilities_environment", "severity": "high",
                "phase": "ongoing",
            })
        else:
            out.update({
                "family": "facilities_environment", "severity": "medium",
                "phase": "just_occurred",
            })

    # A source-grounded transfer of pupil health/identity records to the wrong
    # recipient is an actual data incident, not merely a privacy topic.
    sensitive_records = bool(re.search(
        rf"\b{_MINOR}\b[^.!?\n]{{0,55}}\b(?:medical|health|personal|"
        r"attendance|identity|disciplin(?:e|ary))\s+(?:records?|data|file|spreadsheet)\b|"
        r"\b(?:medical|health|personal|attendance|identity|disciplin(?:e|ary))\s+"
        rf"(?:records?|data|file|spreadsheet)\b[^.!?\n]{{0,55}}\b{_MINOR}\b",
        value,
    ))
    wrong_disclosure = bool(re.search(
        r"\b(?:emailed|sent|shared|uploaded|disclosed|forwarded)\b"
        r"[^.!?\n]{0,85}\b(?:wrong|unintended|unauthori[sz]ed)\b"
        r"[^.!?\n]{0,35}\b(?:vendor|recipient|person|address|party|company)\b|"
        r"\b(?:wrong|unintended|unauthori[sz]ed)\b[^.!?\n]{0,35}"
        r"\b(?:vendor|recipient|person|address|party|company)\b",
        value,
    ))
    if sensitive_records and wrong_disclosure:
        signals.update({
            "data_security_incident", "personal_data_involved",
            "evidence_preservation_needed", "possible_regulatory_trigger",
        })
        stakeholders.update({"school_leadership", "education_authority"})
        out.update({"family": "cyber_data", "severity": "high", "phase": "just_occurred"})

    # Reported staff-on-pupil violence is a safeguarding allegation.  Wording
    # such as "may have" is enough for a protective plan, not a finding of fact.
    staff_harm = bool(re.search(
        r"\b(?:teacher|staff|employee|coach|adult)\b[^.!?\n]{0,75}"
        r"\b(?:hit|slapped|struck|punched|kicked|assaulted|grabbed)\b"
        rf"[^.!?\n]{{0,50}}\b{_MINOR}\b|"
        rf"\b{_MINOR}\b[^.!?\n]{{0,75}}\b(?:hit|slapped|struck|punched|"
        r"kicked|assaulted|grabbed)\b[^.!?\n]{0,50}\b(?:by\s+)?"
        r"(?:a\s+)?(?:teacher|staff|employee|coach|adult)\b",
        value,
    ))
    if staff_harm:
        signals.update({
            "safeguarding_concern", "minor_involved",
            "evidence_preservation_needed",
        })
        stakeholders.update({"guardian", "school_leadership"})
        out.update({"family": "safeguarding_welfare", "severity": "high", "phase": "just_occurred"})

    # Laboratory/chemical exposure combines the active hazard and observed
    # health effect; a generic future science lesson does not match.
    chemical_release = bool(re.search(
        r"\b(?:chemical|acid|chlorine|gas|fumes?)\b[^.!?\n]{0,55}"
        r"\b(?:spill(?:ed)?|leak(?:ed|ing)?|release(?:d)?|exposure)\b|"
        r"\b(?:spill(?:ed)?|leak(?:ed|ing)?|release(?:d)?|exposure)\b"
        r"[^.!?\n]{0,55}\b(?:chemical|acid|chlorine|gas|fumes?)\b",
        value,
    ))
    health_effect = bool(re.search(
        rf"\b{_MINOR}\b[^.!?\n]{{0,75}}\b(?:dizzy|faint(?:ed)?|"
        r"cough(?:ed|ing)?|vomit(?:ed|ing)?|unwell|difficulty\s+breathing|"
        r"burn(?:ed|t)?|pening|pengsan|batuk|muntah|tidak\s+sihat|"
        r"sukar\s+bernafas)\b|"
        r"\b(?:dizzy|faint(?:ed)?|cough(?:ed|ing)?|vomit(?:ed|ing)?|unwell|"
        r"pening|pengsan|batuk|muntah|tidak\s+sihat|sukar\s+bernafas)\b"
        rf"[^.!?\n]{{0,60}}\b{_MINOR}\b",
        value,
    ))
    still_unsafe = bool(re.search(
        r"\b(?:not\s+(?:been\s+)?cleared|still\s+unsafe|may\s+still\s+be\s+unsafe|"
        r"ongoing|room\s+has\s+not\s+been\s+cleared)\b",
        value,
    ))
    if chemical_release and health_effect:
        signals.update({"injury_or_illness", "minor_involved"})
        stakeholders.update({"guardian", "medical_services"})
        out.update({"family": "safety_emergency", "severity": "high", "phase": "just_occurred"})
        if still_unsafe:
            signals.update({"active_danger", "external_help_may_be_required"})
            stakeholders.update({"malaysia_emergency_services_999", "fire_and_rescue"})
            out.update({"severity": "critical", "phase": "ongoing"})

    # A current gas odour at school is an active facilities hazard.  Requiring
    # a reported smell/leak plus a school location avoids classifying science
    # lessons or future drills as incidents.
    gas_report = bool(re.search(
        r"\b(?:gas|fumes?)\b[^.!?\n]{0,55}\b(?:smell|odou?r|leak(?:ing|ed)?)\b|"
        r"\b(?:smell|odou?r)\b[^.!?\n]{0,45}\b(?:gas|fumes?)\b|"
        r"\bbau\s+gas\b|\bgas\b[^.!?\n]{0,35}\b(?:bocor|bau)\b",
        value,
    ))
    school_location = bool(re.search(
        r"\b(?:school|classroom|canteen|cafeteria|kitchen|laboratory|lab|hall|"
        r"office|gate|compound|campus|sekolah|bilik\s+darjah|kantin|dapur|"
        r"makmal|dewan|pagar)\b",
        value,
    ))
    if gas_report and school_location and not exercise_only:
        signals.update({"active_danger", "external_help_may_be_required"})
        stakeholders.update({"school_leadership", "fire_and_rescue"})
        if health_effect:
            signals.update({"injury_or_illness", "minor_involved"})
            stakeholders.update({"guardian", "medical_services"})
        escalate("safety_emergency", "critical", "ongoing")

    # A communicated bomb/explosive threat against a school is handled as an
    # active protective and accountability case.  Mere training material is
    # deliberately excluded.
    bomb_threat = bool(re.search(
        r"\b(?:caller|call|message|note|email|person|someone)\b"
        r"[^.!?\n]{0,90}\b(?:bomb|explosive|device)\b|"
        r"\b(?:bomb|explosive)\s+threat\b|\bancaman\s+bom\b|"
        r"\b(?:panggilan|mesej|nota)\b[^.!?\n]{0,65}\bbom\b",
        value,
    ))
    if bomb_threat and school_location and not exercise_only:
        signals.update({
            "active_danger", "external_help_may_be_required",
            "evacuation_accountability", "minor_involved",
            "evidence_preservation_needed",
        })
        stakeholders.update({"school_leadership", "police", "guardian"})
        escalate("safety_emergency", "critical", "ongoing")

    # Active unauthorised entry requires a safety floor only when the source
    # reports entry/presence plus threatening or uncontrolled behaviour.
    intruder = bool(re.search(
        r"\b(?:unknown|unauthori[sz]ed|unidentified|strange)\s+"
        r"(?:adult|person|man|woman|visitor)\b[^.!?\n]{0,100}"
        r"\b(?:entered|inside|on\s+(?:the\s+)?(?:school\s+)?grounds?|"
        r"near\s+classrooms?|refus(?:e|ed|ing)\s+to\s+leave|shouting|"
        r"threaten(?:ed|ing)?|weapon|knife)\b|"
        r"\b(?:intruder|unauthori[sz]ed\s+visitor)\b[^.!?\n]{0,80}"
        r"\b(?:school|classroom|campus|grounds?|compound)\b|"
        r"\b(?:orang\s+tidak\s+dikenali|pelawat\s+tanpa\s+kebenaran)\b"
        r"[^.!?\n]{0,80}\b(?:masuk|memasuki|berada\s+di)\b[^.!?\n]{0,80}"
        r"\b(?:sekolah|bilik\s+darjah|menjerit|enggan\s+keluar)\b",
        value,
    ))
    if intruder and not exercise_only:
        signals.update({
            "active_danger", "external_help_may_be_required",
            "evacuation_accountability", "minor_involved",
        })
        stakeholders.update({"school_leadership", "police"})
        escalate("safety_emergency", "critical", "ongoing")

    # Weapon threats, extortion and sexual-contact allegations involving a
    # pupil are safeguarding cases even when the report is not yet verified.
    weapon_coercion = bool(re.search(
        rf"\b{_MINOR}\b[^.!?\n]{{0,100}}\b(?:knife|weapon|blade)\b"
        r"[^.!?\n]{0,80}\b(?:threaten(?:ed|ing)?|demand(?:ed|ing)?|"
        r"money|rob(?:bed|bery)?|extort(?:ed|ion)?)\b|"
        r"\b(?:knife|weapon|blade)\b[^.!?\n]{0,100}"
        rf"\b{_MINOR}\b[^.!?\n]{{0,70}}\b(?:threaten|demand|money|extort)",
        value,
    ))
    sexual_safeguarding = bool(re.search(
        rf"\b{_MINOR}\b[^.!?\n]{{0,100}}\b(?:report(?:s|ed)?|said|says|"
        r"disclos(?:e|ed|ure)|told)\b[^.!?\n]{0,110}"
        r"\b(?:sexual\s+touching|touched\s+(?:him|her|them)?\s*"
        r"inappropriately|molest(?:ed|ation)?|sexual\s+abuse|sexual\s+assault)\b|"
        r"\b(?:teacher|staff|employee|coach|adult)\b[^.!?\n]{0,90}"
        r"\b(?:sexual\s+touching|touched\s+inappropriately|molest(?:ed|ation)?|"
        r"sexual\s+abuse|sexual\s+assault)\b|"
        r"\b(?:murid|pelajar)\b[^.!?\n]{0,90}\b(?:melapor(?:kan)?|berkata)\b"
        r"[^.!?\n]{0,100}\b(?:disentuh\s+secara\s+tidak\s+senonoh|"
        r"gangguan\s+seksual|dicabul)\b",
        value,
    ))
    if (weapon_coercion or sexual_safeguarding) and not exercise_only:
        signals.update({
            "safeguarding_concern", "minor_involved",
            "evidence_preservation_needed",
        })
        stakeholders.update({"guardian", "school_leadership"})
        if weapon_coercion:
            signals.add("external_help_may_be_required")
            stakeholders.add("police")
            out["suppress_signals"].add("financial_value_involved")
        escalate("safeguarding_welfare", "high", "just_occurred")

    # Observable clusters of illness are retained as a health incident even
    # when no food source is reported.
    illness_cluster = bool(re.search(
        r"\b(?:three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"tiga|empat|lima|enam|tujuh|lapan|sembilan|sepuluh|sebelas|"
        r"dua\s+belas|\d+)\s+(?:students?|pupils?|children|athletes?|"
        r"murid|pelajar)\b"
        r"[^.!?\n]{0,80}\b(?:fever|rash|vomit(?:ing|ed)?|collapsed?|"
        r"fainted?|difficulty\s+breathing|became\s+ill|fell\s+sick|demam|"
        r"ruam|muntah|pengsan|sukar\s+bernafas)\b",
        value,
    ))
    if illness_cluster and not exercise_only:
        signals.update({"injury_or_illness", "minor_involved"})
        stakeholders.update({"guardian", "medical_services", "school_leadership"})
        escalate("health_medical", "high", "just_occurred")

    severe_allergy = bool(re.search(
        rf"\b{_MINOR}\b[^.!?\n]{{0,90}}\b(?:allerg(?:y|ic)|anaphylaxis|"
        r"peanut|nut)\b[^.!?\n]{0,90}\b(?:difficulty\s+breathing|"
        r"cannot\s+breathe|can't\s+breathe|swelling|collapsed?|unconscious)\b|"
        r"\b(?:anaphylaxis|anaphylactic\s+shock)\b|"
        r"\b(?:murid|pelajar)\b[^.!?\n]{0,80}\b(?:alahan|kacang)\b"
        r"[^.!?\n]{0,80}\b(?:sukar\s+bernafas|bengkak|pengsan)\b",
        value,
    ))
    if severe_allergy and not exercise_only:
        signals.update({
            "injury_or_illness", "minor_involved", "active_danger",
            "external_help_may_be_required",
        })
        stakeholders.update({"guardian", "medical_services", "school_leadership"})
        escalate("health_medical", "critical", "ongoing")

    # Exposed/live electrical infrastructure on an occupied school route is a
    # present hazard, not a routine maintenance request.
    electrical_hazard = bool(re.search(
        r"\b(?:live|exposed|fallen|downed)\s+(?:electrical\s+)?"
        r"(?:wire|cable|power\s+line)\b[^.!?\n]{0,95}"
        r"\b(?:school|gate|compound|classroom|dismissal|pupils?|students?)\b|"
        r"\b(?:wire|cable|power\s+line)\b[^.!?\n]{0,60}"
        r"\b(?:fell|fallen|down|sparking|live|exposed)\b[^.!?\n]{0,70}"
        r"\b(?:school|gate|compound|classroom)\b|"
        r"\bwayar\s+elektrik\b[^.!?\n]{0,55}\b(?:hidup|terdedah|jatuh|"
        r"berpercik)\b[^.!?\n]{0,70}\b(?:sekolah|pagar|kantin|kelas)\b",
        value,
    ))
    if electrical_hazard and not exercise_only:
        signals.update({"active_danger", "external_help_may_be_required"})
        stakeholders.update({"school_leadership", "fire_and_rescue"})
        escalate("safety_emergency", "critical", "ongoing")

    # Existing online false-death/injury rumours require a privacy-safe holding
    # draft, but do not become proof that the rumoured event occurred.
    harmful_online_rumour = bool(re.search(
        r"\b(?:false|unverified)\s+(?:rumou?r|claim|post)\b[^.!?\n]{0,100}"
        rf"\b{_MINOR}\b[^.!?\n]{{0,60}}\b(?:died|dead|killed|injured)\b|"
        r"\b(?:rumou?r|claim|post)\b[^.!?\n]{0,80}\b(?:facebook|online|"
        r"social\s+media|viral)\b[^.!?\n]{0,100}\b(?:died|dead|killed)\b",
        value,
    ))
    if harmful_online_rumour:
        signals.update({"public_interest", "minor_involved"})
        stakeholders.update({"public_media", "school_leadership"})
        escalate("communications_reputation", "medium", "just_occurred")

    # Missing school money, exam-paper leakage and procurement inducements are
    # non-emergency integrity cases.  The floor preserves evidence and routes
    # to an internal governed record without assigning blame.
    legacy_missing_school_money = bool(re.search(
        r"\b(?:rm\s*)?\d[\d,]*(?:\.\d+)?\b[^.!?\n]{0,80}"
        r"\b(?:school|event|pta|pibg|collected|collection)\b[^.!?\n]{0,70}"
        r"\b(?:missing|unaccounted|cannot\s+be\s+found|shortfall)\b|"
        r"\b(?:school|event|pta|pibg)\s+(?:money|funds?|cash|collection)\b"
        r"[^.!?\n]{0,65}\b(?:missing|unaccounted|shortfall)\b",
        value,
    ))
    # Reconciliation failures are phrased in many ordinary ways and often do
    # not contain the word "missing".  Require both a school/event/fund anchor
    # and a financial object before accepting one of the closed discrepancy
    # predicates.  This keeps maths exercises, staffing shortages and requests
    # to perform routine reconciliation out of the incident floor.
    finance_context = bool(re.search(
        r"\b(?:school|event|activity|pta|pibg|charity\s+bazaar|bazaar|"
        r"fundrais(?:er|ing)|sekolah|acara|aktiviti|jualan\s+amal|"
        r"tabung)\b",
        value,
    )) and bool(re.search(
        r"\b(?:money|funds?|cash|accounts?|receipts?|collections?|sales|"
        r"coupons?|proceeds|takings|budget|ledger|wang|dana|tunai|akaun|"
        r"resit|kutipan|jualan|kupon|hasil|bajet)\b|\brm\s*\d",
        value,
    ))
    reconciliation_failure = bool(re.search(
        r"\b(?:cannot|can't|could\s+not|couldn't)\s+be\s+reconciled\b|"
        r"\b(?:does|do|did)\s+not\s+balance\b|"
        r"\bunreconciled\b|"
        r"\b(?:unexplained|unresolved|financial|cash|accounting)?\s*"
        r"discrepanc(?:y|ies)\b|"
        r"\b(?:accounts?|funds?|cash|collection|takings|proceeds)\b"
        r"[^.!?\n]{0,35}\b(?:is|are|was|were)?\s*short\s+by\b|"
        r"\btidak\s+dapat\s+(?:dipadankan|diselaraskan)\b|"
        r"\btidak\s+seimbang\b|"
        r"\b(?:wang|dana|tunai|akaun|kutipan|jualan|hasil)\b"
        r"[^.!?\n]{0,35}\bkurang\s+(?:sebanyak\s+)?rm\s*\d",
        value,
    ))
    discrepancy_resolved_or_negated = bool(re.search(
        r"\b(?:no|without)\s+(?:any\s+)?(?:unresolved\s+|unexplained\s+|"
        r"material\s+|financial\s+|cash\s+|accounting\s+)?"
        r"discrepanc(?:y|ies)\b|"
        r"\bdiscrepanc(?:y|ies)\b[^.!?\n]{0,30}"
        r"\b(?:resolved|cleared|closed)\b|"
        r"\b(?:not|no\s+longer)\s+short\s+by\b|"
        r"\b(?:telah|sudah)\s+(?:dipadankan|diselaraskan|seimbang)\b|"
        r"\btidak\s+kurang\s+(?:sebanyak\s+)?rm\s*\d",
        value,
    ))
    missing_school_money = bool(
        legacy_missing_school_money
        or (
            finance_context
            and reconciliation_failure
            and not discrepancy_resolved_or_negated
        )
    )
    procurement_inducement = bool(re.search(
        r"\b(?:supplier|vendor|contractor)\b[^.!?\n]{0,90}"
        r"\b(?:gift|cash|commission|kickback|bribe|reward)\b[^.!?\n]{0,90}"
        r"\b(?:purchase|procurement|contract|tender|influence|school)\b",
        value,
    ))
    exam_leak = bool(re.search(
        r"\b(?:exam|examination|test)\s+(?:paper|questions?|answers?)\b"
        r"[^.!?\n]{0,90}\b(?:leak(?:ed|age)?|shared|circulating|obtained)\b|"
        r"\b(?:leak(?:ed|age)?|circulating)\b[^.!?\n]{0,80}"
        r"\b(?:exam|examination|test)\s+(?:paper|questions?|answers?)\b",
        value,
    ))
    if missing_school_money or procurement_inducement:
        signals.update({"financial_value_involved", "evidence_preservation_needed"})
        out["suppress_signals"].add("event_operation")
        stakeholders.add("school_leadership")
        escalate("finance_procurement", "medium", "just_occurred")
    if exam_leak:
        signals.update({
            "official_record_involved", "evidence_preservation_needed",
            "possible_regulatory_trigger",
        })
        stakeholders.update({"school_leadership", "education_authority"})
        escalate("records_regulatory", "medium", "just_occurred")

    # Concrete ransomware state: systems/files are encrypted or locked and a
    # ransom is demanded.  Awareness material about ransomware does not match.
    ransomware_event = bool(re.search(
        r"\b(?:server|system|files?|records?|computers?|network)\b"
        r"[^.!?\n]{0,75}\b(?:encrypted|locked|inaccessible|unavailable)\b"
        r"[^.!?\n]{0,75}\b(?:ransomware|ransom|attacker|attack)\b|"
        r"\b(?:ransomware|ransom\s+note)\b[^.!?\n]{0,100}"
        r"\b(?:encrypted|locked|inaccessible|unavailable|detected|demanded)\b",
        value,
    ))
    if ransomware_event:
        signals.update({
            "data_security_incident", "evidence_preservation_needed",
            "service_disruption", "possible_regulatory_trigger",
        })
        stakeholders.update({"school_leadership", "education_authority"})
        out.update({"family": "cyber_data", "severity": "high", "phase": "ongoing"})

    # Rising flood water plus pupil movement/headcount is an active evacuation
    # accountability case, not a generic facilities memo.
    flood_event = bool(re.search(
        r"\b(?:flood|floodwater|flood\s+water|rising\s+water|flash\s+flood)\b"
        r"[^.!?\n]{0,100}\b(?:school|compound|classroom|hall|campus|pupils?|students?)\b|"
        r"\b(?:school|compound|classroom|hall|campus)\b[^.!?\n]{0,100}"
        r"\b(?:flood(?:ed|ing)?|floodwater|rising\s+water)\b",
        value,
    ))
    moving_or_counting = bool(re.search(
        r"\b(?:moving|moved|evacuat(?:e|ed|ing|ion)|assemble|headcount|"
        r"account(?:ed|ing)?\s+for|not\s+completed\s+(?:its\s+)?headcount)\b",
        value,
    ))
    if flood_event and moving_or_counting:
        signals.update({
            "active_danger", "evacuation_accountability", "minor_involved",
            "service_disruption", "external_help_may_be_required",
        })
        stakeholders.update({"school_leadership", "malaysia_emergency_services_999"})
        out.update({"family": "safety_emergency", "severity": "critical", "phase": "ongoing"})

    return out
