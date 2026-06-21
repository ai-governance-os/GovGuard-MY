"""
Pure pattern-matching helpers. No governance decisions are made here.
Patterns come exclusively from caller-supplied lists, never hardcoded.

Ported from TEOW-AGL 10.7.1 with no logic change.

SAFETY: empty-target paths must be rejected by the CALLER. This module
does not invent a policy from an empty input — it just resolves what
it's given. `Path('').resolve()` returns the current directory, which
can lead to dangerous downstream operations if the caller does not
guard against empty input.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable


def normalize(path: str) -> str:
    return str(path).replace("\\", "/").lower()


def resolve_safe(path: str) -> Path:
    return Path(path).expanduser().resolve()


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    s = normalize(path)
    return any(fnmatch(s, p.lower()) for p in patterns)


def is_inside_any_root(path: str, roots: Iterable[str]) -> bool:
    if not path:
        return False  # explicit: empty target is never "inside" anything
    p = resolve_safe(path)
    for root in roots:
        try:
            r = resolve_safe(root)
        except Exception:
            continue
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False
