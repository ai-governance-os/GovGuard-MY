"""Pre-demo verification: Groq + Qwen3-32B works end-to-end with TEOW-AGL.

Run this ONCE before recording / demoing so you don't discover a problem
live. Six checks:

  1. GROQ_API_KEY is set
  2. Alias resolution: "qwen" -> "qwen/qwen3-32b"
  3. OFFLINE — planner request size (system prompt + compact catalog).
     This is the Phase-A1 413 fix: the request must stay well under
     Groq's free-tier per-minute token budget. No API call.
  4. Plain chat call (Chinese; must NOT leak a <think> block)
  5. REAL planner call — uses the actual planner system prompt + the
     actual compact tool catalog, on a heavy research-style brief. This
     is the true 413 test: if the real pipeline would 413, this fails.
  6. CJK quality sanity (no obvious garbled / repeated output)

Usage:
    $env:GROQ_API_KEY = "<your-key>"
    $env:GROQ_MODEL   = "qwen"
    python -X utf8 scripts/verify_qwen_switch.py

Exit code 0 = all green, non-zero = something to look at.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teow_agl.adapters.groq_provider import GroqPlanner, _resolve_model
from teow_agl.adapters.chat_llm import ChatLLM
from teow_agl.runtime import _compact_tool_catalog, _trim_web_hits

# A request must stay comfortably under the free-tier 6,000 tokens/min
# budget. ~14,000 chars of mixed CJK/Latin is roughly ~4,500 tokens —
# safe headroom. If the prompt + catalog re-bloats past this, check 3
# fails BEFORE a demo instead of 413-ing during one.
_REQUEST_CHAR_BUDGET = 14_000


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _looks_like_chinese(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for ch in text if _is_cjk(ch))
    return cjk >= 5 and cjk / max(len(text), 1) > 0.1


def _has_think_block(text: str) -> bool:
    low = (text or "").lower()
    return "<think>" in low or "</think>" in low


def main() -> int:
    failures: list[str] = []

    # ---- 1. API key present ------------------------------------------------
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("[FAIL 1/6] GROQ_API_KEY not set.")
        print("           $env:GROQ_API_KEY = '<your-key>'")
        return 1
    print(f"[ OK  1/6] GROQ_API_KEY present (len={len(key)})")

    # ---- 2. Alias resolution ----------------------------------------------
    raw = os.environ.get("GROQ_MODEL", "qwen")
    resolved = _resolve_model(raw)
    if "qwen" in resolved.lower():
        print(f"[ OK  2/6] GROQ_MODEL '{raw}' -> '{resolved}'")
    else:
        print(f"[WARN 2/6] GROQ_MODEL='{raw}' -> {resolved} (not a Qwen). "
              f"Set $env:GROQ_MODEL='qwen' for native Chinese.")

    # ---- 3. OFFLINE planner request size ----------------------------------
    sys_prompt = (ROOT / "prompts" / "module_102_planner_system.md").read_text(
        encoding="utf-8")
    catalog = json.loads(
        (ROOT / "configs" / "tool_catalog.json").read_text(encoding="utf-8"))
    compact = _compact_tool_catalog(catalog.get("tools", {}))
    compact_json = json.dumps(compact, ensure_ascii=False)
    # Representative brief: realistic user_intent + the compact catalog.
    sample_brief = {
        "task_id": "size_probe",
        "user_intent": "写一份关于人工智能治理的 800 字研究报告，并附引用来源",
        "task_category": "report_generation",
        "planning_mode": "direct",
        "available_tools": compact,
    }
    brief_json = json.dumps(sample_brief, ensure_ascii=False)
    request_chars = len(sys_prompt) + len(brief_json)
    print(f"[ -- 3/6] system_prompt={len(sys_prompt):,}  "
          f"compact_catalog={len(compact_json):,}  "
          f"sample_brief={len(brief_json):,}")
    if request_chars <= _REQUEST_CHAR_BUDGET:
        print(f"[ OK  3/6] planner request ~{request_chars:,} chars "
              f"(<= {_REQUEST_CHAR_BUDGET:,} budget) — safe vs Groq 413")
    else:
        failures.append(
            f"planner request {request_chars:,} chars exceeds "
            f"{_REQUEST_CHAR_BUDGET:,} budget — 413 risk")
        print(f"[FAIL 3/6] planner request {request_chars:,} chars "
              f"> {_REQUEST_CHAR_BUDGET:,} budget — will risk 413")

    # ---- 4. Plain chat (Chinese, no <think> leak) -------------------------
    os.environ.setdefault("TEOW_AGL_CHAT_LLM", "groq")
    chat = ChatLLM(backend="groq")
    reply = chat.chat(
        system="You are TEOW-AGL, a governed AI agent. Answer briefly in Chinese.",
        user="你是谁？请用中文一句话介绍自己。",
        max_tokens=200,
    )
    if _has_think_block(reply):
        failures.append("plain chat leaked a <think> block")
        print(f"[FAIL 4/6] plain chat leaked <think>:\n           {reply[:300]}")
    elif not reply:
        failures.append("plain chat returned empty string")
        print("[FAIL 4/6] plain chat: empty reply (network/auth issue)")
    elif not _looks_like_chinese(reply):
        failures.append(f"plain chat not Chinese: {reply!r}")
        print(f"[FAIL 4/6] plain chat answered non-Chinese:\n           {reply[:200]}")
    else:
        print(f"[ OK  4/6] plain chat Chinese reply ({len(reply)} chars):")
        print(f"           {reply[:150]}")

    # ---- 5. REAL planner call (the true 413 test) -------------------------
    # Use the ACTUAL system prompt + ACTUAL compact catalog + a heavy
    # research brief WITH simulated web_search_context — the heaviest
    # realistic planner request. If this doesn't 413, the demo won't.
    planner = GroqPlanner()
    print(f"           planner.model = {planner.model}")
    fake_hits = _trim_web_hits([
        {"title": f"Source {i}", "url": f"https://example.com/{i}",
         "content": "人工智能治理是指对 AI 系统的开发与部署进行监督。" * 30,
         "source": "tavily"}
        for i in range(5)
    ])
    heavy_brief = dict(sample_brief)
    heavy_brief["web_search_context"] = fake_hits
    plan = planner.plan(heavy_brief, sys_prompt)
    if "refusal_type" in plan:
        rt = plan.get("refusal_type")
        msg = str(plan.get("message", ""))
        failures.append(f"real planner call refused: {rt} — {msg[:120]}")
        print(f"[FAIL 5/6] real planner call REFUSED: {rt}")
        print(f"           {msg[:200]}")
        if "413" in msg:
            print("           >>> 413 STILL HAPPENING — brief needs more slimming")
        elif "429" in msg:
            print("           >>> 429 rate limit — wait 60s and re-run "
                  "(not a code bug)")
    elif not plan.get("actions"):
        failures.append("real planner call returned no actions")
        print(f"[FAIL 5/6] real planner call: empty plan — "
              f"{json.dumps(plan, ensure_ascii=False)[:200]}")
    else:
        acts = plan["actions"]
        tools = ", ".join(f"{a.get('tool')}.{a.get('operation')}" for a in acts)
        print(f"[ OK  5/6] real planner call OK — {len(acts)} action(s): {tools}")

    # ---- 6. CJK quality sanity --------------------------------------------
    body = ""
    if plan.get("actions"):
        for a in plan["actions"]:
            b = (a.get("metadata") or {}).get("body", "")
            if b:
                body = b
                break
    full = (reply or "") + " " + body
    if full.strip():
        repeats = 0
        for i in range(0, max(0, len(full) - 8), 8):
            chunk = full[i:i + 8]
            if chunk.strip() and full.count(chunk) > 3:
                repeats += 1
        if repeats > 2:
            print(f"[WARN 6/6] {repeats} repeated 8-grams — synthesizer may "
                  f"still need to rewrite, but not a blocker")
        else:
            print("[ OK  6/6] no obvious repetition in output")
    else:
        print("[ -- 6/6] no body text to repetition-check (skipped)")

    print()
    if failures:
        print(f"=== {len(failures)} check(s) FAILED ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== ALL CHECKS PASSED — safe to start the server and demo ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
