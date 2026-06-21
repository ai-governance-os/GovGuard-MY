"""Live smoke test for Phase-2 L4.5 cross-context skill adaptation.

Takes a SKILL learned on a Word REPORT and asks the real strong LLM
(OpenAI gpt-4o-mini) to adapt its procedure for a SLIDE DECK. Proves the
Level-3 transfer story end-to-end against the live API.

Usage:
    python scripts/verify_adaptation_phase2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer


_BODY = """# save-q3-docx-report

_Write a quarterly business report as a Word document._

<!-- skill_id: skill_demo00000001 | task_id: t1 | created: 2026-05-29 -->

## Principle

Organise findings into a structured narrative and verify the artifact
before delivery.

## Parameters

```json
{
  "tool": "docx",
  "output_format": "docx",
  "output_language": "en"
}
```

## Procedure

1. Draft an outline of the report's sections (summary, results, outlook).
2. Write each section as full prose paragraphs with supporting detail.
3. Save the document as a .docx file under outputs/.
4. Verify the file exists and is non-empty before returning the path.
"""


class _NullChat:
    backend = "none"


def main() -> int:
    synth = ContentSynthesizer(
        chat_llm=_NullChat(),
        adaptation_prompt_path=(ROOT / "prompts"
                                / "skill_adaptation_prompt.md"),
    )
    skill = {"skill_id": "skill_demo00000001",
             "name": "save-q3-docx-report",
             "description": "Write a quarterly report as a Word document.",
             "body": _BODY}

    print("=== Live cross-context adaptation smoke test ===")
    print("Source skill : Q3 report -> DOCX (sections + paragraphs)")
    print("New task      : Q3 board presentation -> PPTX (slides + bullets)\n")

    adapted, status = synth._adapt_skill_to_task(
        skill,
        target_tool="pptx",
        target_intent="Create a Q3 results presentation for the board.",
        target_format="slide_deck",
    )
    print(f"status: {status}\n")
    if adapted:
        print("--- adapted procedure ---")
        print(adapted)
        print("-------------------------")

    low = (adapted or "").lower()
    ok = (
        status == "ok"
        and adapted
        and ("slide" in low or "bullet" in low)
        # the source talked about paragraphs; a good pptx adaptation
        # should NOT just be a verbatim paragraph-shaped copy
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} "
          f"(status={status}, has_slide_language="
          f"{'slide' in low or 'bullet' in low})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
