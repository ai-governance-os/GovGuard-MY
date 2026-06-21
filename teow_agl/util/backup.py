"""Backup / restore for the learning-state and audit assets (Phase C).

`state/` holds ALL six learning layers (plan cache, subject confidence,
USER.md/MEMORY.md, FTS5 session index, skills) — the deployment's most
valuable and least reproducible asset. `traces/` is the audit chain.
`configs/` is the governance policy as deployed. One zip, timestamped,
rotated.

Restore extracts only whitelisted top-level directories and refuses
path-traversal entries (zip-slip), so a tampered archive cannot write
outside the target root.
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

DEFAULT_ITEMS = ("state", "traces", "configs")
_PREFIX = "teow_backup_"


def create_backup(
    root: str | Path,
    *,
    out_dir: str | Path | None = None,
    items: tuple[str, ...] = DEFAULT_ITEMS,
    keep: int = 14,
) -> Path | None:
    """Zip `items` under `root` into out_dir (default root/backups).

    Returns the archive path, or None when nothing existed to back up.
    Keeps the newest `keep` archives, deletes older ones (rotation).
    """
    root = Path(root)
    out = Path(out_dir) if out_dir is not None else root / "backups"
    out.mkdir(parents=True, exist_ok=True)

    sources = [(name, root / name) for name in items if (root / name).is_dir()]
    if not sources:
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = out / f"{_PREFIX}{stamp}.zip"
    # Same-second collision guard — never silently overwrite an archive.
    counter = 1
    while archive.exists():
        archive = out / f"{_PREFIX}{stamp}_{counter}.zip"
        counter += 1
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, src in sources:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    zf.write(f, arcname=f"{name}/{f.relative_to(src)}")

    # Rotation — newest `keep` survive. Sort by mtime (NOT name: the
    # same-second collision suffix breaks lexical ordering) and never
    # delete the archive we just wrote.
    if keep > 0:
        archives = sorted(out.glob(f"{_PREFIX}*.zip"),
                          key=lambda p: p.stat().st_mtime)
        for old in archives[:-keep]:
            if old == archive:
                continue
            try:
                old.unlink()
            except OSError:
                pass
    return archive


def restore_backup(
    archive: str | Path,
    root: str | Path,
    *,
    items: tuple[str, ...] = ("state", "traces"),
) -> list[str]:
    """Extract whitelisted `items` from `archive` into `root`,
    overwriting existing files. Returns the list of restored paths
    (relative). Entries outside the whitelist or attempting path
    traversal are skipped.

    configs/ is deliberately NOT in the default whitelist — restoring
    governance policy should be an explicit operator decision
    (pass items=("state", "traces", "configs")).
    """
    archive = Path(archive)
    root = Path(root).resolve()
    restored: list[str] = []
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            top = name.split("/", 1)[0]
            if top not in items:
                continue
            target = (root / name).resolve()
            # zip-slip guard: the resolved target must stay under root.
            try:
                target.relative_to(root)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            restored.append(name)
    return restored


def list_backups(root: str | Path,
                 out_dir: str | Path | None = None) -> list[Path]:
    out = Path(out_dir) if out_dir is not None else Path(root) / "backups"
    if not out.is_dir():
        return []
    return sorted(out.glob(f"{_PREFIX}*.zip"))
