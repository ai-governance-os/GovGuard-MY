"""Live end-to-end smoke test for the Phase-2 Level-3 transfer chain.

Runs FIVE real cross-context cases against the live OpenAI API (key from
.env), exercising the whole chain per case:

    create_skill (source tool + principle + REAL embedding)   # L4.3 / L4.4
        → SkillManager.find_relevant cosine retrieval          # L4.3
        → ContentSynthesizer._adapt_skill_to_task (gpt-4o-mini)# L4.5

Unlike the pytest suite (which mocks the network), this spends tokens and
proves the embeddings + adaptation actually work together. Its output is the
raw material for SKILL_LEVEL_3_RESULTS.md (L4.10), including any honest
failures.

Usage:
    python scripts/verify_phase2_e2e.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252; force UTF-8 so the CJK case prints.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from teow_agl.modules.module_skill_manager import SkillManager
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer
from teow_agl.util import embeddings as emb


_TOOL_PROC = {
    "docx": ("1. Outline the report's sections (summary, results, outlook).\n"
             "2. Write each section as full prose paragraphs.\n"
             "3. Save as a .docx under outputs/ and verify it is non-empty."),
    "pptx": ("1. Outline the slides (title, agenda, results, next steps).\n"
             "2. Write concise bullet points per slide.\n"
             "3. Save as a slide deck and verify it opens."),
    "xlsx": ("1. Lay out the columns (metric, Q2, Q3, delta).\n"
             "2. Fill the rows with the quarterly figures.\n"
             "3. Save as a spreadsheet and verify the cells are populated."),
}

# (source_tool, target_tool, goal, expected keyword in a good adaptation)
_CASES = [
    ("docx", "pptx", "Turn the Q3 report into a board slide deck", "slide"),
    ("docx", "xlsx", "Put the Q3 figures into an Excel spreadsheet", "column"),
    ("pptx", "docx", "Write the deck up as a formal Word document", "paragraph"),
    ("xlsx", "pptx", "Make a PowerPoint from the spreadsheet", "slide"),
    ("docx", "pptx", "把这份季度报告做成给董事会的演示文稿", "slide"),
]


class _NullChat:
    backend = "none"


def _make_skill(sm: SkillManager, tool: str) -> dict:
    out = sm.create_skill(
        name=f"q3-{tool}-deliverable",
        description=f"Produce a quarterly business deliverable as a {tool}.",
        procedure=_TOOL_PROC[tool],
        parameters={"tool": tool, "output_format": tool,
                    "output_language": "en"},
        principle="Organise findings into a structured narrative and verify "
                  "the artifact before delivery.",
        source_category="office_doc_generation",
        source_shape=f"{tool}_v1",
        tags=["office", tool],
    )
    return out


def main() -> int:
    os.environ.setdefault("SKILL_EMBEDDING_PROVIDER", "openai")
    emb._reset_dim_cache_for_tests()
    if not emb.embedding_provider_available():
        print("RESULT: SKIP — no OpenAI key / embedding provider unavailable.")
        return 0

    synth = ContentSynthesizer(
        chat_llm=_NullChat(),
        adaptation_prompt_path=(ROOT / "prompts" / "skill_adaptation_prompt.md"),
    )

    print("=== Live Phase-2 end-to-end cross-context smoke (5 cases) ===\n")
    passes = 0
    with tempfile.TemporaryDirectory() as td:
        constraints = {
            "creation_limits": {"min_chars_per_skill": 10,
                                "max_chars_per_skill": 4000,
                                "max_total_skills": 200},
            "retrieval": {"min_score_for_injection": 0.0},
        }
        for i, (src, tgt, goal, kw) in enumerate(_CASES, 1):
            sm = SkillManager(Path(td) / f"skills_{i}", constraints=constraints)
            created = _make_skill(sm, src)
            embedded = created.get("embedding_persisted")

            hits = sm.find_relevant(goal, top_k=3, min_score=0.0)
            retrieved = bool(hits and hits[0]["skill_id"] == created["skill_id"])
            method = hits[0]["rank_method"] if hits else "—"

            adapted, status = (None, "not_run")
            if retrieved:
                adapted, status = synth._adapt_skill_to_task(
                    {"skill_id": created["skill_id"],
                     "name": created.get("skill_id", ""),
                     "description": f"Q3 deliverable as {src}",
                     "body": sm.read_skill(created["skill_id"])},
                    target_tool=tgt,
                    target_intent=goal,
                )
            low = (adapted or "").lower()
            ok = (embedded and retrieved and status == "ok"
                  and adapted and kw in low)
            passes += int(bool(ok))

            print(f"[case {i}] {src} -> {tgt}  goal={goal!r}")
            print(f"  embedded={embedded} retrieved={retrieved}({method}) "
                  f"status={status} kw('{kw}')={kw in low}")
            print(f"  -> {'PASS' if ok else 'FAIL'}\n")

    print(f"RESULT: {passes}/{len(_CASES)} cases passed.")
    # Treat >=4/5 as a green smoke (LLM adaptation is non-deterministic;
    # the doc records the honest per-case outcome regardless).
    return 0 if passes >= 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
