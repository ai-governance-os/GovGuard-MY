"""
Mandatory safety preconditions for ANY tool that touches the filesystem
in a destructive way. These guards run before path resolution to prevent
empty-target / cwd-resolution bombs.

Three guarantees:
  1. Empty / whitespace-only target -> denied.
  2. Target that resolves to a workspace root itself (not a child) -> denied.
  3. Target outside the configured workspace roots -> denied.

These exist because Path("").resolve() returns the cwd. If the cwd happens
to be inside any workspace_root, the resulting path passes a naive
inside-root check, and a destructive op then wipes the cwd.
"""
from __future__ import annotations

from pathlib import Path

from ..util.path_guard import is_inside_any_root, resolve_safe


def safe_target_check(target: str, workspace_roots: list[str]) -> tuple[bool, str]:
    """Return (ok, reason). If ok==False, the caller MUST refuse the action."""
    if not target or not target.strip():
        return False, "empty_target"

    try:
        resolved = resolve_safe(target)
    except Exception as exc:
        return False, f"target_unresolvable:{exc}"

    if not is_inside_any_root(target, workspace_roots):
        return False, "out_of_workspace"

    # destructive ops on a workspace root itself are forbidden — only its
    # children are addressable. Compare the RESOLVED root paths.
    for root in workspace_roots:
        try:
            r = resolve_safe(root)
        except Exception:
            continue
        if resolved == r:
            return False, "target_is_workspace_root_itself"

    return True, "ok"
