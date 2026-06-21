"""SubjectConfidence: per-category outcome ledger + Laplace-smoothed score."""
from __future__ import annotations

from pathlib import Path

from teow_agl.policies.subject_confidence import SubjectConfidence


def test_unknown_category_returns_neutral(tmp_path: Path):
    sc = SubjectConfidence(tmp_path / "sc.jsonl")
    assert sc.score("never_seen") == 0.5
    assert sc.observations("never_seen") == 0
    assert sc.is_confident("never_seen") is False


def test_score_rises_with_successes(tmp_path: Path):
    sc = SubjectConfidence(tmp_path / "sc.jsonl", min_observations_for_confident=3)
    for _ in range(5):
        sc.record(category="docx", outcome="success", task_id="t")
    assert sc.score("docx") > 0.7
    assert sc.observations("docx") == 5
    assert sc.is_confident("docx") is True


def test_score_falls_with_failures(tmp_path: Path):
    sc = SubjectConfidence(tmp_path / "sc.jsonl", min_observations_for_confident=3)
    for _ in range(5):
        sc.record(category="bad", outcome="failure", task_id="t")
    assert sc.score("bad") < 0.5
    assert sc.is_confident("bad") is False


def test_rejection_lowers_score(tmp_path: Path):
    sc = SubjectConfidence(tmp_path / "sc.jsonl")
    sc.record(category="email", outcome="success")
    sc.record(category="email", outcome="success")
    sc.record(category="email", outcome="human_rejected")
    sc.record(category="email", outcome="human_rejected")
    s = sc.score("email")
    assert 0.3 < s < 0.6  # mixed signal


def test_persistence_across_instances(tmp_path: Path):
    path = tmp_path / "sc.jsonl"
    sc1 = SubjectConfidence(path)
    sc1.record(category="x", outcome="success")
    sc1.record(category="x", outcome="success")
    sc1.record(category="x", outcome="success")
    sc1.record(category="x", outcome="success")
    sc2 = SubjectConfidence(path)
    assert sc2.observations("x") == 4
    assert sc2.is_confident("x") is True


def test_snapshot_returns_aggregates(tmp_path: Path):
    sc = SubjectConfidence(tmp_path / "sc.jsonl")
    sc.record(category="a", outcome="success")
    sc.record(category="b", outcome="failure")
    snap = sc.snapshot()
    assert "a" in snap and "b" in snap
    assert snap["a"]["successes"] == 1
    assert snap["b"]["failures"] == 1
