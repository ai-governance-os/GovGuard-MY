"""Fail if the public surface contains a secret or a blocked file (Task 7).

Design note — why shape-aware, not bare substrings:
  The spec lists patterns like ``sk-``, ``Bearer ``, ``API_KEY``. Taken as raw
  substrings these are unusable: ``sk-`` matches "ta*sk-*tree" and "ri*sk-*weights",
  ``Bearer `` matches the literal auth-header code ``f"Bearer {api_key}"``, and
  ``API_KEY``/``SECRET``/``password`` match env-var NAMES everywhere. So this
  scanner matches the real *value shapes* of provider keys (a token of the right
  alphabet and length after the prefix) and flags blocked *files* by type. It
  still catches every real leaked key (Groq ``gsk_…``, OpenAI ``sk-…``, Google
  ``AIza…``, etc.) while passing on legitimate code and obvious test fixtures.

Exit code 0 only when the surface is clean. Wire into CI.
Usage: python -X utf8 scripts/verify_no_secrets.py [--root .]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Directories never scanned (gitignored runtime/caches; not the published surface).
SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".git", ".claude", "state", "traces", "outputs",
    "node_modules",
}
SKIP_DIR_PREFIXES = (".venv", "state")

# Blocked FILE types — must never appear in the public surface.
BLOCKED_FILE_RE = re.compile(
    r"(^\.env$)|(^\.env\.(?!example$).+)|(\.key$)|(\.pem$)|(\.p12$)|(\.pfx$)"
    r"|(\.db$)|(\.sqlite[0-9]*$)",
)

# Real-secret VALUE shapes (prefix + key-length alphabet). Order matters:
# anthropic / proj before the generic sk- form.
SECRET_RES = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_proj_key", re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("groq_key", re.compile(r"gsk_[A-Za-z0-9]{30,}")),
    ("google_key", re.compile(r"AIza[A-Za-z0-9_\-]{30,}")),
    ("slack_bot_token", re.compile(r"xoxb-[A-Za-z0-9-]{20,}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("github_fine_pat", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("bearer_token", re.compile(r"Bearer [A-Za-z0-9._\-]{20,}")),
]

# A matched token is treated as a placeholder (not a real secret) if it contains
# any of these tells. Covers the intentional fake fixtures in tests/.
PLACEHOLDER_TELLS = (
    "abcdefghij", "1234567890", "example", "placeholder", "your_", "xxxx",
    "dummy", "fake", "redacted", "not-a-real", "notreal", "changeme",
    "0000000000",
)

TEXT_SUFFIXES = {
    ".py", ".json", ".jsonl", ".md", ".txt", ".js", ".css", ".html", ".htm",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env", ".sh", ".bat", ".ps1",
    "",
}


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or any(name.startswith(p) for p in SKIP_DIR_PREFIXES)


def _is_placeholder(token: str) -> bool:
    low = token.lower()
    return any(t in low for t in PLACEHOLDER_TELLS)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify no secrets / blocked files")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    blocked_files: list[str] = []
    secret_hits: list[str] = []

    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if any(_skip_dir(p) for p in rel_parts[:-1]):
            continue
        if not path.is_file():
            continue
        name = path.name
        if name == ".env.example":
            continue
        if BLOCKED_FILE_RE.search(name):
            blocked_files.append(str(path.relative_to(root)))
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, rgx in SECRET_RES:
            for m in rgx.findall(text):
                if _is_placeholder(m):
                    continue
                secret_hits.append(
                    f"{path.relative_to(root)}: {label} → {m[:10]}…(len {len(m)})"
                )

    ok = not blocked_files and not secret_hits
    if blocked_files:
        print("BLOCKED FILES present in public surface:")
        for f in sorted(set(blocked_files)):
            print(f"  - {f}")
    if secret_hits:
        print("SECRET-SHAPED VALUES found:")
        for h in sorted(set(secret_hits)):
            print(f"  - {h}")
    if ok:
        print("PASS: no secrets, no blocked files in the public surface.")
        return 0
    print("FAIL: secret-scan found problems above. Run scripts/sanitize_build.py.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
