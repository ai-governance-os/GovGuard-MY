"""Module 101B — Action Risk."""
from __future__ import annotations

from typing import Any

from ..models import ActionRiskAssessment, CandidateAction
from ..policies.governance_profile import ProfileView


_RISK_LEVEL_ORDER = ["low", "medium", "high", "critical"]
_ROUTE_ORDER = ["BLUE", "GREEN", "RED"]
_BACKUP_VERIFIED_TOKEN = "verified"
_BACKUP_UNKNOWN_TOKEN = "unknown"


class ActionRiskModule:
    module_id = "101B"

    def __init__(
        self,
        *,
        risk_weights_cfg: dict,
        intake_classifier: dict,
        learned_policy: dict,
        subject_confidence=None,  # optional SubjectConfidence
    ) -> None:
        self.weights: dict[str, float] = dict(risk_weights_cfg.get("feature_weights", {}))
        self.level_boundaries: dict[str, float] = dict(risk_weights_cfg.get("risk_level_boundaries", {}))
        self.route_boundaries: dict[str, float] = dict(risk_weights_cfg.get("route_recommendation_boundaries", {}))
        self.classifier = intake_classifier
        self.learned = learned_policy
        self.subject_confidence = subject_confidence

    def assess(
        self,
        *,
        action: CandidateAction,
        profile: ProfileView,
        backup_status: str | None = None,
        signature_hint: str | None = None,
        task_category: str | None = None,
    ) -> ActionRiskAssessment:
        features = self._extract_features(action, profile, backup_status or profile.backup_default_status)

        # Subject confidence: agent has been here before and succeeded. This
        # is the "habit / muscle memory" signal — same category + N prior
        # successes + low rejection rate → confidence rises → risk drops.
        if self.subject_confidence is not None and task_category:
            try:
                if self.subject_confidence.is_confident(task_category):
                    features["subject_confident"] = True
            except Exception:
                pass

        if signature_hint:
            adj = self.learned.get("route_weight_adjustments", {}).get(signature_hint)
            if adj is not None:
                if float(adj) < 0:
                    features["repeated_approved_pattern"] = True
                elif float(adj) > 0:
                    features["repeated_rejected_pattern"] = True

        score = sum(self.weights.get(name, 0.0) for name, val in features.items() if val)

        risk_level = self._level_for(score)
        recommended_route = self._route_for(score)

        reasons = [f"feature:{n}" for n, v in features.items() if v]
        reasons.append(f"score:{score:.1f}")

        return ActionRiskAssessment(
            task_id=action.metadata.get("task_id", ""),
            action_id=action.action_id,
            risk_score=float(score),
            risk_level=risk_level,  # type: ignore[arg-type]
            features=features,
            recommended_route=recommended_route,  # type: ignore[arg-type]
            reasons=reasons,
        )

    def _extract_features(self, action: CandidateAction, profile: ProfileView, backup_status: str) -> dict[str, bool]:
        op = (action.operation or "").lower()
        target = action.target or ""
        # Path-shaped: contains a separator. Targets like email addresses
        # ('client@example.com') do not. Only path-shaped targets contribute
        # to filesystem features (in/out of workspace, sensitive paths, etc.).
        target_is_pathlike = bool(target) and ("/" in target or "\\" in target)

        destructive_ops = set(self.classifier.get("destructive_operations", []))
        external_ops = set(self.classifier.get("external_operations", []))
        shell_ops = set(self.classifier.get("shell_operations", []))
        code_ops = set(self.classifier.get("code_edit_operations", []))
        gui_ops = set(self.classifier.get("gui_operations", []))
        gui_safe_ops = set(self.classifier.get("gui_safe_operations", []))
        preview_ops = set(self.classifier.get("preview_safe_operations", []))

        is_destructive = any(d in op for d in destructive_ops)
        is_external = any(e in op for e in external_ops)
        is_shell = any(s in op for s in shell_ops)
        is_source_code = any(c in op for c in code_ops)
        # gui_automation: any GUI op that isn't on the safe-list (read-only)
        is_gui = op in gui_ops and op not in gui_safe_ops
        is_gui_keyboard = op in ("keyboard_type", "keyboard_hotkey")
        is_preview_safe = op in preview_ops

        is_high_value = target_is_pathlike and profile.is_high_value(target)
        is_sensitive = target_is_pathlike and profile.is_sensitive_path(target)
        is_safe_temp = target_is_pathlike and profile.is_safe_temp(target)
        is_safe_generated = target_is_pathlike and profile.is_safe_generated(target)
        out_of_workspace = target_is_pathlike and not profile.is_in_workspace(target)

        cred_kws = self.classifier.get("credential_path_keywords", [])
        is_credential = bool(target) and any(kw.lower() in target.lower() for kw in cred_kws)

        backup_unknown = (backup_status or "").lower() == _BACKUP_UNKNOWN_TOKEN
        backup_verified = _BACKUP_VERIFIED_TOKEN in (backup_status or "").lower()

        irreversible = action.reversibility == "low"
        bulk_operation = "bulk_operation" in action.risk_factors
        # Phase B3 — legal / patent draft scenario. The direct plan for
        # `patent_legal_draft` tasks marks its docx action with
        # risk_factors=["legal_content"]; combined with the
        # `legal_content_draft` symbolic action in action_taxonomy.json
        # and the same name in the profile's approval_required_actions,
        # this routes the action through GREEN (human review required).
        is_legal_content = "legal_content" in action.risk_factors
        # Public-school: a parent/guardian notice DRAFT carries
        # risk_factors=["parent_notice"]; with `parent_notice_broadcast`
        # in action_taxonomy.json and the same name in the active pack's
        # approval_required_actions, this routes the draft through GREEN
        # (educator review required before any release).
        is_parent_notice = "parent_notice" in action.risk_factors
        # Public-school: a proposed change to a PDPA-protected student record
        # (attendance / discipline) carries risk_factors=["student_record_change"];
        # with `student_record_modification` / `student_attendance_update` in
        # action_taxonomy.json and the active pack's approval_required_actions,
        # this routes the change through GREEN (educator approval required).
        is_student_record_change = "student_record_change" in action.risk_factors

        return {
            "destructive": is_destructive,
            "irreversible": irreversible,
            "external_facing": is_external,
            "source_code": is_source_code,
            "high_value_asset": is_high_value,
            "sensitive_path": is_sensitive and not is_credential,
            "credential_path": is_credential,
            "out_of_workspace": out_of_workspace,
            "bulk_operation": bulk_operation,
            "backup_unknown": backup_unknown and (is_destructive or is_external),
            "generated_output": is_safe_generated,
            "temp_or_cache": is_safe_temp,
            "repeated_approved_pattern": False,
            "repeated_rejected_pattern": False,
            "preview_safe_operation": is_preview_safe,
            "shell_operation": is_shell,
            "gui_automation": is_gui,
            "gui_keyboard": is_gui_keyboard,
            "backup_verified": backup_verified,
            "legal_content": is_legal_content,
            "parent_notice": is_parent_notice,
            "student_record_change": is_student_record_change,
        }

    def _level_for(self, score: float) -> str:
        return _bucket(score, self.level_boundaries, _RISK_LEVEL_ORDER, default="critical")

    def _route_for(self, score: float) -> str:
        return _bucket(score, self.route_boundaries, _ROUTE_ORDER, default="RED")


def _bucket(score: float, boundaries: dict[str, float], order: list[str], default: str) -> str:
    for key in order:
        if score <= float(boundaries.get(key, float("inf"))):
            return key
    return default
