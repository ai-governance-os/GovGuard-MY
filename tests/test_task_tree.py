"""Module 102T Task Tree Decomposer — unit tests.

Covers:
  * needs_decomposition heuristic: simple chit-chat False; long goals True;
    trigger phrases True; multi-imperative-verb True; Chinese phrases True
  * decompose: LLM returns valid tree → TaskTree built; LLM returns refusal
    → None; LLM returns empty/malformed → None; LLM returns leaves with
    invalid deps → invalid deps dropped; LLM returns cycle → None;
    LLM returns > max_leaves → truncated per config; LLM returns
    < min_leaves → None
  * topo_sort: linear order preserved; sibling order preserved; cycle
    detected
  * Refusal short-circuits don't crash on missing fields
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.modules.module_102t_task_tree import TaskTreeModule
from teow_agl.models import SubGoal, TaskEnvelope


# ---------------------------------------------------------------------------
# Stub chat LLM — returns a fixed JSON dict so we can exercise the
# decomposer without touching the network.
# ---------------------------------------------------------------------------
class _StubChatLLM:
    def __init__(self, response: dict | None = None,
                 raise_on_call: Exception | None = None) -> None:
        self.response = response if response is not None else {}
        self.raise_on_call = raise_on_call
        self.calls: list[dict] = []

    def chat_json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        self.calls.append({"system": system, "user": user})
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.response

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return json.dumps(self.response or {})


def _load_real_config() -> dict:
    cfg_path = (Path(__file__).resolve().parents[1]
                / "configs" / "task_decomposition.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _load_real_prompt() -> str:
    p = (Path(__file__).resolve().parents[1]
         / "prompts" / "module_102t_decomposer_system.md")
    return p.read_text(encoding="utf-8")


def _envelope(goal: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_test_tree",
        session_id="sess_test",
        user_id="default_user",
        raw_goal=goal,
        normalized_goal=goal,
    )


def _module(response: dict | None = None,
            *, raise_on_call: Exception | None = None,
            cfg_override: dict | None = None) -> TaskTreeModule:
    cfg = cfg_override if cfg_override is not None else _load_real_config()
    return TaskTreeModule(
        chat_llm=_StubChatLLM(response, raise_on_call=raise_on_call),
        system_prompt=_load_real_prompt(),
        config=cfg,
    )


# ===========================================================================
# needs_decomposition heuristic
# ===========================================================================

def test_simple_chitchat_does_not_decompose():
    m = _module()
    assert not m.needs_decomposition("hi")
    assert not m.needs_decomposition("你是谁?")
    assert not m.needs_decomposition("what's 2+2?")


def test_long_goal_triggers_decomposition():
    """min_chars=120 in default config → a 200-char goal triggers."""
    m = _module()
    long_goal = "Please " + ("research the topic and " * 15)
    assert m.needs_decomposition(long_goal)


def test_trigger_phrase_first_then_finally():
    m = _module()
    assert m.needs_decomposition("First search for X. Then write Y. Finally save Z.")


def test_trigger_phrase_numbered_list():
    m = _module()
    assert m.needs_decomposition("1. search for X 2. write Y 3. save Z")


def test_trigger_phrase_chinese():
    m = _module()
    assert m.needs_decomposition("首先搜索AI政策,然后写摘要,最后保存docx")


def test_multi_imperative_verbs():
    """Three or more imperative verbs (per default config) should fire."""
    m = _module()
    assert m.needs_decomposition("write a report, generate an image, save the file")


def test_two_imperative_verbs_not_enough():
    """Below the multi_imperative_min_verbs threshold."""
    m = _module()
    # "write a report" → 1 verb only
    assert not m.needs_decomposition("write a report")


def test_disabled_config_short_circuits():
    cfg = _load_real_config()
    cfg["enabled"] = False
    m = _module(cfg_override=cfg)
    long_goal = "First do A, then do B, then do C." + ("x" * 200)
    assert not m.needs_decomposition(long_goal)


# ===========================================================================
# decompose() — happy path
# ===========================================================================

def test_decompose_valid_response_builds_tree():
    response = {
        "tree_id": "tree_abc",
        "reasoning": "split into research / write / save",
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "search for 3 AI papers",
             "depends_on": []},
            {"sub_goal_id": "sg_b", "description": "summarize each paper",
             "depends_on": ["sg_a"]},
            {"sub_goal_id": "sg_c", "description": "save the summary to docx",
             "depends_on": ["sg_b"]},
        ],
    }
    m = _module(response)
    tree = m.decompose(_envelope("Find 3 papers, summarize, save as docx"))
    assert tree is not None
    assert tree.tree_id == "tree_abc"
    assert len(tree.leaves) == 3
    assert tree.order == ["sg_a", "sg_b", "sg_c"]
    assert tree.parent_task_id == "task_test_tree"
    assert tree.reasoning == "split into research / write / save"


def test_decompose_auto_generates_ids_when_missing():
    response = {
        "leaves": [
            {"description": "do thing 1", "depends_on": []},
            {"description": "do thing 2", "depends_on": []},
        ],
    }
    m = _module(response)
    tree = m.decompose(_envelope("Do a few things"))
    assert tree is not None
    assert len(tree.leaves) == 2
    for leaf in tree.leaves:
        assert leaf.sub_goal_id.startswith("sg_")


# ===========================================================================
# decompose() — rejection paths
# ===========================================================================

def test_decompose_returns_none_on_refusal():
    m = _module({"refusal": "not_decomposable", "reasoning": "single action"})
    assert m.decompose(_envelope("do one thing")) is None


def test_decompose_returns_none_on_empty_response():
    m = _module({})
    assert m.decompose(_envelope("anything")) is None


def test_decompose_returns_none_when_llm_raises():
    m = _module(raise_on_call=RuntimeError("network down"))
    assert m.decompose(_envelope("anything")) is None


def test_decompose_returns_none_when_below_min_leaves():
    """min_leaves_per_tree=2 in default config → 1-leaf response is rejected."""
    response = {
        "leaves": [{"sub_goal_id": "sg_a",
                    "description": "lonely leaf", "depends_on": []}],
    }
    m = _module(response)
    assert m.decompose(_envelope("anything")) is None


def test_decompose_truncates_overflow():
    """max_leaves_per_tree=8 with fallback.on_overflow=truncate → keep first 8."""
    response = {
        "leaves": [
            {"sub_goal_id": f"sg_{i}", "description": f"leaf {i}",
             "depends_on": []}
            for i in range(15)
        ],
    }
    m = _module(response)
    tree = m.decompose(_envelope("many things"))
    assert tree is not None
    assert len(tree.leaves) == 8


def test_decompose_drops_invalid_deps():
    """Dependencies pointing at unknown sub_goal_ids should be dropped
    silently — the LLM commonly hallucinates dep ids."""
    response = {
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "first",
             "depends_on": ["sg_DOES_NOT_EXIST"]},
            {"sub_goal_id": "sg_b", "description": "second",
             "depends_on": ["sg_a", "another_fake"]},
        ],
    }
    m = _module(response)
    tree = m.decompose(_envelope("things"))
    assert tree is not None
    # sg_a's bad dep dropped → it becomes a root
    sg_a = next(l for l in tree.leaves if l.sub_goal_id == "sg_a")
    assert sg_a.depends_on == []
    # sg_b keeps the valid dep, drops the fake one
    sg_b = next(l for l in tree.leaves if l.sub_goal_id == "sg_b")
    assert sg_b.depends_on == ["sg_a"]


def test_decompose_returns_none_on_cycle():
    response = {
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "a", "depends_on": ["sg_b"]},
            {"sub_goal_id": "sg_b", "description": "b", "depends_on": ["sg_a"]},
        ],
    }
    m = _module(response)
    assert m.decompose(_envelope("cyclic mess")) is None


def test_decompose_drops_empty_descriptions():
    response = {
        "leaves": [
            {"sub_goal_id": "sg_a", "description": "real one", "depends_on": []},
            {"sub_goal_id": "sg_b", "description": "", "depends_on": []},
            {"sub_goal_id": "sg_c", "description": "another real", "depends_on": []},
        ],
    }
    m = _module(response)
    tree = m.decompose(_envelope("things"))
    assert tree is not None
    # 3 → 2 leaves (sg_b dropped). Still ≥ min_leaves (2) so tree is built.
    assert len(tree.leaves) == 2


def test_decompose_resolves_id_collisions():
    """Duplicate sub_goal_ids get suffixed so neither leaf is lost."""
    response = {
        "leaves": [
            {"sub_goal_id": "sg_dup", "description": "first dup", "depends_on": []},
            {"sub_goal_id": "sg_dup", "description": "second dup", "depends_on": []},
        ],
    }
    m = _module(response)
    tree = m.decompose(_envelope("things"))
    assert tree is not None
    ids = [l.sub_goal_id for l in tree.leaves]
    assert len(set(ids)) == 2  # both kept, with distinct ids


# ===========================================================================
# topo_sort
# ===========================================================================

def test_topo_sort_linear():
    leaves = [
        SubGoal(sub_goal_id="c", description="c", depends_on=["b"]),
        SubGoal(sub_goal_id="a", description="a", depends_on=[]),
        SubGoal(sub_goal_id="b", description="b", depends_on=["a"]),
    ]
    assert TaskTreeModule.topo_sort(leaves) == ["a", "b", "c"]


def test_topo_sort_fan_out():
    leaves = [
        SubGoal(sub_goal_id="root", description="r", depends_on=[]),
        SubGoal(sub_goal_id="b", description="b", depends_on=["root"]),
        SubGoal(sub_goal_id="c", description="c", depends_on=["root"]),
        SubGoal(sub_goal_id="d", description="d", depends_on=["root"]),
    ]
    order = TaskTreeModule.topo_sort(leaves)
    assert order[0] == "root"
    assert set(order[1:]) == {"b", "c", "d"}


def test_topo_sort_preserves_sibling_order():
    leaves = [
        SubGoal(sub_goal_id="first", description="x", depends_on=[]),
        SubGoal(sub_goal_id="second", description="x", depends_on=[]),
        SubGoal(sub_goal_id="third", description="x", depends_on=[]),
    ]
    # All three are roots — should come out in declared order
    assert TaskTreeModule.topo_sort(leaves) == ["first", "second", "third"]


def test_topo_sort_cycle_raises():
    leaves = [
        SubGoal(sub_goal_id="a", description="a", depends_on=["b"]),
        SubGoal(sub_goal_id="b", description="b", depends_on=["a"]),
    ]
    with pytest.raises(ValueError) as exc:
        TaskTreeModule.topo_sort(leaves)
    assert "cycle" in str(exc.value).lower()


def test_topo_sort_self_cycle_raises():
    leaves = [SubGoal(sub_goal_id="a", description="a", depends_on=["a"])]
    with pytest.raises(ValueError):
        TaskTreeModule.topo_sort(leaves)
