"""Phase 14 — LLM-as-judge verifier (Module 110.llm_judge).

Unit tests for the judge method in isolation, with a stub chat LLM.
Covers:
  * judge disabled → enabled=False, no LLM call
  * no chat_llm → skipped:no_chat_llm
  * RED/INFEASIBLE route → skipped:route_exempt
  * category not in judge_enabled_categories → skipped
  * empty body → skipped:no_judgeable_output
  * happy path: LLM returns valid score → pass/fail based on threshold
  * LLM raises → skipped:judge_error
  * LLM returns garbage → skipped:judge_empty_or_malformed
  * score clamped to [0, 100]
  * per-category rubric resolved (essay → report_generation rubric)
  * default rubric fallback when category unknown
  * issues + suggestions clamped to 8 items each, 200 chars each
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.modules.module_110_verifier import VerifierModule
from teow_agl.models import CandidateAction, ExecutionResult, TaskEnvelope


# ---------------------------------------------------------------------------
# Stub chat LLM (returns fixed JSON; counts calls)
# ---------------------------------------------------------------------------
class _StubChatLLM:
    def __init__(self, response: dict | None = None,
                 raise_on_call: Exception | None = None) -> None:
        self.response = response if response is not None else {}
        self.raise_on_call = raise_on_call
        self.calls: list[dict] = []

    def chat_json(self, system: str, user: str, max_tokens: int = 600) -> dict:
        self.calls.append({"system": system[:200], "user": user[:200]})
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.response

    def chat(self, system: str, user: str, max_tokens: int = 600) -> str:
        return json.dumps(self.response or {})


def _load_real_rules() -> dict:
    p = (Path(__file__).resolve().parents[1] / "configs" / "verifier_rules.json")
    return json.loads(p.read_text(encoding="utf-8"))


def _load_real_rubrics() -> dict:
    p = (Path(__file__).resolve().parents[1] / "configs" / "judge_rubrics.json")
    return json.loads(p.read_text(encoding="utf-8"))


def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_test_judge", session_id="sess_test",
        user_id="default_user", raw_goal=goal, normalized_goal=goal,
    )


def _action(tool: str, op: str = "answer", body: str | None = None,
            target: str = "") -> CandidateAction:
    meta = {}
    if body is not None:
        meta["body"] = body
    return CandidateAction(
        action_id="a1", tool=tool, operation=op, target=target,
        purpose="t", expected_effect="t", reversibility="high",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata=meta,
    )


def _exec(action: CandidateAction, *, status: str = "success",
          summary: str = "") -> ExecutionResult:
    return ExecutionResult(
        task_id="task_test_judge", action_id=action.action_id,
        ticket_id="ticket_test", status=status, output_summary=summary,
        affected_resources=[],
    )


# ===========================================================================
# Disabled / skipped paths (no LLM call)
# ===========================================================================

def test_judge_disabled_in_config_short_circuits():
    rules = _load_real_rules()
    rules["llm_judge"]["enabled"] = False
    llm = _StubChatLLM({"score": 100})
    v = VerifierModule(rules=rules, rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert out["enabled"] is False
    assert llm.calls == []


def test_judge_skipped_when_no_chat_llm():
    """A VerifierModule with no chat_llm should always skip the judge."""
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=None)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert out["pass"] is None
    assert out["skipped_reason"] == "no_chat_llm"


def test_judge_skipped_on_red_route():
    """RED route → refusal IS the right answer; don't judge it."""
    llm = _StubChatLLM({"score": 100})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="I cannot help with that — blocked.")
    out = v.llm_judge(
        envelope=_envelope("Tell me my passwords"), plan_actions=[a],
        executions=[_exec(a, summary="blocked")], final_route="RED",
        task_category="unknown",
    )
    assert out["pass"] is None
    assert "route_exempt" in out["skipped_reason"]
    assert llm.calls == []


def test_judge_skipped_on_infeasible_route():
    llm = _StubChatLLM({"score": 100})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="That's infeasible.")
    out = v.llm_judge(
        envelope=_envelope("Write a 10TB essay"), plan_actions=[a],
        executions=[_exec(a, summary="infeasible")], final_route="INFEASIBLE",
        task_category="unknown",
    )
    assert out["pass"] is None
    assert "route_exempt" in out["skipped_reason"]


def test_judge_skipped_when_category_not_enabled():
    rules = _load_real_rules()
    # Make judge category-restricted to a category we won't pass
    rules["llm_judge"]["judge_enabled_categories"] = ["only_this_one"]
    llm = _StubChatLLM({"score": 100})
    v = VerifierModule(rules=rules, rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert out["pass"] is None
    assert "category_not_enabled" in out["skipped_reason"]
    assert llm.calls == []


def test_judge_skipped_when_no_body_to_judge():
    """If all executions are file-marker outputs (no prose), there's
    nothing for the judge to score."""
    llm = _StubChatLLM({"score": 100})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    out = v.llm_judge(
        envelope=_envelope("Save a file"), plan_actions=[],
        executions=[], final_route="BLUE", task_category="unknown",
    )
    assert out["pass"] is None
    assert out["skipped_reason"] == "no_judgeable_output"
    assert llm.calls == []


# ===========================================================================
# Happy path — LLM returns a valid decision
# ===========================================================================

def test_judge_pass_when_score_meets_threshold():
    llm = _StubChatLLM({
        "score": 85, "issues": [], "suggestions": [],
        "reasoning": "answers the question well",
    })
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="Paris is the capital of France.")
    out = v.llm_judge(
        envelope=_envelope("What is the capital of France?"),
        plan_actions=[a], executions=[_exec(a, summary="Paris is the capital.")],
        final_route="BLUE", task_category="unknown",
    )
    assert out["pass"] is True
    assert out["score"] == 85
    assert "judge_pass" in out["summary"]
    assert len(llm.calls) == 1


def test_judge_fail_when_score_below_threshold():
    llm = _StubChatLLM({
        "score": 30,
        "issues": ["gibberish characters", "wrong language"],
        "suggestions": ["rewrite from scratch", "use English"],
    })
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="xxxxx ddddd ?????")
    out = v.llm_judge(
        envelope=_envelope("What is the capital of France?"),
        plan_actions=[a], executions=[_exec(a, summary="xxxxx ddddd ?????")],
        final_route="BLUE", task_category="unknown",
    )
    assert out["pass"] is False
    assert out["score"] == 30
    assert "judge_fail" in out["summary"]
    assert "gibberish characters" in out["issues"]


# ===========================================================================
# Robustness — bad LLM responses
# ===========================================================================

def test_judge_skipped_when_llm_raises():
    llm = _StubChatLLM(raise_on_call=RuntimeError("network down"))
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert out["pass"] is None
    assert "judge_error" in out["skipped_reason"]


def test_judge_skipped_on_empty_response():
    llm = _StubChatLLM({})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert out["pass"] is None
    assert out["skipped_reason"] == "judge_empty_or_malformed"


def test_judge_clamps_runaway_score():
    """Even if the LLM returns score=999 or -50, it should clamp."""
    llm = _StubChatLLM({"score": 999, "issues": [], "suggestions": []})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert 0 <= out["score"] <= 100


def test_judge_handles_garbage_score_field():
    llm = _StubChatLLM({"score": "very good"})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    # Garbage → 0 → fails
    assert out["score"] == 0
    assert out["pass"] is False


def test_judge_clamps_issues_and_suggestions():
    """Each list capped at 8 items, each item at 200 chars."""
    llm = _StubChatLLM({
        "score": 30,
        "issues": ["issue " + str(i) for i in range(20)],
        "suggestions": ["x" * 500 for _ in range(20)],
    })
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="unknown",
    )
    assert len(out["issues"]) <= 8
    assert len(out["suggestions"]) <= 8
    for s in out["suggestions"]:
        assert len(s) <= 200


# ===========================================================================
# Rubric resolution
# ===========================================================================

def test_judge_uses_category_specific_rubric():
    """A task_category that exists in rubrics should pick that rubric
    (not default)."""
    rubrics = _load_real_rubrics()
    # report_generation has a higher pass_threshold than default
    llm = _StubChatLLM({"score": 62, "issues": [], "suggestions": []})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=rubrics, chat_llm=llm)
    a = _action("chat", body="some report")
    out = v.llm_judge(
        envelope=_envelope("Write a report"), plan_actions=[a],
        executions=[_exec(a, summary="some report")], final_route="BLUE",
        task_category="report_generation",
    )
    assert out["rubric_used"] == "report_generation"
    # report_generation threshold = 65; score 62 → fail
    assert out["pass"] is False


def test_judge_falls_back_to_default_rubric():
    llm = _StubChatLLM({"score": 70, "issues": [], "suggestions": []})
    v = VerifierModule(rules=_load_real_rules(),
                       rubrics=_load_real_rubrics(), chat_llm=llm)
    a = _action("chat", body="ok")
    out = v.llm_judge(
        envelope=_envelope("anything"), plan_actions=[a],
        executions=[_exec(a, summary="ok")], final_route="BLUE",
        task_category="totally_unknown_category_not_in_rubrics",
    )
    assert out["rubric_used"] == "default"
