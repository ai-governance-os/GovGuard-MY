"""Reset the local demo to a pristine "first run" state.

Repeated demo/test runs pile up historical records — applied SOP proposals in
``state/curator_proposals.jsonl``, multiple distilled SOP skills in
``state/skills/``, task/session history, plan-cache + subject-confidence — which
clutter a judge-facing Curator / procedural-memory panel even though the runtime
correctly REUSES the active approved SOP. This script gives a one-command clean
slate for a rehearsal.

It first BACKS UP ``state/`` ``outputs/`` ``traces/`` (and the seeded workspace
result files) to a timestamped folder under ``backups/``, then clears them, then
recreates the empty directories. The server re-seeds everything it needs on the
next start, so the next run is the clean lifecycle: first SOP proposed -> owner
approves -> second run reuses it, with no pile of historical applied cards.

Stop the server first, then:

    python -X utf8 scripts/reset_demo_state.py            # prompts to confirm
    python -X utf8 scripts/reset_demo_state.py --yes      # no prompt
    python -X utf8 scripts/reset_demo_state.py --keep-memory   # preserve state/memory

Nothing here is shipped: ``backups/`` is git-ignored and the cleared dirs are
runtime-only (already git-ignored).
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_DIRS = ("state", "outputs", "traces")
_SEEDED_WORKSPACE = (
    "workspace/results.md",
    "workspace/national_athletics_results.md",
)


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def reset(root: Path, *, keep_memory: bool, stamp: str) -> Path:
    """Back up then clear the runtime state under `root`. Returns the backup
    folder. Pure given (root, stamp) so it is unit-testable against a temp dir."""
    backup = root / "backups" / f"demo_reset_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)

    # ---- back up (copy, never move — the originals are cleared next) --------
    for d in _RUNTIME_DIRS:
        src = root / d
        if src.exists():
            shutil.copytree(src, backup / d)
    for rel in _SEEDED_WORKSPACE:
        src = root / rel
        if src.exists():
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # ---- clear + recreate ---------------------------------------------------
    for d in _RUNTIME_DIRS:
        src = root / d
        if not src.exists():
            continue
        if d == "state":
            # Keep the dir; clear its children (optionally preserve memory/).
            for child in src.iterdir():
                if keep_memory and child.name == "memory":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            shutil.rmtree(src)
            src.mkdir(parents=True, exist_ok=True)
    for rel in _SEEDED_WORKSPACE:
        src = root / rel
        if src.exists():
            src.unlink()
    return backup


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset GovGuard local demo state")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    ap.add_argument("--keep-memory", action="store_true",
                    help="preserve state/memory (the personal-memory boundary)")
    ap.add_argument("--root", default=str(_DEFAULT_ROOT),
                    help="repo root to reset (default: this checkout)")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    present = [d for d in _RUNTIME_DIRS if (root / d).exists()]
    seeded = [p for p in _SEEDED_WORKSPACE if (root / p).exists()]
    if not present and not seeded:
        print(f"nothing to reset — {root} has no runtime state.")
        return 0

    print("This will BACK UP then CLEAR the local demo state:")
    for d in present:
        print(f"  - {d}/")
    for p in seeded:
        print(f"  - {p}")
    if args.keep_memory:
        print("  (preserving state/memory)")
    if not args.yes and not _confirm("Proceed? [y/N] "):
        print("aborted.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = reset(root, keep_memory=args.keep_memory, stamp=stamp)
    print(f"\ndone — demo state reset (backup at {backup}).")
    print("Restart the server and hard-refresh the browser for a clean run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
