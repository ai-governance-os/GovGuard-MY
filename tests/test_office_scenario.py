"""Phase B1 — §6 office document scenario.

Verifies:
 1. office_doc_generation prompts route to `_direct_office_plan`
    (planner skipped — no remote LLM call needed for routing).
 2. The direct plan ships EMPTY content metadata (no skeleton). This
    is the trigger that forces the synthesizer to call the chat LLM
    for real content — before B1 the plan shipped 1-slide / 3-row /
    two-sentence skeletons that passed the synthesizer's "looks real"
    gate and reached the user as placeholder files.
 3. The chat companion text is language-matched (Chinese vs English).
 4. The scenario verifier catches a docx body that still contains
    placeholder text after synthesis (defence-in-depth).
 5. The scenario verifier catches a pptx with too few slides.
"""
from __future__ import annotations

import json

from teow_agl.models import (
    CandidateAction, ExecutionResult, TaskEnvelope,
)
from teow_agl.modules.module_110_verifier import VerifierModule


# ---------------------------------------------------------------------------
# 1+2+3: classification + direct plan + bilingual chat
# ---------------------------------------------------------------------------

def test_office_doc_chinese_goal_routes_direct_with_empty_body(
        make_runtime_factory, isolated_workspace):
    """Chinese office prompt → planner_skipped, docx action has no
    pre-filled body so synthesizer must call the LLM."""
    rt = make_runtime_factory(planner=None, gate="approve_all")
    result = rt.run(raw_goal="写一份关于人工智能治理的报告")
    assert result.pre_assessment.task_category == "office_doc_generation"
    assert result.plan is not None
    assert result.plan.planner_id == "office_direct"
    # docx action exists with title set, body left empty
    docx_actions = [a for a in result.plan.actions if a.tool == "docx"]
    assert docx_actions, "should have a docx action"
    meta = docx_actions[0].metadata or {}
    # `title` is a hint for the synthesizer; body must be empty so
    # synthesizer fires.
    assert meta.get("title")
    # Either no body key, or empty string. (Synthesizer may have
    # written one if it ran — that's OK.)
    body_after = meta.get("body")
    if body_after is not None:
        # If body was filled, it must not equal the OLD skeleton text.
        assert "基础文档草稿" not in body_after
        assert "<content here>" not in body_after.lower()


def test_office_doc_chat_companion_is_chinese_for_chinese_intent(
        make_runtime_factory, isolated_workspace):
    rt = make_runtime_factory(planner=None, gate="approve_all")
    result = rt.run(raw_goal="做一份关于Q3销售的演示")
    chat = next((a for a in result.plan.actions if a.tool == "chat"), None)
    assert chat is not None
    body = (chat.metadata or {}).get("body", "")
    # Must be Chinese, must not contain the old "已为你生成基础"
    assert any("一" <= ch <= "鿿" for ch in body), \
        f"expected Chinese chat body, got {body!r}"
    assert "基础幻灯片" not in body  # old skeleton text gone
    assert "下面" in body or "下载" in body or "演示" in body


def test_office_doc_chat_companion_is_english_for_english_intent(
        make_runtime_factory, isolated_workspace):
    rt = make_runtime_factory(planner=None, gate="approve_all")
    result = rt.run(raw_goal="make a slide deck about Q3 sales")
    chat = next((a for a in result.plan.actions if a.tool == "chat"), None)
    assert chat is not None
    body = (chat.metadata or {}).get("body", "")
    # Not Chinese, should be English
    cjk = sum(1 for ch in body if "一" <= ch <= "鿿")
    assert cjk == 0
    assert "slide" in body.lower() or "deck" in body.lower() \
        or "presentation" in body.lower()


def test_office_doc_pptx_has_empty_slides_to_trigger_synth(
        make_runtime_factory, isolated_workspace):
    rt = make_runtime_factory(planner=None, gate="approve_all")
    result = rt.run(raw_goal="做一份关于AI的演示文稿")
    pptx_actions = [a for a in result.plan.actions if a.tool == "pptx"]
    assert pptx_actions
    meta = pptx_actions[0].metadata or {}
    # Slides should NOT be pre-filled with the old 1-slide skeleton.
    slides = meta.get("slides") or []
    # Either no slides yet (synth will fill) or non-empty real content
    # (synth ran). The OLD value was a single slide titled "核心要点".
    if slides:
        assert not (len(slides) == 1
                    and slides[0].get("title") == "核心要点"), \
            "still shipping the old hardcoded skeleton"


def test_office_doc_xlsx_has_no_skeleton_sheets(
        make_runtime_factory, isolated_workspace):
    rt = make_runtime_factory(planner=None, gate="approve_all")
    result = rt.run(raw_goal="做一份Q3销售数据的电子表格")
    xlsx_actions = [a for a in result.plan.actions if a.tool == "xlsx"]
    assert xlsx_actions
    meta = xlsx_actions[0].metadata or {}
    # The OLD skeleton had {"sheets": {"Summary": [["Item","Value"], ...]}}
    sheets = meta.get("sheets") or {}
    if "Summary" in sheets:
        # If a Summary sheet exists, it must NOT be the old "Item/Value"
        # placeholder shape.
        rows = sheets["Summary"]
        if isinstance(rows, list) and rows:
            header = rows[0] if rows else []
            assert header != ["Item", "Value"], \
                "still shipping the old hardcoded skeleton"


# ---------------------------------------------------------------------------
# 4+5: scenario verifier catches placeholders / too-few slides
# ---------------------------------------------------------------------------

def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="t1", session_id="s1", user_id="u1",
        raw_goal=goal, normalized_goal=goal,
        attachments=[], workspace_roots=[], metadata={},
    )


def _verifier_with_office_rules() -> VerifierModule:
    rules = json.load(open(
        "configs/verifier_rules.json", encoding="utf-8"))
    return VerifierModule(rules=rules)


def test_office_verifier_catches_placeholder_in_docx_body():
    v = _verifier_with_office_rules()
    action = CandidateAction(
        action_id="a1", tool="docx", operation="save_under_outputs",
        target="outputs/x.docx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T",
                  "body": "Intro paragraph TODO finish this later."},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary="",
        affected_resources=["outputs/x.docx"],
    )
    report = v.verify(
        envelope=_envelope("write a report"),
        plan_actions=[action], executions=[ex], final_route="BLUE",
        task_category="office_doc_generation",
    )
    placeholder_checks = [
        c for c in report["checks"]
        if c["name"] == "scenario.office_doc_generation.no_placeholder"
    ]
    assert placeholder_checks
    assert placeholder_checks[0]["pass"] is False
    assert "todo" in str(placeholder_checks[0]["details"]["hits"]).lower()


def test_office_verifier_catches_too_few_pptx_slides():
    v = _verifier_with_office_rules()
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "T",
                  "slides": [{"title": "S1", "bullets": ["a"]}]},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary="",
        affected_resources=["outputs/x.pptx"],
    )
    report = v.verify(
        envelope=_envelope("make a deck"),
        plan_actions=[action], executions=[ex], final_route="BLUE",
        task_category="office_doc_generation",
    )
    pp = [c for c in report["checks"]
          if c["name"] == "scenario.office_doc_generation.pptx_min_slides"]
    assert pp
    assert pp[0]["pass"] is False
    assert pp[0]["details"]["actual"] == 1
    assert pp[0]["details"]["min"] == 3


def test_office_verifier_passes_on_clean_pptx():
    v = _verifier_with_office_rules()
    slides = [{"title": f"S{i}", "bullets": [f"point {i}.{j}"
                                              for j in range(3)]}
              for i in range(5)]
    action = CandidateAction(
        action_id="a1", tool="pptx", operation="save_under_outputs",
        target="outputs/x.pptx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"title": "Quarterly Review", "slides": slides},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success",
        output_summary="",
        affected_resources=["outputs/x.pptx"],
    )
    report = v.verify(
        envelope=_envelope("make a deck about quarterly review"),
        plan_actions=[action], executions=[ex], final_route="BLUE",
        task_category="office_doc_generation",
    )
    # All scenario.* checks pass
    scenario_checks = [c for c in report["checks"]
                        if c["name"].startswith("scenario.")]
    assert scenario_checks
    assert all(c["pass"] for c in scenario_checks), \
        f"failed: {[c for c in scenario_checks if not c['pass']]}"


def test_office_verifier_catches_too_few_xlsx_rows():
    v = _verifier_with_office_rules()
    action = CandidateAction(
        action_id="a1", tool="xlsx", operation="save_under_outputs",
        target="outputs/x.xlsx",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata={"sheets": {"S": [
            ["col1", "col2"], ["a", 1], ["b", 2]
        ]}},
    )
    ex = ExecutionResult(
        result_id="r1", task_id="t1", action_id="a1", ticket_id="tk",
        status="success", output_summary="",
        affected_resources=["outputs/x.xlsx"],
    )
    report = v.verify(
        envelope=_envelope("make a spreadsheet"),
        plan_actions=[action], executions=[ex], final_route="BLUE",
        task_category="office_doc_generation",
    )
    xl = [c for c in report["checks"]
          if c["name"] == "scenario.office_doc_generation.xlsx_min_rows"]
    assert xl
    assert xl[0]["pass"] is False
    assert xl[0]["details"]["actual"] == 2  # 2 data rows
    assert xl[0]["details"]["min"] == 5
