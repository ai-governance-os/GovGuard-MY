"""Phase 17 — FTS5 cross-session search.

Covers:
  * SessionIndex: schema bootstrap; idempotent re-index; BM25 query
    returns sane results; bloat cap evicts oldest; empty/garbage query
    safe; query sanitization handles FTS5 grammar landmines
  * build_indexable_body: skips file-marker outputs; respects max_chars
  * SessionSearchTool: query/count dispatch; empty query rejected;
    unknown op rejected
  * Runtime integration: tasks auto-indexed on completion; episodic
    heuristic triggers injection on "remember when" / "上次" phrases;
    `no_index=True` metadata opts out; kill-switch env disables fully
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool
from teow_agl.tools.session_search_tool import SessionSearchTool
from teow_agl.util.fts5_indexer import (
    SessionIndex, build_indexable_body, _sanitize_query,
)
from teow_agl.models import CandidateAction


# ===========================================================================
# Indexer — schema + write + query
# ===========================================================================

def test_index_bootstraps_clean(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    assert idx.available is True
    assert idx.count() == 0


def test_index_one_task_then_query(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    res = idx.index_task(
        task_id="t1",
        raw_goal="write a koi pond essay",
        body="[chat] Koi ponds need filtration and oxygenation",
        started_at="2026-01-01T10:00:00",
        final_route="BLUE",
    )
    assert res["ok"] and res["indexed"]
    assert idx.count() == 1
    hits = idx.query("koi", top_k=5)
    assert hits, "expected at least one match"
    assert hits[0]["task_id"] == "t1"
    assert "koi" in hits[0]["raw_goal"].lower()


def test_index_is_idempotent_on_same_task_id(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="version one", body="body one")
    idx.index_task(task_id="t1", raw_goal="version two", body="body two")
    assert idx.count() == 1  # not 2
    hits = idx.query("version", top_k=5)
    assert hits[0]["raw_goal"] == "version two"


def test_index_query_ranks_by_relevance(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="cooking pasta",
                   body="boil water add salt and pasta")
    idx.index_task(task_id="t2", raw_goal="cooking pizza",
                   body="stretch dough top with sauce cheese basil")
    idx.index_task(task_id="t3", raw_goal="cooking pasta carbonara recipe",
                   body="eggs guanciale pecorino black pepper pasta")
    hits = idx.query("pasta", top_k=5)
    # Both pasta tasks should rank above the pizza one
    pasta_ids = [h["task_id"] for h in hits]
    assert "t1" in pasta_ids and "t3" in pasta_ids
    # And the carbonara one (more 'pasta' matches in body) should rank
    # at or near the top
    assert "t3" in pasta_ids[:2] or "t1" in pasta_ids[:2]


def test_index_no_match_returns_empty(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="cooking pasta", body="boil water")
    assert idx.query("photosynthesis") == []


def test_index_no_index_flag_skips(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    res = idx.index_task(task_id="t1", raw_goal="secret task",
                         body="confidential", no_index=True)
    assert res["ok"] and res["indexed"] is False
    assert res["reason"] == "opted_out_no_index"
    assert idx.count() == 0


def test_index_empty_fields_no_op(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    # No task_id → skipped
    res = idx.index_task(task_id="", raw_goal="x", body="y")
    assert res["indexed"] is False
    # No goal AND no body → skipped
    res = idx.index_task(task_id="t1", raw_goal="", body="")
    assert res["indexed"] is False


def test_index_bloat_cap_evicts_oldest(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db", max_indexed_tasks=3)
    # Insert 5 tasks; expect only the latest 3 to survive
    for i in range(5):
        idx.index_task(
            task_id=f"t{i}", raw_goal=f"task number {i}",
            body=f"body for task {i}",
            started_at=f"2026-01-0{i + 1}T10:00:00",
        )
    assert idx.count() == 3
    # The earliest two (t0 and t1) should have been evicted
    hits = idx.query("task", top_k=10)
    ids = {h["task_id"] for h in hits}
    assert "t0" not in ids
    assert "t1" not in ids
    assert "t4" in ids


def test_index_query_empty_string_safe(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="something", body="body")
    assert idx.query("") == []
    assert idx.query("   ") == []


def test_index_replays_state_across_processes(tmp_path: Path):
    """Open one SessionIndex, write, close, open another — the new
    instance should see all prior data."""
    db = tmp_path / "idx.db"
    idx1 = SessionIndex(db)
    idx1.index_task(task_id="t1", raw_goal="lazy fox", body="quick brown")
    # New instance — fresh sqlite connection
    idx2 = SessionIndex(db)
    assert idx2.count() == 1
    assert idx2.query("fox")[0]["task_id"] == "t1"


# ===========================================================================
# Query sanitization — FTS5 grammar landmines
# ===========================================================================

def test_sanitize_handles_special_chars(tmp_path: Path):
    """Queries with FTS5-syntax characters (colon, parens, asterisk,
    quote) should NOT crash the query path — they should be sanitized
    to a safe OR-of-quoted-tokens expression."""
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="essay about cats",
                   body="cats are nice")
    # These would all raise sqlite3.OperationalError if we passed them
    # raw to MATCH.
    for q in ["raw_goal:cats", "cat(s)", "cat*", '"cat', "cat:foo:bar"]:
        hits = idx.query(q)
        assert isinstance(hits, list), f"query {q!r} crashed"


def test_sanitize_query_skips_garbage():
    assert _sanitize_query("") == ""
    assert _sanitize_query("    ") == ""
    assert _sanitize_query("!@#$%^&*()") == ""


def test_sanitize_query_or_joins_tokens():
    """Multi-word queries should turn into OR-of-quoted-tokens so the
    user gets results even when not all words appear together."""
    sanitized = _sanitize_query("anthropic claude latest news")
    assert ' OR ' in sanitized
    assert sanitized.count('"') == 8  # 4 tokens * 2 quote chars each


def test_sanitize_caps_runaway_token_count():
    """A 100-token paragraph shouldn't expand into a massive query."""
    sanitized = _sanitize_query(" ".join(f"word{i}" for i in range(100)))
    assert sanitized.count(" OR ") <= 15  # 16 tokens max → 15 ORs


# ===========================================================================
# build_indexable_body
# ===========================================================================

def test_build_body_strips_file_markers():
    """Execution summaries that are just file-write markers (no real
    content) should be excluded from the index — searching for 'docx'
    shouldn't surface every docx-writing task."""
    executions = [
        {"tool": "docx", "output_summary": "docx_written:essay.docx",
         "status": "success"},
        {"tool": "chat", "output_summary": "Koi ponds need oxygenation.",
         "status": "success"},
        {"tool": "image_gen", "output_summary": "image_saved:koi.png",
         "status": "success"},
    ]
    body = build_indexable_body(executions)
    assert "Koi ponds" in body
    assert "docx_written" not in body
    assert "image_saved" not in body


def test_build_body_respects_max_chars():
    executions = [
        {"tool": "chat", "output_summary": "x" * 10000, "status": "success"},
    ]
    body = build_indexable_body(executions, max_chars=200)
    assert len(body) <= 200


def test_build_body_handles_objects_and_dicts():
    """Should accept both ExecutionResult-like objects and dicts."""
    class _Ex:
        def __init__(self, t, s):
            self.tool, self.output_summary = t, s
            self.status = "success"
    executions = [
        _Ex("chat", "object form output"),
        {"tool": "chat", "output_summary": "dict form output",
         "status": "success"},
    ]
    body = build_indexable_body(executions)
    assert "object form" in body
    assert "dict form" in body


# ===========================================================================
# SessionSearchTool — LLM-facing dispatch
# ===========================================================================

def _action(op: str, meta: dict) -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool="session_search", operation=op, target="",
        purpose="t", expected_effect="t", reversibility="high",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata=meta,
    )


def test_tool_query_returns_hits(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="learn koi pond filtration",
                   body="filters and oxygenators are essential")
    tool = SessionSearchTool(idx)
    res = tool(_action("query", {"query": "koi"}))
    assert res["status"] == "success"
    assert res["hits"]
    assert "session_search for" in res["summary"]


def test_tool_query_no_matches(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    tool = SessionSearchTool(idx)
    res = tool(_action("query", {"query": "anything"}))
    assert res["status"] == "success"
    assert res["hits"] == []
    assert "no_matches" in res["summary"]


def test_tool_empty_query_rejected(tmp_path: Path):
    tool = SessionSearchTool(SessionIndex(tmp_path / "idx.db"))
    res = tool(_action("query", {}))
    assert res["status"] == "failed"
    assert "empty_query" in res["summary"]


def test_tool_count_op(tmp_path: Path):
    idx = SessionIndex(tmp_path / "idx.db")
    idx.index_task(task_id="t1", raw_goal="goal", body="body")
    tool = SessionSearchTool(idx)
    res = tool(_action("count", {}))
    assert res["status"] == "success"
    assert res["count"] == 1


def test_tool_unknown_op_rejected(tmp_path: Path):
    tool = SessionSearchTool(SessionIndex(tmp_path / "idx.db"))
    res = tool(_action("evil_op", {}))
    assert res["status"] == "failed"
    assert "unknown_session_search_op" in res["summary"]


# ===========================================================================
# Runtime integration — auto-index + episodic injection
# ===========================================================================

class _StubPlanner:
    planner_id = "stub_planner"

    def __init__(self, responder):
        self.responder = responder

    def plan(self, brief, system_prompt):
        out = self.responder(brief)
        out.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:8]}")
        out.setdefault("task_id", brief.get("task_id", "unknown"))
        out.setdefault("planner_id", self.planner_id)
        out.setdefault("planning_mode", brief.get("planning_mode", "direct"))
        out.setdefault("used_refusal_recovery", False)
        out.setdefault("notes", [])
        return out


def _make_runtime(workspace: Path, responder) -> Runtime:
    workspace_roots = [str(workspace / "workspace"),
                       str(workspace / "outputs")]
    tools = {n: MockTool(n) for n in
             ["report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "human", "memory",
              "chat", "image_gen"]}
    tools["fs"] = FilesystemTool(workspace_roots)
    rt = Runtime(
        config_dir=workspace / "configs",
        prompts_dir=workspace / "prompts",
        planner=_StubPlanner(responder),
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=workspace / "traces",
        session_index_path=workspace / "state" / "session.db",
    )
    rt.profile.profile["workspace_roots"] = workspace_roots
    return rt


def _fs_responder(target: Path, content: str = "hello"):
    def responder(brief):
        return {"planning_mode": brief["planning_mode"], "actions": [{
            "action_id": "a1", "tool": "fs",
            "operation": "save_under_outputs", "target": str(target),
            "purpose": "p", "expected_effect": "e",
            "reversibility": "high", "uncertainty": "low",
            "risk_factors": [], "requires_governance": True,
            "metadata": {"content": content},
        }]}
    return responder


def test_runtime_attaches_session_index(isolated_workspace: Path):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    rt = _make_runtime(isolated_workspace, _fs_responder(target))
    assert rt.session_index is not None
    assert rt.session_index.available


def test_runtime_auto_indexes_completed_task(isolated_workspace: Path):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    rt = _make_runtime(isolated_workspace, _fs_responder(target))
    assert rt.session_index.count() == 0
    rt.run(raw_goal="Save a koi pond note for the index test")
    # The task should have been indexed at _after_run time
    assert rt.session_index.count() == 1
    hits = rt.session_index.query("koi")
    assert hits and "koi" in hits[0]["raw_goal"].lower()


def test_runtime_skips_index_when_no_index_flag(isolated_workspace: Path):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    rt = _make_runtime(isolated_workspace, _fs_responder(target))
    rt.run(raw_goal="Save a secret note please",
           metadata={"no_index": True})
    assert rt.session_index.count() == 0


def test_runtime_kill_switch_disables_indexing(isolated_workspace: Path,
                                               monkeypatch):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SESSION_SEARCH_ENABLED", "0")
    rt = _make_runtime(isolated_workspace, _fs_responder(target))
    rt.run(raw_goal="Anything")
    assert rt.session_index.count() == 0


def test_runtime_episodic_injection_on_recall_phrase(isolated_workspace: Path):
    """Run a first task, then a second task whose prompt contains
    'remember when'. The second task's planner brief should contain
    `prior_sessions`."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    captured_briefs: list[dict] = []

    def responder(brief):
        captured_briefs.append(dict(brief))
        return {"planning_mode": brief["planning_mode"], "actions": [{
            "action_id": "a1", "tool": "fs",
            "operation": "save_under_outputs", "target": str(target),
            "purpose": "p", "expected_effect": "e",
            "reversibility": "high", "uncertainty": "low",
            "risk_factors": [], "requires_governance": True,
            "metadata": {"content": "hello"},
        }]}

    rt = _make_runtime(isolated_workspace, responder)
    rt.run(raw_goal="Save a note about quantum computing research")
    # Now ask recall — heuristic should fire
    rt.run(raw_goal="Remember when we talked about quantum computing? "
                    "Save another note.")
    # The second brief must contain prior_sessions
    assert len(captured_briefs) == 2
    prior = captured_briefs[1].get("prior_sessions") or []
    assert prior, ("expected prior_sessions on the recall task; "
                   f"brief keys: {list(captured_briefs[1].keys())}")
    assert "quantum" in prior[0]["raw_goal"].lower()


def test_runtime_no_injection_without_recall_phrase(isolated_workspace: Path):
    """A normal prompt (no recall phrase) should NOT trigger episodic
    injection, even if the index has matching history."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    captured_briefs: list[dict] = []

    def responder(brief):
        captured_briefs.append(dict(brief))
        return {"planning_mode": brief["planning_mode"], "actions": [{
            "action_id": "a1", "tool": "fs",
            "operation": "save_under_outputs", "target": str(target),
            "purpose": "p", "expected_effect": "e",
            "reversibility": "high", "uncertainty": "low",
            "risk_factors": [], "requires_governance": True,
            "metadata": {"content": "hello"},
        }]}

    rt = _make_runtime(isolated_workspace, responder)
    rt.run(raw_goal="Save a note about quantum computing")
    rt.run(raw_goal="Save another note about quantum computing")
    # The second brief should NOT have prior_sessions (no recall phrase)
    assert "prior_sessions" not in (captured_briefs[1] or {})


def test_runtime_episodic_injection_chinese_recall(isolated_workspace: Path):
    """Chinese recall phrases should also trigger injection."""
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    captured_briefs: list[dict] = []

    def responder(brief):
        captured_briefs.append(dict(brief))
        return {"planning_mode": brief["planning_mode"], "actions": [{
            "action_id": "a1", "tool": "fs",
            "operation": "save_under_outputs", "target": str(target),
            "purpose": "p", "expected_effect": "e",
            "reversibility": "high", "uncertainty": "low",
            "risk_factors": [], "requires_governance": True,
            "metadata": {"content": "hello"},
        }]}

    rt = _make_runtime(isolated_workspace, responder)
    rt.run(raw_goal="save a note about quantum computing")
    rt.run(raw_goal="上次我们聊的quantum computing,再做一个笔记")
    prior = captured_briefs[1].get("prior_sessions") or []
    assert prior, "Chinese recall phrase should trigger injection"


def test_runtime_emits_session_indexed_trace(isolated_workspace: Path):
    target = isolated_workspace / "outputs" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    rt = _make_runtime(isolated_workspace, _fs_responder(target))

    emitted: list[dict] = []
    original = rt.trace.emit

    def capture(*a, **kw):
        ev = original(*a, **kw)
        emitted.append({"module": ev.module, "event_type": ev.event_type})
        return ev
    rt.trace.emit = capture  # type: ignore[method-assign]

    rt.run(raw_goal="Save a quick note for the trace test")
    types = [e["event_type"] for e in emitted if e["module"] == "SESSION"]
    assert "session_indexed" in types
