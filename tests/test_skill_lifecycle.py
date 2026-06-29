"""Phase 2 L4.8 — 4-state skill lifecycle (active/stale/superseded/archived).

Test surface:
  * transitions: mark_stale / mark_superseded / restore_skill — legal
    paths, illegal-transition guards, invalid id, not-found, no-op
  * _retrieval_weight: active 1.0 / stale 0.5 / superseded 0.0 / archived 0.0
    + config override
  * list_skills(statuses=...) filtering + include_archived backward-compat
  * find_relevant: excludes superseded + archived; includes stale at
    reduced weight; an ACTIVE skill outranks an equally-keyworded STALE one
  * lifecycle_sweep: time-based ACTIVE→STALE auto-apply; STALE→ARCHIVED and
    ACTIVE→SUPERSEDED proposals; honours config day thresholds
  * runtime: _apply_curator_proposal mark_skill_stale / supersede_skill
    branches; run_skill_lifecycle queues proposals + honours kill-switch

No OpenAI tokens are spent — embedding provider is left unavailable so
find_relevant rides the pure-BM25 lane (weighting applies identically).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from teow_agl.modules.module_skill_manager import SkillManager

from tests.conftest import make_runtime


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _constraints(**lifecycle_overrides) -> dict:
    lifecycle = {
        "stale_after_days": 30,
        "active_to_stale_days_unused": 90,
        "stale_to_archived_days_unused": 180,
        "supersede_on_new_skill_same_shape": True,
        "stale_retrieval_weight": 0.5,
        "superseded_retrieval_weight": 0.0,
        "auto_archive_enabled": False,
        "auto_delete_enabled": False,
    }
    lifecycle.update(lifecycle_overrides)
    return {
        "creation_limits": {
            "max_skills_per_task": 1,
            "max_chars_per_skill": 4000,
            "min_chars_per_skill": 10,
            "max_total_skills": 200,
        },
        "retrieval": {
            "top_k_injected": 5,
            "min_score_for_injection": 0.0,
        },
        "lifecycle": lifecycle,
        "forbidden_patterns": {"patterns": []},
        "min_task_quality": {
            "require_blue_or_green_route": True,
            "min_executions": 1,
            "skip_if_verification_failed": True,
        },
    }


def _good_quality() -> dict:
    return {"final_route": "BLUE", "verification_failed": False,
            "execution_success_count": 1}


@pytest.fixture
def sm(tmp_path: Path) -> SkillManager:
    return SkillManager(tmp_path / "skills", constraints=_constraints())


def _make(sm: SkillManager, name: str, *, desc: str = "",
          proc: str = "", category: str = "", shape: str = "",
          tags=None) -> str:
    out = sm.create_skill(
        name=name,
        description=desc or f"{name} description",
        procedure=proc or f"When the user wants {name}, do the {name} steps "
                          "carefully and verify the artifact." * 2,
        task_id="task_t", task_quality=_good_quality(),
        tags=tags or [],
        source_category=category, source_shape=shape,
    )
    assert out["ok"], out
    return out["skill_id"]


def _status(sm: SkillManager, sid: str) -> str:
    rec = next(r for r in sm.list_skills(statuses=SkillManager._VALID_STATUSES)
               if r["skill_id"] == sid)
    return rec["status"]


# ===========================================================================
# transitions
# ===========================================================================

def test_mark_stale_active_to_stale(sm: SkillManager):
    sid = _make(sm, "alpha")
    res = sm.mark_stale(sid)
    assert res["ok"] and res["status"] == "stale"
    assert _status(sm, sid) == "stale"
    # No longer in the active-only default list
    assert sid not in [s["skill_id"] for s in sm.list_skills()]


def test_mark_stale_idempotent_noop(sm: SkillManager):
    sid = _make(sm, "alpha")
    sm.mark_stale(sid)
    res = sm.mark_stale(sid)
    assert res["ok"] and res.get("noop") is True


def test_mark_stale_invalid_and_missing(sm: SkillManager):
    assert sm.mark_stale("not-an-id")["error"] == "invalid_skill_id"
    assert sm.mark_stale("skill_doesnotexist")["error"] == "skill_not_found"


def test_mark_superseded_records_superseded_by(sm: SkillManager):
    old = _make(sm, "old")
    new = _make(sm, "new")
    res = sm.mark_superseded(old, superseded_by=new)
    assert res["ok"] and res["status"] == "superseded"
    rec = next(r for r in sm.list_skills(statuses=("superseded",))
               if r["skill_id"] == old)
    assert rec["superseded_by"] == new


def test_mark_superseded_from_stale_is_legal(sm: SkillManager):
    sid = _make(sm, "alpha")
    sm.mark_stale(sid)
    res = sm.mark_superseded(sid, superseded_by="")
    assert res["ok"] and res["status"] == "superseded"


def test_mark_superseded_from_archived_is_illegal(sm: SkillManager):
    sid = _make(sm, "alpha")
    sm.archive_skill(sid)
    res = sm.mark_superseded(sid)
    assert not res["ok"]
    assert "illegal_transition" in res["error"]


def test_restore_from_each_terminal_state(sm: SkillManager):
    a, b, c = _make(sm, "a"), _make(sm, "b"), _make(sm, "c")
    sm.mark_stale(a)
    sm.mark_superseded(b)
    sm.archive_skill(c)
    for sid in (a, b, c):
        res = sm.restore_skill(sid)
        assert res["ok"] and res["status"] == "active", (sid, res)
        assert _status(sm, sid) == "active"


def test_restore_active_is_noop(sm: SkillManager):
    sid = _make(sm, "alpha")
    res = sm.restore_skill(sid)
    assert res["ok"] and res.get("noop") is True


# ===========================================================================
# retrieval weight
# ===========================================================================

def test_retrieval_weight_defaults(sm: SkillManager):
    assert sm._retrieval_weight("active") == 1.0
    assert sm._retrieval_weight("stale") == 0.5
    assert sm._retrieval_weight("superseded") == 0.0
    assert sm._retrieval_weight("archived") == 0.0
    assert sm._retrieval_weight("garbage") == 0.0


def test_retrieval_weight_config_override(tmp_path: Path):
    sm = SkillManager(tmp_path / "s",
                      constraints=_constraints(stale_retrieval_weight=0.25,
                                               superseded_retrieval_weight=0.1))
    assert sm._retrieval_weight("stale") == 0.25
    assert sm._retrieval_weight("superseded") == 0.1


# ===========================================================================
# list_skills filtering
# ===========================================================================

def test_list_skills_statuses_filter(sm: SkillManager):
    a, b, c, d = (_make(sm, "a"), _make(sm, "b"),
                  _make(sm, "c"), _make(sm, "d"))
    sm.mark_stale(b)
    sm.mark_superseded(c)
    sm.archive_skill(d)
    active = {s["skill_id"] for s in sm.list_skills(statuses=("active",))}
    assert active == {a}
    both = {s["skill_id"] for s in sm.list_skills(statuses=("active", "stale"))}
    assert both == {a, b}
    every = {s["skill_id"]
             for s in sm.list_skills(statuses=SkillManager._VALID_STATUSES)}
    assert every == {a, b, c, d}


def test_list_skills_include_archived_backward_compat(sm: SkillManager):
    a = _make(sm, "a")
    b = _make(sm, "b")
    sm.mark_stale(b)
    # Default (active-only) — stale is excluded just like archived
    assert {s["skill_id"] for s in sm.list_skills()} == {a}
    # include_archived=True returns everything (incl. stale)
    assert {s["skill_id"]
            for s in sm.list_skills(include_archived=True)} == {a, b}


# ===========================================================================
# find_relevant weighting
# ===========================================================================

def test_find_relevant_excludes_superseded_and_archived(sm: SkillManager):
    keep = _make(sm, "essay-research",
                 desc="Write a researched essay with web citations")
    sup = _make(sm, "essay-superseded",
                desc="Write a researched essay with web citations")
    arc = _make(sm, "essay-archived",
                desc="Write a researched essay with web citations")
    sm.mark_superseded(sup)
    sm.archive_skill(arc)
    hits = sm.find_relevant("write a researched essay", top_k=5)
    ids = {h["skill_id"] for h in hits}
    assert keep in ids
    assert sup not in ids
    assert arc not in ids


def test_find_relevant_includes_stale_at_reduced_weight(sm: SkillManager):
    sid = _make(sm, "essay-research",
                desc="Write a researched essay with web citations")
    active_hits = sm.find_relevant("write a researched essay", top_k=5)
    active_score = next(h["score"] for h in active_hits if h["skill_id"] == sid)

    sm.mark_stale(sid)
    stale_hits = sm.find_relevant("write a researched essay", top_k=5)
    assert stale_hits, "stale skill must still be retrievable"
    stale_score = next(h["score"] for h in stale_hits if h["skill_id"] == sid)
    # Weight 0.5 → exactly half the raw score (BM25 lane).
    assert stale_score == pytest.approx(active_score * 0.5, rel=1e-6)


def test_find_relevant_active_outranks_equal_stale(sm: SkillManager):
    desc = "Write a researched essay with web citations"
    active = _make(sm, "essay-active", desc=desc)
    stale = _make(sm, "essay-stale", desc=desc)
    sm.mark_stale(stale)
    hits = sm.find_relevant("write a researched essay", top_k=5)
    ids = [h["skill_id"] for h in hits]
    assert ids[0] == active
    assert stale in ids  # still present, just ranked lower


# ===========================================================================
# lifecycle_sweep
# ===========================================================================

def test_sweep_marks_unused_active_stale(sm: SkillManager):
    sid = _make(sm, "alpha")
    future = datetime.now(timezone.utc) + timedelta(days=100)
    out = sm.lifecycle_sweep(now=future)
    assert sid in out["marked_stale"]
    assert _status(sm, sid) == "stale"


def test_sweep_does_not_mark_recent_active(sm: SkillManager):
    sid = _make(sm, "alpha")
    out = sm.lifecycle_sweep(now=datetime.now(timezone.utc) + timedelta(days=10))
    assert sid not in out["marked_stale"]
    assert _status(sm, sid) == "active"


def test_sweep_proposes_supersede_for_same_shape(sm: SkillManager):
    old = _make(sm, "doc-old", category="office_doc", shape="docx_v1")
    new = _make(sm, "doc-new", category="office_doc", shape="docx_v1")
    out = sm.lifecycle_sweep()
    sup = [p for p in out["proposals"] if p["type"] == "supersede_skill"]
    assert len(sup) == 1
    assert sup[0]["skill_id"] == old          # older one superseded
    assert sup[0]["superseded_by"] == new
    # Proposal only — not auto-applied
    assert _status(sm, old) == "active"


def test_supersede_winner_deterministic_on_created_at_tie(sm: SkillManager):
    """Regression (flaky-test root cause): two same-shape skills created in the
    SAME coarse-clock tick get an IDENTICAL created_at (common on Windows, where
    datetime.now() can repeat a microsecond). The "newest wins" sort must then
    break the tie deterministically by append/creation order (later == newer),
    superseding the OLDER one — never flip by hash/iteration order."""
    TIE = "2026-01-01T12:00:00+00:00"
    old = {"skill_id": "OLD", "source_category": "office_doc",
           "source_shape": "docx_v1", "created_at": TIE, "updated_at": TIE,
           "status": "active"}
    new = {"skill_id": "NEW", "source_category": "office_doc",
           "source_shape": "docx_v1", "created_at": TIE, "updated_at": TIE,
           "status": "active"}
    # _replay() reflects append (creation) order: old first, new second.
    sm._replay = lambda: [old, new]  # type: ignore[method-assign]
    # The result must hold regardless of what order list_skills happens to yield.
    for listed in ([old, new], [new, old]):
        sm.list_skills = lambda _l=listed, **k: list(_l)  # type: ignore[method-assign]
        cands = sm._detect_supersede_candidates()
        assert cands == [{"skill_id": "OLD", "superseded_by": "NEW"}], (listed, cands)


def test_sweep_no_supersede_without_shape(sm: SkillManager):
    _make(sm, "a", category="office_doc", shape="")
    _make(sm, "b", category="office_doc", shape="")
    out = sm.lifecycle_sweep()
    assert [p for p in out["proposals"] if p["type"] == "supersede_skill"] == []


def test_sweep_proposes_archive_for_long_unused_stale(sm: SkillManager):
    sid = _make(sm, "alpha")
    sm.mark_stale(sid)
    future = datetime.now(timezone.utc) + timedelta(days=200)
    out = sm.lifecycle_sweep(now=future)
    arch = [p for p in out["proposals"]
            if p["type"] == "archive_skill" and p["skill_id"] == sid]
    assert len(arch) == 1
    # Proposal only — still stale, not archived
    assert _status(sm, sid) == "stale"


def test_sweep_supersede_disabled_by_config(tmp_path: Path):
    sm = SkillManager(tmp_path / "s",
                      constraints=_constraints(
                          supersede_on_new_skill_same_shape=False))
    _make(sm, "doc-old", category="office_doc", shape="docx_v1")
    _make(sm, "doc-new", category="office_doc", shape="docx_v1")
    out = sm.lifecycle_sweep()
    assert [p for p in out["proposals"] if p["type"] == "supersede_skill"] == []


# ===========================================================================
# runtime integration
# ===========================================================================

def _runtime_with_sm(isolated_workspace: Path) -> tuple:
    rt = make_runtime(isolated_workspace)
    sm = SkillManager(isolated_workspace / "state" / "skills",
                      constraints=_constraints())
    rt.skill_manager = sm
    return rt, sm


def test_apply_proposal_mark_skill_stale(isolated_workspace: Path):
    rt, sm = _runtime_with_sm(isolated_workspace)
    sid = _make(sm, "alpha")
    ok, reason = rt._apply_curator_proposal(
        {"type": "mark_skill_stale", "skill_id": sid})
    assert ok, reason
    assert _status(sm, sid) == "stale"


def test_apply_proposal_supersede_skill(isolated_workspace: Path):
    rt, sm = _runtime_with_sm(isolated_workspace)
    old = _make(sm, "old")
    new = _make(sm, "new")
    ok, reason = rt._apply_curator_proposal(
        {"type": "supersede_skill", "skill_id": old, "superseded_by": new})
    assert ok, reason
    assert _status(sm, old) == "superseded"
    rec = next(r for r in sm.list_skills(statuses=("superseded",))
               if r["skill_id"] == old)
    assert rec["superseded_by"] == new


def test_run_skill_lifecycle_queues_proposals(isolated_workspace: Path):
    rt, sm = _runtime_with_sm(isolated_workspace)
    _make(sm, "doc-old", category="office_doc", shape="docx_v1")
    _make(sm, "doc-new", category="office_doc", shape="docx_v1")
    res = rt.run_skill_lifecycle()
    assert res["ok"]
    sup = [p for p in res["proposals"] if p["type"] == "supersede_skill"]
    assert len(sup) == 1
    # Queued into the shared curator-proposal store with pending status
    pending = rt.list_curator_proposals(status="pending")
    assert any(p["type"] == "supersede_skill" for p in pending)
    assert all(p.get("proposal_id") for p in res["proposals"])


def test_run_skill_lifecycle_respects_kill_switch(isolated_workspace: Path,
                                                  monkeypatch):
    rt, sm = _runtime_with_sm(isolated_workspace)
    _make(sm, "alpha")
    monkeypatch.setenv("CURATOR_ENABLED", "0")
    res = rt.run_skill_lifecycle()
    assert not res["ok"]
    assert res["error"] == "curator_disabled_via_env"


def test_run_skill_lifecycle_end_to_end_approval(isolated_workspace: Path):
    """Queue a supersede proposal, approve it via the human-gate path,
    and confirm the older skill ends up SUPERSEDED."""
    rt, sm = _runtime_with_sm(isolated_workspace)
    old = _make(sm, "doc-old", category="office_doc", shape="docx_v1")
    _make(sm, "doc-new", category="office_doc", shape="docx_v1")
    rt.run_skill_lifecycle()
    prop = next(p for p in rt.list_curator_proposals(status="pending")
                if p["type"] == "supersede_skill")
    ok, reason, updated = rt.decide_curator_proposal(
        prop["proposal_id"], "approved")
    assert ok, reason
    assert updated["status"] == "applied"
    assert _status(sm, old) == "superseded"
