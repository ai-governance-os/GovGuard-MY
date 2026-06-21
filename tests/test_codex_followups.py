"""Regression tests for the Codex UI-test follow-ups.

These lock the fixes made after an independent agent ran the Skill
Learning UI flow end-to-end (see SKILL_LEARNING_UI_TEST_REPORT.md):

  * P2 — cross-context retrieval lane (SkillManager.find_cross_context):
          a SEPARATE lower-threshold lane that only returns skills whose
          stored tool DIFFERS from the task's target tool. We did NOT
          lower the global min_score_for_injection (a deliberate
          governance choice: a missed skill is harmless, a wrongly-
          injected one can mislead).
  * P6 — procedure normalisation: a stringified list of steps is rendered
          as clean numbered markdown before persistence.
  * P7 — judge language guard: the LLM judge must only score output
          language when the user EXPLICITLY asked for one (it was
          hallucinating a "should be Chinese" failure on English prompts).
"""
from __future__ import annotations

from pathlib import Path

from teow_agl.modules.module_skill_manager import SkillManager
from teow_agl.modules.module_110_verifier import VerifierModule


def _constraints(**overrides) -> dict:
    base = {
        "creation_limits": {
            "max_chars_per_skill": 2000,
            "min_chars_per_skill": 60,
            "max_total_skills": 200,
        },
        "retrieval": {
            "top_k_injected": 3,
            "min_score_for_injection": 0.5,
            "cross_context_min_score": 0.30,
            "cross_context_categories": ["office_doc_generation"],
        },
        "min_task_quality": {
            "require_blue_or_green_route": True,
            "min_executions": 0,
            "skip_if_verification_failed": True,
        },
        "forbidden_patterns": {"patterns": []},
    }
    base.update(overrides)
    return base


def _quality() -> dict:
    return {"final_route": "BLUE", "verification_failed": False,
            "execution_success_count": 2}


# ─── P6 — procedure normalisation ───────────────────────────────────────

def test_normalize_procedure_json_list():
    out = SkillManager._normalize_procedure(
        '["Gather the Q3 figures", "Draft an executive summary", '
        '"Write the docx and save under outputs/"]')
    assert out == ("1. Gather the Q3 figures\n"
                   "2. Draft an executive summary\n"
                   "3. Write the docx and save under outputs/")


def test_normalize_procedure_python_repr_mixed_quotes():
    # ast.literal_eval handles the single/double-quote mix json.loads can't.
    out = SkillManager._normalize_procedure(
        "['Identify the topic', \"Utilize an AI tool\", 'Review output']")
    assert out == ("1. Identify the topic\n"
                   "2. Utilize an AI tool\n"
                   "3. Review output")


def test_normalize_procedure_plain_string_unchanged():
    s = "1. Do x.\n2. Do y.\n3. Save it."
    assert SkillManager._normalize_procedure(s) == s


def test_normalize_procedure_non_list_bracket_text_unchanged():
    # Looks bracket-ish but isn't a list literal → returned verbatim.
    s = "[note] follow the standard operating procedure carefully"
    assert SkillManager._normalize_procedure(s) == s


def test_create_skill_persists_normalised_numbered_markdown(tmp_path: Path):
    sm = SkillManager(tmp_path, constraints=_constraints())
    proc = ('["Gather the Q3 figures from finance", '
            '"Draft an executive summary then one section per metric", '
            '"Write a .docx via python-docx and save under outputs/"]')
    out = sm.create_skill(
        name="q3 docx report sop",
        description="How to assemble a Q3 docx report.",
        procedure=proc, task_id="task_x", task_quality=_quality(),
        source_category="office_doc_generation", source_shape="docx",
    )
    assert out["ok"], out
    body = sm.read_skill(out["skill_id"])
    # The ugly list literal must NOT survive into the SKILL_<id>.md.
    assert "[\"Gather" not in body and "['Gather" not in body
    assert "1. Gather the Q3 figures from finance" in body
    assert "3. Write a .docx via python-docx and save under outputs/" in body


# ─── P2 — cross-context retrieval lane ──────────────────────────────────

def _seed_two_tool_skills(sm: SkillManager) -> tuple[str, str]:
    docx = sm.create_skill(
        name="governance docx report sop",
        description="Assemble a governance readiness report as a docx.",
        procedure=("1. Gather governance findings. 2. Draft sections. "
                   "3. Write the docx and save under outputs/."),
        task_id="t1", task_quality=_quality(),
        source_category="office_doc_generation", source_shape="docx",
    )
    pptx = sm.create_skill(
        name="governance pptx deck sop",
        description="Assemble a governance readiness pitch deck as a pptx.",
        procedure=("1. Gather governance findings. 2. Draft slides. "
                   "3. Write the pptx and save under outputs/."),
        task_id="t2", task_quality=_quality(),
        source_category="office_doc_generation", source_shape="pptx",
    )
    assert docx["ok"] and pptx["ok"]
    return docx["skill_id"], pptx["skill_id"]


def test_find_cross_context_keeps_only_different_tool(tmp_path, monkeypatch):
    sm = SkillManager(tmp_path, constraints=_constraints())
    docx_id, pptx_id = _seed_two_tool_skills(sm)

    # Drive the FILTER deterministically: pretend the underlying retrieval
    # surfaced BOTH skills at a usable score, regardless of BM25/embeddings.
    def _fake_find_relevant(goal, *, top_k=3, min_score=None):
        return [
            {"skill_id": docx_id, "name": "governance docx report sop",
             "description": "", "tags": [], "score": 0.42, "body": "",
             "char_length": 0, "usage_count": 1, "success_count": 0,
             "rank_method": "cosine"},
            {"skill_id": pptx_id, "name": "governance pptx deck sop",
             "description": "", "tags": [], "score": 0.40, "body": "",
             "char_length": 0, "usage_count": 1, "success_count": 0,
             "rank_method": "cosine"},
        ]
    monkeypatch.setattr(sm, "find_relevant", _fake_find_relevant)

    # Target = pptx → only the DOCX skill is a valid cross-tool candidate.
    hits = sm.find_cross_context("make a governance deck", target_tool="pptx")
    assert [h["skill_id"] for h in hits] == [docx_id]
    assert hits[0]["cross_context"] is True
    assert hits[0]["source_shape"] == "docx"

    # Target = docx → only the PPTX skill qualifies as cross-tool.
    hits2 = sm.find_cross_context("make a governance doc", target_tool="docx")
    assert [h["skill_id"] for h in hits2] == [pptx_id]


def test_find_cross_context_empty_when_no_target_tool(tmp_path):
    sm = SkillManager(tmp_path, constraints=_constraints())
    _seed_two_tool_skills(sm)
    assert sm.find_cross_context("make something", target_tool="") == []


# ─── P7 — judge language guard ──────────────────────────────────────────

def test_detect_requested_language_none_for_plain_english():
    assert VerifierModule._detect_requested_language(
        "Make a docx report about AI governance for boards") == ""


def test_detect_requested_language_chinese_and_english_cues():
    assert VerifierModule._detect_requested_language(
        "用中文写一份关于AI治理的报告") == "Chinese"
    assert VerifierModule._detect_requested_language(
        "Please answer in English only") == "English"


def test_judge_prompt_no_language_assumption_when_unspecified():
    p = VerifierModule._judge_user_prompt(
        "Make a docx report about AI governance", "the output")
    assert "did NOT specify" in p
    assert "never assume a language requirement" in p


def test_judge_prompt_enforces_explicit_language():
    p = VerifierModule._judge_user_prompt("用中文写报告", "the output")
    assert "explicitly asked for Chinese" in p
