"""PlanCache: shape-keyed templates, threshold activation, invalidation on failure."""
from __future__ import annotations

from pathlib import Path

from teow_agl.policies.plan_cache import PlanCache, shape_signature


def _docx_action():
    return {
        "tool": "docx", "operation": "save_under_outputs",
        "target": "outputs/x.docx", "purpose": "draft docx",
        "reversibility": "high", "uncertainty": "low",
        "requires_governance": True,
        "metadata": {"title": "Hello", "body": "world"},
    }


def test_shape_signature_is_deterministic_and_order_sensitive():
    a1 = [{"tool": "docx", "operation": "save_under_outputs"}]
    a2 = [{"tool": "docx", "operation": "save_under_outputs"}]
    assert shape_signature(a1) == shape_signature(a2)
    a3 = [{"tool": "fs", "operation": "save_under_outputs"}]
    assert shape_signature(a1) != shape_signature(a3)


def test_warming_until_threshold(tmp_path: Path):
    pc = PlanCache(tmp_path / "pc.jsonl", min_successes_for_cache=3)
    actions = [_docx_action()]
    # First success — warming
    e1 = pc.record_success(category="office_doc_generation", actions_dump=actions)
    assert e1["status"] == "warming"
    assert e1["successes"] == 1
    # Lookup should NOT return a warming entry as active
    assert pc.lookup(category="office_doc_generation") is None
    # Second
    pc.record_success(category="office_doc_generation", actions_dump=actions)
    assert pc.lookup(category="office_doc_generation") is None
    # Third — flips to active
    e3 = pc.record_success(category="office_doc_generation", actions_dump=actions)
    assert e3["status"] == "active"
    assert pc.lookup(category="office_doc_generation") is not None


def test_failure_invalidates_cache(tmp_path: Path):
    pc = PlanCache(tmp_path / "pc.jsonl", min_successes_for_cache=2)
    actions = [_docx_action()]
    pc.record_success(category="office_doc_generation", actions_dump=actions)
    pc.record_success(category="office_doc_generation", actions_dump=actions)
    assert pc.lookup(category="office_doc_generation") is not None
    pc.record_failure(category="office_doc_generation", actions_dump=actions)
    assert pc.lookup(category="office_doc_generation") is None  # invalidated


def test_materialize_fills_template_for_docx(tmp_path: Path):
    pc = PlanCache(tmp_path / "pc.jsonl", min_successes_for_cache=1,
                   default_outputs_dir="outputs")
    actions = [_docx_action()]
    pc.record_success(category="office_doc_generation", actions_dump=actions)
    entry = pc.lookup(category="office_doc_generation")
    assert entry is not None
    out = pc.materialize(entry, goal_text="Write a doc about transformers",
                         task_id="t1")
    assert len(out) == 1
    a = out[0]
    assert a["tool"] == "docx"
    assert a["operation"] == "save_under_outputs"
    assert a["target"].endswith(".docx")
    assert "outputs" in a["target"]
    assert a["metadata"]["title"]  # synthesized from goal
    assert a["metadata"]["body"]


def test_different_shapes_dont_collide(tmp_path: Path):
    pc = PlanCache(tmp_path / "pc.jsonl", min_successes_for_cache=1)
    a_docx = [{"tool": "docx", "operation": "save_under_outputs"}]
    a_pptx = [{"tool": "pptx", "operation": "save_under_outputs"}]
    pc.record_success(category="office", actions_dump=a_docx)
    pc.record_success(category="office", actions_dump=a_pptx)
    snap = pc.snapshot()
    assert len(snap) == 2
