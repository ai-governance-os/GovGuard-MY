"""Desktop file-control tool — with safety guard."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..models import CandidateAction
from ..util.path_guard import resolve_safe
from ._safety import safe_target_check


def get_desktop_path() -> Path:
    home = Path.home()
    candidates = [home / "Desktop", home / "OneDrive" / "Desktop", home / "OneDrive - Personal" / "Desktop"]
    for c in candidates:
        if c.exists():
            return c
    return home / "Desktop"


class DesktopTool:
    name = "desktop"

    def __init__(self, workspace_roots: list[str]) -> None:
        self.roots = list(workspace_roots)

    def __call__(self, action: CandidateAction) -> dict:
        op = action.operation
        if op == "list_desktop":
            return self._list(str(get_desktop_path()))
        if op == "list_dir":
            return self._list(action.target or str(get_desktop_path()))
        if op == "make_folder":
            return self._make_folder(action)
        if op in ("move_file", "move"):
            return self._move(action)
        if op == "open_path":
            return self._open(action.target)
        if op in ("delete_file", "delete"):
            return self._delete(action.target)
        return {"status": "skipped", "summary": f"unknown_desktop_op:{op}", "affected": []}

    def _list(self, path: str) -> dict:
        if not path or not path.strip():
            return _denied("empty_target")
        ok, reason = safe_target_check(path, self.roots)
        if not ok and reason != "target_is_workspace_root_itself":
            return _denied(reason)
        p = resolve_safe(path)
        if not p.exists():
            return {"status": "success", "summary": "empty", "affected": []}
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        rows = [
            {"name": x.name, "kind": "file" if x.is_file() else "folder",
             "size": x.stat().st_size if x.is_file() else 0,
             "path": str(x)}
            for x in items
        ]
        return {"status": "success", "summary": f"listed {len(rows)} items",
                "affected": [str(x['path']) for x in rows], "rows": rows}

    def _make_folder(self, action: CandidateAction) -> dict:
        meta = action.metadata or {}
        parent = action.target or str(get_desktop_path())
        name = meta.get("name") or meta.get("folder_name") or "new_folder"
        ok, reason = safe_target_check(parent, self.roots)
        if not ok and reason != "target_is_workspace_root_itself":
            return _denied(reason)
        new_dir = resolve_safe(parent) / str(name)
        new_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "success", "summary": f"created_folder:{new_dir.name}", "affected": [str(new_dir)]}

    def _move(self, action: CandidateAction) -> dict:
        meta = action.metadata or {}
        src = meta.get("src") or action.target
        dst = meta.get("dst") or meta.get("destination")
        if not src or not dst:
            return _failed("missing src or dst in metadata")
        ok_s, rs = safe_target_check(src, self.roots)
        if not ok_s:
            return _denied(f"src:{rs}")
        ok_d, rd = safe_target_check(dst, self.roots)
        if not ok_d and rd != "target_is_workspace_root_itself":
            return _denied(f"dst:{rd}")
        src_p = resolve_safe(src)
        dst_p = resolve_safe(dst)
        if dst_p.is_dir():
            dst_p = dst_p / src_p.name
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_p), str(dst_p))
        return {"status": "success", "summary": f"moved:{src_p.name}->{dst_p}",
                "affected": [str(src_p), str(dst_p)]}

    def _open(self, path: str) -> dict:
        ok, reason = safe_target_check(path, self.roots)
        if not ok:
            return _denied(reason)
        p = resolve_safe(path)
        if not p.exists():
            return _failed(f"not_found:{p}")
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                import subprocess
                opener = "open" if shutil.which("open") else "xdg-open"
                subprocess.Popen([opener, str(p)])
        except Exception as exc:
            return _failed(f"open_failed:{exc}")
        return {"status": "success", "summary": f"opened:{p.name}", "affected": [str(p)]}

    def _delete(self, path: str) -> dict:
        ok, reason = safe_target_check(path, self.roots)
        if not ok:
            return _denied(reason)
        p = resolve_safe(path)
        if not p.exists():
            return {"status": "success", "summary": "already_absent", "affected": []}
        if p.is_file():
            p.unlink()
        else:
            shutil.rmtree(str(p))
        return {"status": "success", "summary": f"deleted:{p.name}", "affected": [str(p)]}


def _denied(reason: str) -> dict:
    return {"status": "denied", "summary": reason, "affected": [], "error": reason}


def _failed(reason: str) -> dict:
    return {"status": "failed", "summary": reason, "affected": [], "error": reason}
