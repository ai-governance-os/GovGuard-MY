"""Domain pack activation and strengthening-only merge helpers.

Domain packs adapt the agent for a professional context (legal, medical,
finance, etc.) without changing the constitution. They may add approvals,
rubrics, and learning exclusions; they may not remove or weaken base rules.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_DOMAIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_GOVERNANCE_ADD_KEYS = {
    "approval_required_actions_add": "approval_required_actions",
    "hard_block_actions_add": "hard_block_actions",
    "high_value_assets_add": "high_value_assets",
    "sensitive_patterns_add": "sensitive_patterns",
    "infeasibility_actions_add": "infeasibility_actions",
}
_IGNORED_OVERLAY_KEYS = {
    "_purpose",
    "notes",
    "policy_version",
    "domain",
    "domain_pack_version",
    # Human-facing metadata only; never consumed by governance logic. Listed
    # here so a pack may carry a friendly name without the additive-only
    # enforcement (_reject_remove_keys / unknown-add-key check) rejecting it.
    "display_name",
}


@dataclass
class DomainPack:
    name: str
    root: Path
    governance_overlay: dict = field(default_factory=dict)
    approval_templates: dict = field(default_factory=dict)
    verifier_rubrics: dict = field(default_factory=dict)
    learning_exclusions: dict = field(default_factory=dict)
    seed_skill_manifest: dict = field(default_factory=dict)
    planner_guidance: dict = field(default_factory=dict)

    @property
    def version(self) -> str:
        for data in (
            self.governance_overlay,
            self.approval_templates,
            self.verifier_rubrics,
            self.learning_exclusions,
            self.seed_skill_manifest,
            self.planner_guidance,
        ):
            version = data.get("policy_version") or data.get("domain_pack_version")
            if version:
                return str(version)
        return "unversioned"


def load_domain_pack(config_dir: str | Path, name: str | None) -> DomainPack | None:
    """Load a named domain pack from configs/domain_packs/<name>.

    None or empty string means "no domain pack". Names are intentionally
    restricted to simple identifiers to avoid path traversal.
    """
    if not name:
        return None
    if not _DOMAIN_NAME_RE.match(name):
        raise ValueError(f"invalid domain pack name: {name!r}")

    root = Path(config_dir) / "domain_packs" / name
    if not root.is_dir():
        raise FileNotFoundError(f"domain pack not found: {name}")

    return DomainPack(
        name=name,
        root=root,
        governance_overlay=_read_optional_json(root / "governance_profile_overlay.json"),
        approval_templates=_read_optional_json(root / "approval_templates.json"),
        verifier_rubrics=_read_optional_json(root / "verifier_rubrics.json"),
        learning_exclusions=_read_optional_json(root / "learning_exclusions.json"),
        seed_skill_manifest=_read_optional_json(root / "seed_skill_manifest.json"),
        planner_guidance=_read_optional_json(root / "planner_guidance.json"),
    )


def apply_domain_pack_to_profile(profile: dict, pack: DomainPack | None) -> dict:
    """Return a profile copy with strengthening-only overlay applied."""
    merged = copy.deepcopy(profile or {})
    if pack is None:
        return merged

    overlay = pack.governance_overlay or {}
    _reject_remove_keys(overlay, source=f"domain_pack:{pack.name}:governance")

    unknown = [
        key for key in overlay
        if key not in _ALLOWED_GOVERNANCE_ADD_KEYS
        and key not in _IGNORED_OVERLAY_KEYS
    ]
    if unknown:
        raise ValueError(
            f"unsupported governance overlay keys in {pack.name}: {unknown}"
        )

    applied: dict[str, list[str]] = {}
    for add_key, base_key in _ALLOWED_GOVERNANCE_ADD_KEYS.items():
        additions = _string_list(overlay.get(add_key))
        if additions:
            _append_unique(merged, base_key, additions)
            applied[add_key] = additions

    active = _string_list(merged.get("active_domain_packs"))
    if pack.name not in active:
        active.append(pack.name)
    merged["active_domain_packs"] = active

    overlays = dict(merged.get("domain_pack_overlays") or {})
    overlays[pack.name] = {
        "version": pack.version,
        "governance_additions": applied,
    }
    merged["domain_pack_overlays"] = overlays

    base_version = str(merged.get("profile_version") or "unknown")
    domain_suffix = f"+domain:{pack.name}@{pack.version}"
    if domain_suffix not in base_version:
        merged["profile_version"] = base_version + domain_suffix

    return merged


def merge_approval_templates(base: dict, pack: DomainPack | None) -> dict:
    """Merge domain approval card templates before the base fallback."""
    merged = copy.deepcopy(base or {})
    if pack is None or not pack.approval_templates:
        return merged

    _reject_remove_keys(pack.approval_templates, source=f"domain_pack:{pack.name}:approval")
    templates = list(merged.get("templates") or [])
    existing_ids = {str(t.get("id")) for t in templates if isinstance(t, dict)}
    for template in pack.approval_templates.get("templates", []) or []:
        if not isinstance(template, dict):
            continue
        template_id = str(template.get("id") or "").strip()
        if template_id and template_id not in existing_ids:
            templates.append(copy.deepcopy(template))
            existing_ids.add(template_id)
    merged["templates"] = templates
    _stamp_pack_metadata(merged, "approval_template_domain_packs", pack)
    return merged


def merge_verifier_rules(base: dict, pack: DomainPack | None) -> dict:
    """Merge verifier scenario overlays from a domain pack."""
    merged = copy.deepcopy(base or {})
    if pack is None or not pack.verifier_rubrics:
        return merged

    _reject_remove_keys(pack.verifier_rubrics, source=f"domain_pack:{pack.name}:verifier")
    overlays = pack.verifier_rubrics.get("verifier_rule_overlays") or {}
    if overlays:
        merged = _deep_merge_additive(merged, overlays)
    _stamp_pack_metadata(merged, "verifier_domain_packs", pack)
    return merged


def merge_judge_rubrics(base: dict, pack: DomainPack | None) -> dict:
    """Merge LLM judge rubrics supplied by a domain pack."""
    merged = copy.deepcopy(base or {})
    if pack is None or not pack.verifier_rubrics:
        return merged

    _reject_remove_keys(pack.verifier_rubrics, source=f"domain_pack:{pack.name}:rubrics")
    rubrics = pack.verifier_rubrics.get("judge_rubrics") or {}
    for category, rubric in rubrics.items():
        if category in merged:
            continue
        if isinstance(rubric, dict):
            merged[category] = copy.deepcopy(rubric)
    _stamp_pack_metadata(merged, "judge_rubric_domain_packs", pack)
    return merged


def merge_learning_exclusions(base: dict, pack: DomainPack | None) -> dict:
    """Merge positive-learning exclusions from a domain pack."""
    merged = copy.deepcopy(base or {})
    if pack is None or not pack.learning_exclusions:
        return merged

    data = pack.learning_exclusions
    _reject_remove_keys(data, source=f"domain_pack:{pack.name}:learning")

    for key in (
        "exclude_conversation_acts_add",
        "exclude_task_outcomes_add",
        "exclude_content_patterns_add",
    ):
        target_key = key[:-4]
        additions = _string_list(data.get(key))
        if additions:
            _append_unique(merged, target_key, additions)

    domains = dict(merged.get("domain_pack_exclusions") or {})
    domains[pack.name] = {
        "version": pack.version,
        "exclude": _string_list(data.get("exclude")),
        "notes": data.get("notes", ""),
    }
    merged["domain_pack_exclusions"] = domains
    _stamp_pack_metadata(merged, "learning_exclusion_domain_packs", pack)
    return merged


def domain_context_for_brief(pack: DomainPack | None) -> dict:
    """Small, bounded domain context injected into planner briefs/traces."""
    if pack is None:
        return {}

    guidance = pack.planner_guidance or {}
    overlay = pack.governance_overlay or {}
    approvals = pack.approval_templates or {}
    rubrics = pack.verifier_rubrics or {}
    learning = pack.learning_exclusions or {}

    return {
        "active": True,
        "domain": pack.name,
        "version": pack.version,
        "planner_guidance": {
            "summary": str(guidance.get("summary") or "")[:500],
            "must_do": _string_list(guidance.get("must_do"))[:12],
            "must_not_do": _string_list(guidance.get("must_not_do"))[:12],
            "review_boundary": str(guidance.get("review_boundary") or "")[:400],
            "learning_boundary": str(guidance.get("learning_boundary") or "")[:400],
        },
        "governance_additions": {
            key: _string_list(overlay.get(key))[:12]
            for key in sorted(_ALLOWED_GOVERNANCE_ADD_KEYS)
            if overlay.get(key)
        },
        "approval_template_ids": [
            str(t.get("id"))
            for t in approvals.get("templates", []) or []
            if isinstance(t, dict) and t.get("id")
        ][:12],
        "judge_categories": sorted(
            str(k) for k in (rubrics.get("judge_rubrics") or {}).keys()
        )[:12],
        "learning_exclusions": {
            "exclude": _string_list(learning.get("exclude"))[:12],
            "content_patterns": _string_list(
                learning.get("exclude_content_patterns_add")
            )[:12],
        },
    }


def _read_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"domain pack file must be a JSON object: {path}")
    return data


def _reject_remove_keys(data: Any, *, source: str) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).endswith("_remove"):
                raise ValueError(f"{source} attempted weakening key: {key}")
            _reject_remove_keys(value, source=source)
    elif isinstance(data, list):
        for item in data:
            _reject_remove_keys(item, source=source)


def _deep_merge_additive(base: dict, overlay: dict) -> dict:
    merged = copy.deepcopy(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_additive(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            seen = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in merged[key]}
            for item in value:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker not in seen:
                    merged[key].append(copy.deepcopy(item))
                    seen.add(marker)
        elif key not in merged:
            merged[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_additive(merged[key], value)
    return merged


def _append_unique(data: dict, key: str, additions: list[str]) -> None:
    values = _string_list(data.get(key))
    for item in additions:
        if item not in values:
            values.append(item)
    data[key] = values


def _stamp_pack_metadata(data: dict, key: str, pack: DomainPack) -> None:
    packs = _string_list(data.get(key))
    stamp = f"{pack.name}@{pack.version}"
    if stamp not in packs:
        packs.append(stamp)
    data[key] = packs


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
