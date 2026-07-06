"""Sanitize the build into an IP-minimal, secret-free public surface.

Removes (Task 7):
  * runtime-generated state / artifacts: outputs/, traces/, state*/, backups/,
    workspace/uploads, workspace/temp, *.db, *.sqlite*, *.log
  * secrets & key material: .env / .env.* (keeps .env.example), *.key, *.pem,
    *.p12, *.pfx
  * caches / editor / OS junk: __pycache__, .pytest_cache, .mypy_cache,
    .ruff_cache, .claude, .DS_Store, Thumbs.db
  * obsolete internal manuscripts / dev reports not needed for MAIC judging
    (IP-minimal scoping, §2 keep/cut) — listed in OBSOLETE_DOCS below

NOT removed: `.venv`, `.git`, `node_modules` (PRUNE_DIRS) are deliberately left
untouched — they are needed locally and are gitignored, so they never reach the
published surface (releases are produced with `git archive`, which ships
tracked files only). To VERIFY a release folder is physically clean, run
`python -X utf8 scripts/check_submission_clean.py <release-folder>`.

Usage:
    python -X utf8 scripts/sanitize_build.py [--root .] [--dry-run]

Idempotent: safe to run repeatedly. Run it before publishing, then run
scripts/verify_no_secrets.py to confirm the surface is clean.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
from pathlib import Path

# Directories pruned from the walk: never descended into, never removed.
# They are gitignored and needed locally (the virtualenv) — they never reach
# the published surface, so leave them untouched.
PRUNE_DIRS = (".venv", ".git", "node_modules")
# Directory NAMES removed anywhere in the tree.
BLOCKED_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "outputs", "traces", "backups", ".claude", "client_exports",
}
# Directory name GLOBS removed anywhere in the tree.
BLOCKED_DIR_GLOBS = ("state*", "*.egg-info")
# Specific nested dirs (relative to root) removed if present.
BLOCKED_RELDIRS = ("workspace/uploads", "workspace/temp")
# File GLOBS removed anywhere in the tree.
BLOCKED_FILE_GLOBS = (
    ".env", ".env.*", "*.db", "*.sqlite", "*.sqlite3", "*.log",
    "*.key", "*.pem", "*.p12", "*.pfx", ".DS_Store", "Thumbs.db",
)
# Never remove these even if a glob would match.
KEEP_FILES = {".env.example"}

# Obsolete internal manuscripts / dev reports (root-level) — not for judging.
OBSOLETE_DOCS = (
    "AGENT_DEMO_TEST_BRIEF.md",          # contained a hard-coded GROQ key
    "AGENT_COMMON_SENSE_ENGINEERING_BLUEPRINT.md",
    "BUG_FIXES_LOG.md", "NEXT_PHASES.md", "PHASE_PLAN.md", "PROJECT_REPORT.md",
    "SANDBOX_PLAN.md", "INTAKE_UNDERSTANDING_REDESIGN.md", "design-qa.md",
    "GOVERNANCE_GREEN_CATALOG.md", "TEOW_AGL_FOR_AI_REVIEWERS.md",
    "LEARNING_SYSTEM_ROADMAP.md", "LEARNING_SYSTEM_ROADMAP_v2.md",
    "PHASE_1A_COMPLETION_REPORT.md", "PHASE_A_FIX_REPORT.md", "PHASE_B_FIX_REPORT.md",
    "PHASE_AB_PATCH_REPORT.md", "PHASE_AB_QPATCH_REPORT.md", "PHASE_AB_RPATCH_REPORT.md",
    "PHASE_AB_SPATCH_REPORT.md", "PHASE_AB_TPATCH_REPORT.md",
    "SKILL_LEARNING_PHASE_1_2_REPORT.md", "SKILL_LEARNING_UI_TEST_PLAYBOOK.md",
    "SKILL_LEARNING_UI_TEST_REPORT.md", "SKILL_LEVEL_3_RESULTS.md",
    # Long-form research manuscripts (sensitive; out of the IP-minimal surface).
    "TEOW-AGL_Cognitive_Governance_Manifesto_CN.docx",
    "TEOW-AGL_Governance_Learning_Architecture_CN.docx",
)


def _match_glob(name: str, globs) -> bool:
    return any(fnmatch.fnmatch(name, g) for g in globs)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sanitize build to IP-minimal surface")
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    removed: list[str] = []

    def rm(path: Path) -> None:
        rel = path.relative_to(root)
        removed.append(str(rel))
        if args.dry_run:
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except OSError:
                pass

    # Obsolete docs + specific nested dirs (root-relative).
    for name in OBSOLETE_DOCS:
        p = root / name
        if p.exists():
            rm(p)
    for reldir in BLOCKED_RELDIRS:
        p = root / reldir
        if p.is_dir():
            rm(p)

    # Walk top-down, pruning PRUNE_DIRS so we never descend into the venv/git.
    blocked_dirs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dp = Path(dirpath)
        keep = []
        for d in dirnames:
            if d in PRUNE_DIRS:
                continue  # prune: don't descend, don't remove
            if d in BLOCKED_DIR_NAMES or _match_glob(d, BLOCKED_DIR_GLOBS):
                blocked_dirs.append(dp / d)
                continue  # don't descend into a dir we're about to remove
            keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            if f in KEEP_FILES:
                continue
            if _match_glob(f, BLOCKED_FILE_GLOBS):
                rm(dp / f)
    for d in blocked_dirs:
        if d.exists():
            rm(d)

    verb = "Would remove" if args.dry_run else "Removed"
    print(f"{verb} {len(removed)} item(s):")
    for r in removed:
        print(f"  - {r}")
    if not removed:
        print("  (nothing to remove — surface already clean)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
