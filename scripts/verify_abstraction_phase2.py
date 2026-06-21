"""Live smoke test for the Phase-2 L4.4 abstraction pass.

Runs SkillDistiller._abstract_skill_from_draft against the REAL OpenAI
endpoint using the key in .env (loaded by openai_provider on import).
Prints the principle + parameters so we can eyeball that the abstraction
is tool/language-agnostic and well-formed.

Usage:
    python scripts/verify_abstraction_phase2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teow_agl.modules.module_109b_skill_distiller import SkillDistiller


class _NullChat:
    backend = "none"

    def chat_json(self, *, system, user, max_tokens=1500):
        return {}


def main() -> int:
    distiller = SkillDistiller(
        chat_llm=_NullChat(),
        skill_manager=None,
        subject_confidence=None,
        constraints={},
        abstraction_prompt_path=(ROOT / "prompts"
                                 / "skill_abstraction_prompt.md"),
    )

    draft = {
        "name": "save-llm-text-as-docx",
        "description": "Turn the agent's generated text into a Word "
                       "document saved under outputs/.",
        "procedure": (
            "1. Capture the LLM output as plain text.\n"
            "2. Pass it to docx.save_under_outputs with a clean title.\n"
            "3. Verify the resulting file exists and is non-empty.\n"
            "4. Return the absolute path for downstream linking."
        ),
        "tags": ["docx", "office", "save"],
    }

    print("=== Live abstraction smoke test ===")
    result = distiller._abstract_skill_from_draft(draft)
    model = result.get("_model_used", "?")
    principle = result.get("principle", "")
    params = result.get("parameters", {})

    print(f"model_used : {model}")
    print(f"principle  : {principle}")
    print(f"parameters : {json.dumps(params, ensure_ascii=False)}")

    ok = (
        model.startswith("openai:")
        and bool(principle)
        and isinstance(params, dict)
        and len(principle.split()) <= 50
    )
    # Principle must not name the specific tool (abstraction contract)
    leaked_tool = "docx" in principle.lower()
    if leaked_tool:
        print("WARN: principle mentions the specific tool 'docx' "
              "(should be tool-agnostic)")

    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} "
          f"(model_ok={model.startswith('openai:')}, "
          f"has_principle={bool(principle)}, "
          f"words={len(principle.split())})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
