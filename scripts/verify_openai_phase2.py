"""Live smoke test for Phase 2 OpenAI provider.

NOT a pytest — costs real tokens (about $0.0001 per run with the
configured prompts). Runs only when invoked explicitly:

    python scripts/verify_openai_phase2.py

What it checks:
  1. chat (gpt-4o-mini)         — model resolves, returns non-empty text
  2. chat_json (json_object)    — returns a parsed dict
  3. embed batch                — list[list[float]] with expected dim
  4. embed single (convenience) — list[float]

Exit codes:
  0 — all pass
  1 — at least one failure (with reason on stderr)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `teow_agl` importable when run as `python scripts/verify_openai_phase2.py`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teow_agl.adapters.openai_provider import (  # noqa: E402
    openai_chat,
    openai_chat_json,
    openai_embed,
    openai_embed_one,
)


def _check(name: str, ok: bool, detail: str = "") -> tuple[bool, str]:
    mark = "OK  " if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f"  ({detail})"
    return ok, line


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        print("OPENAI_API_KEY not set — aborting.", file=sys.stderr)
        return 1

    print("Phase 2 OpenAI live smoke test")
    print("------------------------------")
    results: list[tuple[bool, str]] = []

    # 1. chat
    reply = openai_chat(
        system="Reply with exactly one English word: PONG.",
        user="ping?",
        max_tokens=8,
        temperature=0.0,
    )
    results.append(_check(
        "openai_chat returns non-empty text",
        bool(reply),
        f"reply={reply!r}",
    ))

    # 2. chat_json
    obj = openai_chat_json(
        system=("You return a JSON object with one key 'status' "
                "whose value is the string 'green'. Return ONLY the JSON."),
        user="report status",
        max_tokens=20,
        temperature=0.0,
    )
    results.append(_check(
        "openai_chat_json returns a parseable dict",
        isinstance(obj, dict) and obj.get("status") == "green",
        f"obj={obj!r}",
    ))

    # 3. embed batch
    vectors = openai_embed(["hello", "world"])
    ok = (vectors is not None
          and len(vectors) == 2
          and len(vectors[0]) == 1536  # text-embedding-3-small dim
          and len(vectors[1]) == 1536)
    results.append(_check(
        "openai_embed batch (2x1536-dim)",
        ok,
        f"n={len(vectors) if vectors else 0} "
        f"dim={len(vectors[0]) if vectors else 0}",
    ))

    # 4. embed single
    v = openai_embed_one("solo test")
    ok = v is not None and len(v) == 1536
    results.append(_check(
        "openai_embed_one (1536-dim vector)",
        ok,
        f"dim={len(v) if v else 0}",
    ))

    # Print + exit
    for _, line in results:
        print(line)
    fail_count = sum(1 for ok, _ in results if not ok)
    print("------------------------------")
    print(f"Result: {len(results) - fail_count}/{len(results)} passed.")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
