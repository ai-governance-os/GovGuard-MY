"""All policy/profile/safety files are loaded from JSON only."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@dataclass
class LoadedConfig:
    config_dir: Path
    governance_profile: dict = field(default_factory=dict)
    universal_hard_safety: dict = field(default_factory=dict)
    learned_contextual_policy: dict = field(default_factory=dict)
    model_behavior_profile: dict = field(default_factory=dict)
    intake_classifier: dict = field(default_factory=dict)

    @property
    def governance_profile_version(self) -> str:
        return self.governance_profile.get("profile_version", "unknown")

    @property
    def hard_safety_version(self) -> str:
        return self.universal_hard_safety.get("policy_version", "unknown")

    @property
    def learned_policy_version(self) -> str:
        return self.learned_contextual_policy.get("policy_version", "unknown")

    def policy_version(self) -> str:
        return f"{self.governance_profile_version}+{self.hard_safety_version}+{self.learned_policy_version}"


def load_config(
    config_dir: str | Path,
    *,
    profile_filename: str = "default_user_governance_profile.json",
    learned_filename: str = "learned_contextual_policy.json",
    model_behavior_filename: str = "model_behavior_profile.json",
    hard_safety_filename: str = "universal_hard_safety.json",
    intake_classifier_filename: str = "intake_classifier.json",
) -> LoadedConfig:
    base = Path(config_dir)
    cfg = LoadedConfig(config_dir=base)
    cfg.governance_profile = _read_json(base / profile_filename)
    cfg.universal_hard_safety = _read_json(base / hard_safety_filename)
    cfg.learned_contextual_policy = _read_json(base / learned_filename)
    cfg.model_behavior_profile = _read_json(base / model_behavior_filename)
    cfg.intake_classifier = _read_json(base / intake_classifier_filename)
    return cfg


def save_learned_policy(config_dir: str | Path, data: dict, learned_filename: str = "learned_contextual_policy.json") -> None:
    _write_json(Path(config_dir) / learned_filename, data)


def save_model_behavior(config_dir: str | Path, data: dict, filename: str = "model_behavior_profile.json") -> None:
    _write_json(Path(config_dir) / filename, data)
