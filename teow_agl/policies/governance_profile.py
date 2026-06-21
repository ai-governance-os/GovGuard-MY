"""Governance profile reader. Pure data accessors; no governance literals."""
from __future__ import annotations

from ..util.path_guard import is_inside_any_root, matches_any


class ProfileView:
    def __init__(self, profile: dict) -> None:
        self.profile = profile

    @property
    def role_context(self) -> str:
        return self.profile.get("role_context", "unknown")

    @property
    def workspace_roots(self) -> list[str]:
        return list(self.profile.get("workspace_roots", []))

    @property
    def high_value_assets(self) -> list[str]:
        return list(self.profile.get("high_value_assets", []))

    @property
    def sensitive_patterns(self) -> list[str]:
        return list(self.profile.get("sensitive_patterns", []))

    @property
    def safe_generated_patterns(self) -> list[str]:
        return list(self.profile.get("safe_generated_patterns", []))

    @property
    def safe_temp_patterns(self) -> list[str]:
        return list(self.profile.get("safe_temp_patterns", []))

    @property
    def approval_required_actions(self) -> list[str]:
        return list(self.profile.get("approval_required_actions", []))

    @property
    def hard_block_actions(self) -> list[str]:
        return list(self.profile.get("hard_block_actions", []))

    @property
    def external_send_policy(self) -> str:
        return self.profile.get("external_send_policy", "")

    @property
    def source_code_policy(self) -> str:
        return self.profile.get("source_code_policy", "")

    @property
    def default_delete_policy(self) -> str:
        return self.profile.get("default_delete_policy", "")

    @property
    def backup_default_status(self) -> str:
        return self.profile.get("backup_policy", {}).get("default_status", "unknown")

    @property
    def allow_destructive_auto_only_if_backup_verified(self) -> bool:
        return bool(self.profile.get("backup_policy", {}).get("allow_destructive_auto_only_if_backup_verified", True))

    @property
    def learning_constraints(self) -> dict:
        return dict(self.profile.get("learning_constraints", {}))

    @property
    def max_auto_downgrade(self) -> dict:
        return dict(self.profile.get("max_auto_downgrade", {}))

    def is_high_value(self, target: str) -> bool:
        return matches_any(target, self.high_value_assets)

    def is_sensitive_path(self, target: str) -> bool:
        return matches_any(target, self.sensitive_patterns)

    def is_safe_generated(self, target: str) -> bool:
        return matches_any(target, self.safe_generated_patterns)

    def is_safe_temp(self, target: str) -> bool:
        return matches_any(target, self.safe_temp_patterns)

    def is_in_workspace(self, target: str) -> bool:
        return is_inside_any_root(target, self.workspace_roots)
