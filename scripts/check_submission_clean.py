"""Physical clean-package checker for a RELEASE folder (submission review P2).

`verify_no_secrets.py` guards the git-tracked surface; this script guards the
extracted RELEASE folder itself — it FAILS if any local-only artifact is
physically present (a venv, runtime state, caches, git internals, env files).
Run it against the extracted submission folder before uploading:

    python -X utf8 scripts/check_submission_clean.py <release-folder>

Exit code 0 = clean; 1 = blocked entries found (each is printed).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Directory / file names that must NOT exist anywhere in a release folder.
BLOCKED_DIRS = (
    ".venv", "venv", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "__pycache__", "state", "outputs", "traces", "backups", "node_modules",
    ".claude", ".idea", ".vscode",
)
BLOCKED_FILE_GLOBS = (
    ".env", ".env.*", "*.key", "*.pem", "*.p12", "*.pfx",
    "*.db", "*.sqlite", "*.sqlite3", "*.log",
)
ALLOWED_FILES = {".env.example"}


def check(root: Path) -> list[str]:
    bad: list[str] = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_dir() and p.name in BLOCKED_DIRS:
            bad.append(f"dir  {rel}/")
        elif p.is_file() and p.name not in ALLOWED_FILES:
            if any(p.match(g) for g in BLOCKED_FILE_GLOBS):
                bad.append(f"file {rel}")
        # egg-info directories (any name)
        if p.is_dir() and p.name.endswith(".egg-info"):
            bad.append(f"dir  {rel}/")
    return sorted(set(bad))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"FAIL: not a directory: {root}")
        return 2
    bad = check(root)
    if bad:
        print(f"FAIL: {len(bad)} local-only artifact(s) in the release folder:")
        for b in bad:
            print("  ", b)
        return 1
    print("PASS: release folder is physically clean "
          "(no venv/state/outputs/traces/caches/git/env/keys).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
