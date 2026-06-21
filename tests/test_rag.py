"""RAG: indexer, retriever, sensitive-pattern exclusion, runtime injection."""
from __future__ import annotations

import json
from pathlib import Path

from teow_agl.adapters.mock_planner import MockPlanner
from teow_agl.policies.governance_profile import ProfileView
from teow_agl.rag.bm25 import BM25Index, tokenize
from teow_agl.rag.indexer import build_index, index_summary
from teow_agl.rag.retriever import Retriever


def test_tokenize_drops_stopwords():
    toks = tokenize("The quick brown fox jumps over the lazy dog")
    assert "the" not in toks
    assert "quick" in toks
    assert "fox" in toks


def test_bm25_ranks_relevant_doc_higher():
    idx = BM25Index()
    idx.add("d1", tokenize("Patent claim drafting requires careful attention to scope"))
    idx.add("d2", tokenize("My favorite recipe for chocolate chip cookies"))
    idx.add("d3", tokenize("Patent prosecution timelines vary by jurisdiction"))
    idx.finalize()
    hits = idx.score_query(tokenize("patent claim"), top_k=3)
    assert hits[0][0] == "d1"  # highest score


def test_indexer_produces_index_with_header(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.md").write_text("# Notes\n\nThis is a markdown note about patents.")
    (workspace / "log.txt").write_text("Some log lines about meetings.")

    profile_dict = {
        "workspace_roots": [str(workspace)],
        "sensitive_patterns": [],
    }
    profile = ProfileView(profile_dict)
    out = tmp_path / "rag" / "index.jsonl"
    header = build_index(roots=[str(workspace)], profile=profile, out_path=out)
    assert header["n_files"] == 2
    assert header["n_chunks"] >= 2
    assert out.exists()

    summary = index_summary(out)
    assert summary == header


def test_indexer_excludes_sensitive_patterns(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "private").mkdir(parents=True)
    (workspace / "private" / "secrets.md").write_text("admin password is hunter2")
    (workspace / "open_notes.md").write_text("public meeting notes")

    profile = ProfileView({
        "workspace_roots": [str(workspace)],
        "sensitive_patterns": ["**/private/**"],
    })
    out = tmp_path / "rag" / "index.jsonl"
    build_index(roots=[str(workspace)], profile=profile, out_path=out)

    # Read raw index — should not contain "hunter2"
    raw = out.read_text(encoding="utf-8")
    assert "hunter2" not in raw
    assert "meeting notes" in raw


def test_retriever_returns_relevant_chunks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "patent_notes.md").write_text(
        "Patent claim drafting requires careful attention to claim scope. "
        "When narrowing claims, consider both prior art and infringement strategy."
    )
    (workspace / "groceries.md").write_text("milk, bread, eggs, butter")
    profile = ProfileView({"workspace_roots": [str(workspace)], "sensitive_patterns": []})
    out = tmp_path / "rag" / "index.jsonl"
    build_index(roots=[str(workspace)], profile=profile, out_path=out)

    r = Retriever(out)
    assert r.loaded
    hits = r.query("patent claim drafting", top_k=2)
    assert hits, "expected at least one hit"
    assert "patent" in hits[0]["text"].lower() or "claim" in hits[0]["text"].lower()
    assert hits[0]["path"].endswith("patent_notes.md")


def test_retriever_empty_index_returns_no_hits(tmp_path: Path):
    r = Retriever(tmp_path / "missing_index.jsonl")
    assert not r.loaded
    assert r.query("anything") == []


def test_runtime_injects_relevant_context(make_runtime_factory, isolated_workspace: Path):
    """End-to-end: when a RAG index exists, the runtime adds relevant_context
    to the planning brief that 102 sees."""
    # build a tiny index over the isolated_workspace's workspace dir
    ws = isolated_workspace / "workspace"
    (ws / "ai_notes.md").write_text(
        "Notes on AI governance: external policy must remain sovereign. "
        "Universal hard safety stays inside the model; contextual governance lives outside."
    )
    profile_dict = {"workspace_roots": [str(ws)], "sensitive_patterns": []}
    out = isolated_workspace / "state" / "rag" / "index.jsonl"
    build_index(roots=[str(ws)], profile=ProfileView(profile_dict), out_path=out)

    # capture the brief the planner sees
    captured: dict = {}

    def responder(brief: dict) -> dict:
        captured.update(brief)
        return {
            "planning_mode": brief.get("planning_mode", "direct"),
            "actions": [
                {"action_id": "a1", "tool": "report", "operation": "draft_report",
                 "target": str(ws / "out.md"), "purpose": "summarize",
                 "expected_effect": "summary written",
                 "reversibility": "high", "uncertainty": "low",
                 "risk_factors": [], "requires_governance": True,
                 "metadata": {"topic": "AI governance summary"}}
            ],
        }
    rt = make_runtime_factory(planner=MockPlanner(responder=responder), gate="approve_all")
    # rebuild with rag_index_path
    from teow_agl.runtime import Runtime
    from teow_agl.tools.filesystem_tools import FilesystemTool
    from teow_agl.tools.report_tools import ReportTool
    from teow_agl.tools.mock_tools import MockTool
    from teow_agl.modules.module_105_human_gate import HumanGate
    workspace_roots = rt.profile.workspace_roots
    tools = {n: MockTool(n) for n in ["fs","report","docx","pptx","xlsx","desktop","gui","email","publish","code","shell","human"]}
    tools["fs"] = FilesystemTool(workspace_roots)
    tools["report"] = ReportTool()
    rt2 = Runtime(
        config_dir=isolated_workspace / "configs",
        prompts_dir=isolated_workspace / "prompts",
        planner=MockPlanner(responder=responder),
        tool_registry=tools,
        human_gate=HumanGate("approve_all"),
        trace_dir=isolated_workspace / "traces",
        rag_index_path=out,
    )
    rt2.profile.profile["workspace_roots"] = workspace_roots
    rt2.run(raw_goal="Summarize my AI governance notes")

    assert "relevant_context" in captured
    assert isinstance(captured["relevant_context"], list)
    assert len(captured["relevant_context"]) >= 1
    assert "governance" in captured["relevant_context"][0]["text"].lower()
