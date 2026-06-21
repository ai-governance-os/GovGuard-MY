"""Phase B0 — scenario-specific verifier infrastructure.

Generic engine that reads per-category rules from
`verifier_rules.scenario_checks.by_category[<category>]` and emits one
verification check entry per sub-rule. Categories themselves
(office_doc_generation, research_report, patent_legal_draft) are
populated by B1/B2/B3 — this file just tests the engine.
"""
from __future__ import annotations

from teow_agl.models import (
    CandidateAction, ExecutionResult, TaskEnvelope,
)
from teow_agl.modules.module_110_verifier import VerifierModule


def _envelope(goal: str = "test") -> TaskEnvelope:
    return TaskEnvelope(
        task_id="t1", session_id="s1", user_id="u1",
        raw_goal=goal, normalized_goal=goal,
        attachments=[], workspace_roots=[], metadata={},
    )


def _action(tool: str, op: str = "answer", body: str = "",
            metadata: dict | None = None) -> CandidateAction:
    meta = dict(metadata or {})
    if body and "body" not in meta:
        meta["body"] = body
    return CandidateAction(
        action_id="a1", tool=tool, operation=op, target="",
        purpose="t", expected_effect="t",
        reversibility="high", uncertainty="low",
        risk_factors=[], requires_governance=True,
        metadata=meta,
    )


def _success(action_id: str = "a1", body: str = "",
             affected: list[str] | None = None) -> ExecutionResult:
    return ExecutionResult(
        result_id="r1", task_id="t1", action_id=action_id,
        ticket_id="tk1", status="success",
        output_summary=body, affected_resources=affected or [],
    )


_BASE_RULES = {
    "enabled": True,
    "length_check": {"enabled": False},
    "format_check": {"enabled": False},
    "refusal_sniff": {"enabled": False},
    "scenario_checks": {
        "enabled": True,
        "applies_to_tools": ["chat", "docx", "pptx", "xlsx", "fs"],
        "by_category": {},
    },
}


def _verify(rules: dict, category: str, action: CandidateAction,
            body: str = "") -> dict:
    v = VerifierModule(rules=rules)
    return v.verify(
        envelope=_envelope(),
        plan_actions=[action],
        executions=[_success(body=body)],
        final_route="BLUE",
        task_category=category,
    )


def _names(report: dict) -> list[str]:
    return [c["name"] for c in report.get("checks", [])]


# ---------------------------------------------------------------------------
# No category, or unknown category → no scenario sub-checks fire
# ---------------------------------------------------------------------------

def test_no_category_means_no_scenario_checks():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "office_doc_generation": {"min_body_chars": 100}
    }
    r = _verify(rules, category=None, action=_action("chat"), body="hi")
    assert _names(r) == []  # nothing fired


def test_unmatched_category_means_no_scenario_checks():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "office_doc_generation": {"min_body_chars": 100}
    }
    r = _verify(rules, category="research_report",
                action=_action("chat"), body="hi")
    assert _names(r) == []


# ---------------------------------------------------------------------------
# min_body_chars
# ---------------------------------------------------------------------------

def test_min_body_chars_passes_when_long_enough():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "my_cat": {"min_body_chars": 10}
    }
    body = "this is a long enough body"
    r = _verify(rules, "my_cat", _action("chat", body=body), body=body)
    matching = [c for c in r["checks"]
                if c["name"] == "scenario.my_cat.min_body_chars"]
    assert matching and matching[0]["pass"] is True


def test_min_body_chars_fails_when_too_short():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "my_cat": {"min_body_chars": 100}
    }
    body = "short"
    r = _verify(rules, "my_cat", _action("chat", body=body), body=body)
    matching = [c for c in r["checks"]
                if c["name"] == "scenario.my_cat.min_body_chars"]
    assert matching and matching[0]["pass"] is False
    assert r["pass"] is False  # overall verification failed


# ---------------------------------------------------------------------------
# forbid_placeholders
# ---------------------------------------------------------------------------

def test_placeholder_check_passes_on_clean_body():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "my_cat": {"forbid_placeholders": ["TODO", "...", "<content>"]}
    }
    body = "Real substantive content here."
    r = _verify(rules, "my_cat", _action("chat", body=body), body=body)
    m = [c for c in r["checks"] if c["name"] == "scenario.my_cat.no_placeholder"]
    assert m and m[0]["pass"] is True


def test_placeholder_check_fails_on_TODO():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "my_cat": {"forbid_placeholders": ["TODO", "lorem ipsum"]}
    }
    body = "Intro paragraph TODO finish this."
    r = _verify(rules, "my_cat", _action("chat", body=body), body=body)
    m = [c for c in r["checks"] if c["name"] == "scenario.my_cat.no_placeholder"]
    assert m and m[0]["pass"] is False
    assert "TODO" in m[0]["details"]["hits"]


# ---------------------------------------------------------------------------
# must_contain_any (will be used for has_sources / has_disclaimer / …)
# ---------------------------------------------------------------------------

def test_must_contain_any_passes_when_a_needle_present():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "research": {
            "must_contain_any": [
                {"label": "has_sources",
                 "any_of": ["sources:", "来源:", "参考资料:"]}
            ]
        }
    }
    body = "Some content.\n\n参考资料:\n[1] https://x"
    r = _verify(rules, "research", _action("chat", body=body), body=body)
    m = [c for c in r["checks"] if c["name"] == "scenario.research.has_sources"]
    assert m and m[0]["pass"] is True


def test_must_contain_any_fails_when_none_present():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "research": {
            "must_contain_any": [
                {"label": "has_sources",
                 "any_of": ["sources:", "来源:"]}
            ]
        }
    }
    body = "Some content without citations."
    r = _verify(rules, "research", _action("chat", body=body), body=body)
    m = [c for c in r["checks"] if c["name"] == "scenario.research.has_sources"]
    assert m and m[0]["pass"] is False


# ---------------------------------------------------------------------------
# pptx_min_slides
# ---------------------------------------------------------------------------

def test_pptx_min_slides_passes_with_enough_slides():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "office": {"pptx_min_slides": 3}
    }
    action = _action("pptx", op="save_under_outputs", metadata={
        "title": "T", "slides": [
            {"title": "S1", "bullets": ["a"]},
            {"title": "S2", "bullets": ["b"]},
            {"title": "S3", "bullets": ["c"]},
        ]
    })
    r = _verify(rules, "office", action)
    m = [c for c in r["checks"] if c["name"] == "scenario.office.pptx_min_slides"]
    assert m and m[0]["pass"] is True


def test_pptx_min_slides_fails_with_too_few():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "office": {"pptx_min_slides": 5}
    }
    action = _action("pptx", op="save_under_outputs", metadata={
        "title": "T", "slides": [{"title": "S1", "bullets": ["a"]}]
    })
    r = _verify(rules, "office", action)
    m = [c for c in r["checks"] if c["name"] == "scenario.office.pptx_min_slides"]
    assert m and m[0]["pass"] is False


# ---------------------------------------------------------------------------
# xlsx_min_rows (excludes header)
# ---------------------------------------------------------------------------

def test_xlsx_min_rows_excludes_header():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "office": {"xlsx_min_rows": 3}
    }
    # 1 header + 4 data rows = 5 total rows; should pass for min 3
    action = _action("xlsx", op="save_under_outputs", metadata={
        "sheets": {"S": [
            ["col1", "col2"],
            ["a", 1], ["b", 2], ["c", 3], ["d", 4],
        ]}
    })
    r = _verify(rules, "office", action)
    m = [c for c in r["checks"] if c["name"] == "scenario.office.xlsx_min_rows"]
    assert m and m[0]["pass"] is True
    assert m[0]["details"]["actual"] == 4


# ---------------------------------------------------------------------------
# Disabled / empty config gracefully no-ops
# ---------------------------------------------------------------------------

def test_disabled_scenario_checks_emits_nothing():
    rules = dict(_BASE_RULES)
    rules["scenario_checks"] = {"enabled": False, "by_category": {
        "office": {"min_body_chars": 1000000}
    }}
    r = _verify(rules, "office", _action("chat", body="hi"), body="hi")
    assert not any(c["name"].startswith("scenario.") for c in r["checks"])


def test_multiple_sub_rules_emit_separate_checks():
    """Each sub-rule produces its own named entry."""
    rules = dict(_BASE_RULES)
    rules["scenario_checks"]["by_category"] = {
        "research": {
            "min_body_chars": 5,
            "forbid_placeholders": ["TODO"],
            "must_contain_any": [
                {"label": "has_sources", "any_of": ["sources:"]}
            ],
        }
    }
    body = "Content. sources:\n[1] x"
    r = _verify(rules, "research", _action("chat", body=body), body=body)
    names = _names(r)
    assert "scenario.research.min_body_chars" in names
    assert "scenario.research.no_placeholder" in names
    assert "scenario.research.has_sources" in names
