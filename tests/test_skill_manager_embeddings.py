"""SkillManager embedding persistence + cosine retrieval — Phase 2 L4.3.

Test surface:
  * create_skill persists an embedding when the provider is available
  * create_skill still succeeds when the provider returns None
  * find_relevant uses cosine for embedded skills, BM25 for the rest
  * find_relevant falls back to pure BM25 when provider unavailable
  * archive_skill leaves the .embedding.json on disk untouched
  * pop_audit_events surfaces the embedding outcome to runtime callers

OpenAI HTTP is fully mocked — these tests never spend a token.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from teow_agl.modules.module_skill_manager import (
    SkillManager,
    _EMBEDDING_SUFFIX,
)
from teow_agl.util import embeddings as emb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _constraints() -> dict:
    return {
        "creation_limits": {
            "max_skills_per_task": 1,
            "max_chars_per_skill": 4000,
            "min_chars_per_skill": 60,
            "max_total_skills": 50,
        },
        "retrieval": {
            "top_k_injected": 3,
            "min_score_for_injection": 0.0,
        },
        "lifecycle": {"stale_after_days": 30,
                      "auto_archive_enabled": False,
                      "auto_delete_enabled": False},
        "forbidden_patterns": {"patterns": []},
        "min_task_quality": {
            "require_blue_or_green_route": True,
            "min_executions": 1,
            "skip_if_verification_failed": True,
        },
    }


def _good_quality() -> dict:
    return {"task_id": "task_test", "final_route": "BLUE",
            "verification_failed": False, "execution_success_count": 1}


@pytest.fixture
def sm(tmp_path: Path) -> SkillManager:
    return SkillManager(tmp_path / "skills", constraints=_constraints())


@pytest.fixture(autouse=True)
def _reset_embedding_dim_cache():
    emb._reset_dim_cache_for_tests()
    yield
    emb._reset_dim_cache_for_tests()


def _fake_embed(text_to_vec: dict[str, list[float]]):
    """Return a `_embed_via_provider` replacement that maps inputs to
    canned vectors. Unknown inputs get [0, 0, 0]."""

    def _impl(texts: list[str]) -> list[list[float]]:
        return [text_to_vec.get(t, [0.0, 0.0, 0.0]) for t in texts]

    return _impl


# ===========================================================================
# create_skill — embedding persistence
# ===========================================================================

def test_create_skill_persists_embedding_when_available(
    sm: SkillManager, monkeypatch,
):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda texts: [[0.1] * 1536] * len(texts))

    out = sm.create_skill(
        name="save-as-docx",
        description="Save the agent output as a Word document.",
        procedure="1. Build text.\n2. Call docx.save.\n3. Verify file.\n"
                  "4. Return path.",
        task_id="t1", task_quality=_good_quality(),
        tags=["docx", "office"],
    )
    assert out["ok"] is True
    assert out["embedding_persisted"] is True

    embedding_path = sm.dir / f"SKILL_{out['skill_id']}{_EMBEDDING_SUFFIX}"
    assert embedding_path.is_file()
    payload = json.loads(embedding_path.read_text(encoding="utf-8"))
    assert payload["skill_id"] == out["skill_id"]
    assert payload["dim"] == 1536
    assert len(payload["vector"]) == 1536


def test_create_skill_succeeds_when_embedding_fails(
    sm: SkillManager, monkeypatch,
):
    """Provider returning None must NOT block skill creation — Phase 2
    failure-isolation contract."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(emb, "_embed_via_provider", lambda _: None)

    out = sm.create_skill(
        name="n", description="d",
        procedure="x" * 100,
        task_id="t1", task_quality=_good_quality(),
    )
    assert out["ok"] is True
    assert out["embedding_persisted"] is False

    embedding_path = sm.dir / f"SKILL_{out['skill_id']}{_EMBEDDING_SUFFIX}"
    assert not embedding_path.exists()

    # Audit event should explain why
    events = sm.pop_audit_events()
    kinds = [k for k, _ in events]
    assert "skill_embedding_failed" in kinds


def test_create_skill_skips_embed_when_provider_unavailable(
    sm: SkillManager, monkeypatch,
):
    """Provider explicitly disabled → skip silently, audit event says so."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    out = sm.create_skill(
        name="n", description="d",
        procedure="x" * 100,
        task_id="t1", task_quality=_good_quality(),
    )
    assert out["ok"] is True
    assert out["embedding_persisted"] is False
    kinds = [k for k, _ in sm.pop_audit_events()]
    assert "skill_embedding_skipped" in kinds


def test_pop_audit_events_clears_after_read(
    sm: SkillManager, monkeypatch,
):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    sm.create_skill(name="n", description="d", procedure="x" * 100,
                    task_id="t", task_quality=_good_quality())
    first = sm.pop_audit_events()
    second = sm.pop_audit_events()
    assert len(first) >= 1
    assert second == []


# ===========================================================================
# load_embedding — defensive
# ===========================================================================

def test_load_embedding_missing_file_returns_none(sm: SkillManager):
    assert sm.load_embedding("skill_aaaaaaaaaaaa") is None


def test_load_embedding_invalid_skill_id_returns_none(sm: SkillManager):
    assert sm.load_embedding("not-a-real-id") is None
    assert sm.load_embedding("") is None


# ===========================================================================
# Phase 2 L4.4 — Principle / Parameters persistence
# ===========================================================================

def _body_text(sm: SkillManager, skill_id: str) -> str:
    return (sm.dir / f"SKILL_{skill_id}.md").read_text(encoding="utf-8")


def test_create_skill_persists_principle_in_record_and_body(
    sm: SkillManager, monkeypatch,
):
    """A skill created with a principle + parameters writes a 3-section
    body (Principle / Parameters / Procedure) and records the provenance."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    out = sm.create_skill(
        name="save-as-docx",
        description="Save the agent output as a Word document.",
        procedure="1. Build text.\n2. Call docx.save.\n3. Verify file.\n"
                  "4. Return path.",
        task_id="t1", task_quality=_good_quality(),
        tags=["docx", "office"],
        principle="Persist generated text into a portable document and "
                  "verify the artifact exists.",
        parameters={"tool": "docx", "output_format": "docx",
                    "output_language": "en"},
        abstraction_model="openai:gpt-4o-mini",
    )
    assert out["ok"] is True

    rec = next(r for r in sm.list_skills()
               if r["skill_id"] == out["skill_id"])
    assert rec["principle"].startswith("Persist generated text")
    assert rec["has_principle"] is True
    assert rec["parameters_count"] == 3
    assert rec["abstraction_model"] == "openai:gpt-4o-mini"

    body = _body_text(sm, out["skill_id"])
    assert "## Principle" in body
    assert "## Parameters" in body
    assert "```json" in body
    assert "## Procedure" in body
    # Principle section comes before Procedure
    assert body.index("## Principle") < body.index("## Procedure")


def test_create_skill_without_principle_is_single_section(
    sm: SkillManager, monkeypatch,
):
    """Legacy / no-abstraction path keeps the old single-section layout
    and records empty principle metadata."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    out = sm.create_skill(
        name="n", description="d", procedure="x" * 100,
        task_id="t1", task_quality=_good_quality(),
    )
    assert out["ok"] is True
    rec = next(r for r in sm.list_skills()
               if r["skill_id"] == out["skill_id"])
    assert rec["principle"] == ""
    assert rec["has_principle"] is False
    assert rec["parameters_count"] == 0

    body = _body_text(sm, out["skill_id"])
    assert "## Principle" not in body
    assert "## Parameters" not in body
    assert "## Procedure" in body


def test_create_skill_embeds_principle_when_present(
    sm: SkillManager, monkeypatch,
):
    """When a principle exists, the retrieval embedding is computed from
    the principle text (name + principle + tags), NOT the description."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    seen: list[str] = []

    def _capture(texts: list[str]) -> list[list[float]]:
        seen.extend(texts)
        return [[0.2] * 1536 for _ in texts]

    monkeypatch.setattr(emb, "_embed_via_provider", _capture)

    out = sm.create_skill(
        name="make-doc",
        description="DESCRIPTION_TOKEN_THAT_SHOULD_NOT_BE_EMBEDDED",
        procedure="1. a\n2. b\n3. c\n4. d" + "x" * 60,
        task_id="t1", task_quality=_good_quality(),
        tags=["office"],
        principle="PRINCIPLE_TOKEN generate and verify an artifact.",
    )
    assert out["ok"] is True and out["embedding_persisted"] is True
    embedded = " ".join(seen)
    assert "PRINCIPLE_TOKEN" in embedded
    assert "DESCRIPTION_TOKEN_THAT_SHOULD_NOT_BE_EMBEDDED" not in embedded


def test_load_embedding_malformed_json_returns_none(
    sm: SkillManager, monkeypatch,
):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.5, 0.5, 0.5]])
    out = sm.create_skill(name="n", description="d", procedure="x" * 100,
                          task_id="t", task_quality=_good_quality())
    # Corrupt the embedding file
    path = sm.dir / f"SKILL_{out['skill_id']}{_EMBEDDING_SUFFIX}"
    path.write_text("not json at all", encoding="utf-8")
    assert sm.load_embedding(out["skill_id"]) is None


# ===========================================================================
# find_relevant — cosine route (with controlled vectors)
# ===========================================================================

def test_find_relevant_uses_cosine_when_provider_available(
    sm: SkillManager, monkeypatch,
):
    """Skill A's embedding is aligned with the query; Skill B's isn't.
    Cosine route should rank A first regardless of BM25 keyword overlap."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Two skills with DIFFERENT vectors:
    vectors = {
        "a-skill irrelevant text everywhere": [1.0, 0.0, 0.0],
        "b-skill another irrelevant string": [0.0, 1.0, 0.0],
        "save a docx file": [0.99, 0.01, 0.0],  # query — close to A
    }

    def _route(texts: list[str]) -> list[list[float]]:
        return [vectors.get(t.strip(), [0.0, 0.0, 0.0]) for t in texts]

    monkeypatch.setattr(emb, "_embed_via_provider", _route)

    out_a = sm.create_skill(
        name="a-skill", description="irrelevant text everywhere",
        procedure="x" * 100, task_id="t1", task_quality=_good_quality(),
        tags=[],
    )
    out_b = sm.create_skill(
        name="b-skill", description="another irrelevant string",
        procedure="y" * 100, task_id="t2", task_quality=_good_quality(),
        tags=[],
    )
    assert out_a["embedding_persisted"]
    assert out_b["embedding_persisted"]

    hits = sm.find_relevant("save a docx file", top_k=3,
                            min_score=0.0)
    # A is first because cosine(query, A) > cosine(query, B)
    assert len(hits) >= 1
    assert hits[0]["skill_id"] == out_a["skill_id"]
    assert hits[0]["rank_method"] == "cosine"
    assert hits[0]["score"] > 0.9


def test_find_relevant_falls_back_to_bm25_when_provider_off(
    sm: SkillManager, monkeypatch,
):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.7] * 3] * 1)

    out = sm.create_skill(
        name="docx-saver", description="Save text as docx",
        procedure="x" * 100, task_id="t", task_quality=_good_quality(),
        tags=["office", "docx"],
    )
    assert out["embedding_persisted"]

    # Now disable embedding lane — find_relevant must fall back to BM25
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    hits = sm.find_relevant("docx", top_k=3, min_score=0.0)
    assert len(hits) == 1
    assert hits[0]["rank_method"] == "bm25"


def test_find_relevant_query_embed_failure_falls_back_to_bm25(
    sm: SkillManager, monkeypatch,
):
    """Provider says available, but the QUERY embed call returns None
    (e.g. transient API error) → BM25 fallback for the whole query."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Phase 1: create skill with working embedding
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.5, 0.5, 0.5]])
    sm.create_skill(name="docx-saver", description="Save as docx",
                    procedure="x" * 100, task_id="t",
                    task_quality=_good_quality(), tags=["docx"])

    # Phase 2: query-time embed fails
    monkeypatch.setattr(emb, "_embed_via_provider", lambda _: None)
    hits = sm.find_relevant("docx", top_k=3, min_score=0.0)
    assert len(hits) == 1
    assert hits[0]["rank_method"] == "bm25"


def test_find_relevant_mixed_skills_some_without_embedding(
    sm: SkillManager, monkeypatch,
):
    """Two skills: A has embedding (cosine ranks it), B was created
    before Phase 2 / with embedding disabled (no embedding file).
    Both should be returnable — A via cosine, B via BM25 fallback."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Skill A: create with embedding
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.9, 0.1, 0.0]])
    out_a = sm.create_skill(
        name="alpha-skill", description="alpha description",
        procedure="x" * 100, task_id="t1",
        task_quality=_good_quality(), tags=[],
    )

    # Skill B: create with embedding lane "off" so no file is persisted
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    out_b = sm.create_skill(
        name="beta-skill", description="beta description with docx keyword",
        procedure="y" * 100, task_id="t2",
        task_quality=_good_quality(), tags=["docx"],
    )

    # Query: turn embedding back on so cosine ranks A; B has no embed
    # so it should come in via the BM25 fill pass.
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")

    def _route(texts: list[str]) -> list[list[float]]:
        # Query text → cosine close to A
        return [[0.95, 0.05, 0.0] for _ in texts]

    monkeypatch.setattr(emb, "_embed_via_provider", _route)

    hits = sm.find_relevant("docx", top_k=3, min_score=0.01)
    ids = [h["skill_id"] for h in hits]
    methods = {h["skill_id"]: h["rank_method"] for h in hits}
    assert out_a["skill_id"] in ids
    assert out_b["skill_id"] in ids
    assert methods[out_a["skill_id"]] == "cosine"
    assert methods[out_b["skill_id"]] == "bm25"


def test_find_relevant_empty_input_returns_empty(sm: SkillManager):
    assert sm.find_relevant("") == []
    assert sm.find_relevant("anything") == []  # no skills yet


def test_find_relevant_respects_min_score(
    sm: SkillManager, monkeypatch,
):
    """Cosine below threshold drops the hit."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.1, 0.1, 0.1]])
    sm.create_skill(name="weak-skill", description="vague keywords",
                    procedure="x" * 100, task_id="t",
                    task_quality=_good_quality(), tags=[])

    # Query whose embedding is orthogonal → cosine near 0
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.0, 0.0, 1.0]])
    hits = sm.find_relevant("unrelated query", top_k=3, min_score=0.9)
    assert hits == []  # below threshold + BM25 won't match either


# ===========================================================================
# archive_skill keeps the embedding file
# ===========================================================================

def test_archive_skill_preserves_embedding_file(
    sm: SkillManager, monkeypatch,
):
    """Archive is soft delete — the .embedding.json stays so if the
    skill ever gets un-archived it doesn't need re-embedding."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[0.3, 0.3, 0.3]])
    out = sm.create_skill(name="n", description="d", procedure="x" * 100,
                          task_id="t", task_quality=_good_quality())
    embedding_path = sm.dir / f"SKILL_{out['skill_id']}{_EMBEDDING_SUFFIX}"
    assert embedding_path.is_file()

    sm.archive_skill(out["skill_id"])
    assert embedding_path.is_file()  # still there
