"""Hard-safety policy reader: thin lookup over universal_hard_safety.json."""
from __future__ import annotations


def detect_red_pattern(text: str, hard_safety_cfg: dict) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for pat in hard_safety_cfg.get("hard_red_patterns", []):
        if pat.lower() in lowered:
            return pat
    return None


def is_hard_red_category(category: str, hard_safety_cfg: dict) -> bool:
    return category in set(hard_safety_cfg.get("hard_red_categories", []))


def hard_red_categories(hard_safety_cfg: dict) -> list[str]:
    return list(hard_safety_cfg.get("hard_red_categories", []))
