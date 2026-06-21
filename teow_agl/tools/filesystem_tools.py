"""Filesystem tool handler — mechanical, ticket-gated."""
from __future__ import annotations

from pathlib import Path

from ..models import CandidateAction
from ..util.path_guard import resolve_safe
from ._safety import safe_target_check


_DESTRUCTIVE_OPS = ("delete_approved", "delete")
_WRITE_OPS = ("save_under_outputs", "save", "write", "create_file")
_READ_OPS = ("read_safe", "read")


class FilesystemTool:
    name = "fs"

    def __init__(self, workspace_roots: list[str]) -> None:
        self.roots = list(workspace_roots)

    def __call__(self, action: CandidateAction) -> dict:
        op = action.operation
        target = action.target
        if op in _WRITE_OPS:
            return self._write(target, action.metadata.get("content", ""))
        if op in _READ_OPS:
            return self._read(target)
        if op in ("list_files",):
            return self._list(target)
        if op in ("classify_files", "preview_deletion"):
            files = self._list(target).get("affected", [])
            return {"status": "success", "summary": f"previewed_{len(files)}", "affected": files}
        if op in _DESTRUCTIVE_OPS:
            return self._delete(target)
        return {"status": "skipped", "summary": f"unknown_fs_op:{op}", "affected": []}

    def _write(self, target: str, content: str) -> dict:
        ok, reason = safe_target_check(target, self.roots)
        if not ok:
            return _denied(reason)
        path = resolve_safe(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"status": "success", "summary": f"wrote {len(content)} chars", "affected": [str(path)]}

    def _read(self, target: str) -> dict:
        ok, reason = safe_target_check(target, self.roots)
        if not ok:
            return _denied(reason)
        path = resolve_safe(target)
        if not path.exists():
            return {"status": "failed", "summary": "not_found", "error": "missing", "affected": []}
        # Read text content. For binary office files, return a placeholder.
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"status": "success", "summary": "binary_file_skipped",
                    "affected": [str(path)], "content": ""}
        return {"status": "success", "summary": f"read_{len(text)}_chars",
                "affected": [str(path)], "content": text}

    def _list(self, target: str) -> dict:
        if not target or not target.strip():
            return _denied("empty_target")
        ok, reason = safe_target_check(target, self.roots)
        if not ok:
            # for listing, allow listing the workspace root itself
            if reason == "target_is_workspace_root_itself":
                pass
            else:
                return _denied(reason)
        path = resolve_safe(target)
        if not path.exists():
            return {"status": "success", "summary": "empty", "affected": []}
        if path.is_file():
            return {"status": "success", "summary": "single_file", "affected": [str(path)]}
        out = [str(c) for c in path.iterdir()]
        return {"status": "success", "summary": f"listed_{len(out)}", "affected": out}

    def _delete(self, target: str) -> dict:
        ok, reason = safe_target_check(target, self.roots)
        if not ok:
            return _denied(reason)
        path = resolve_safe(target)
        if not path.exists():
            return {"status": "success", "summary": "already_absent", "affected": []}
        affected = [str(path)]
        if path.is_file():
            path.unlink()
        else:
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            path.rmdir()
        return {"status": "success", "summary": "deleted", "affected": affected}


def _denied(reason: str) -> dict:
    return {"status": "denied", "summary": reason, "error": reason, "affected": []}
