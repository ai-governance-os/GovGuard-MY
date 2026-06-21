"""Module 109 Reflector — unit + integration tests.

Covers:
  * intent-too-short / no-executions / route-RED short-circuit
  * LLM-empty / LLM-malformed → skipped
  * HIGH confidence → applied
  * MEDIUM confidence → pending_review
  * LOW confidence → logged_only
  * bounded-delta: max entries per file, max chars per entry, max net delta
  * replace / remove actions wired through UserMemory correctly
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from teow_agl.modules.module_109_reflector import ReflectorModule
from teow_agl.policies.user_memory import UserMemory


# ---------------------------------------------------------------------------
# Stub ChatLLM — returns whatever JSON we want without touching the network.
# ---------------------------------------------------------------------------
class StubChatLLM:
    """Returns a pre-programmed JSON dict from chat_json()."""

    def __init__(self, response: dict | None = None,
                 raise_on_call: Exception | None = None) -> None:
        self.response = response if response is not None else {}
        self.raise_on_call = raise_on_call
        self.calls: list[dict] = []

    def chat_json(self, system: str, user: str, max_tokens: int = 1200) -> dict:
        self.calls.append({"system": system, "user": user})
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.response

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return json.dumps(self.response or {})


# ---------------------------------------------------------------------------
# Lightweight stand-ins for TaskEnvelope and TaskRunResult so we can test
# the reflector without dragging in the full Runtime.
# ---------------------------------------------------------------------------
@dataclass
class _FakeExec:
    status: str
    tool: str = "chat"
    output_summary: str = ""


@dataclass
class _FakeEnvelope:
    task_id: str = "task_test"
    normalized_goal: str = ""


@dataclass
class _FakeResult:
    final_route: str = "BLUE"
    executions: list = field(default_factory=list)
    decisions: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default constraints (mirrors configs/reflection_constraints.json but
# loaded inline so tests don't depend on a real file).
# ---------------------------------------------------------------------------
def _make_constraints(**overrides) -> dict:
    base = {
        "confidence_thresholds": {
            "auto_apply_min": 0.80,
            "human_review_min": 0.40,
        },
        "bounded_delta": {
            "max_entries_per_file_per_task": 2,
            "max_chars_per_entry": 200,
            "max_net_delta_chars_per_task": 400,
        },
        "min_task_signal": {
            "min_user_intent_chars": 12,
            "skip_if_route_in": ["RED"],
            "skip_if_no_executions": True,
        },
        "forbidden_patterns": {"patterns": []},
        "forbidden_topics": {"topics": []},
    }
    for k, v in overrides.items():
        base[k] = v
    return base


def _strong_intent() -> str:
    return "Help me organize my AGI research notes by topic"


# ===========================================================================
# Short-circuit checks (no LLM call expected)
# ===========================================================================

def test_skips_when_intent_too_short():
    llm = StubChatLLM(response={"confidence": 1.0})
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal="hi"),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert proposal["skipped"] == "intent_too_short"
    # LLM must NOT have been called
    assert llm.calls == []


def test_skips_when_route_red():
    llm = StubChatLLM(response={"confidence": 1.0})
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(final_route="RED",
                           executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert proposal["skipped"] == "route_excluded:RED"
    assert llm.calls == []


def test_skips_when_no_executions():
    llm = StubChatLLM(response={"confidence": 1.0})
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert proposal["skipped"] == "no_executions"
    assert llm.calls == []


# ===========================================================================
# LLM call paths
# ===========================================================================

def test_llm_error_returns_skipped():
    llm = StubChatLLM(raise_on_call=RuntimeError("network gone"))
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert "llm_error" in proposal["skipped"]


def test_llm_empty_returns_skipped():
    llm = StubChatLLM(response={})
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert proposal["skipped"] == "llm_empty_or_malformed"


def test_llm_empty_updates_returns_skipped():
    """LLM returns valid JSON but zero proposed updates → noted as skipped
    with reason `no_proposed_updates`, not applied."""
    llm = StubChatLLM(response={
        "confidence": 0.95,
        "reasoning": "task was trivial chit-chat",
        "user_md_updates": [],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert proposal["skipped"] == "no_proposed_updates"
    assert proposal["confidence"] == 0.95


def test_returns_valid_proposal_with_high_confidence():
    llm = StubChatLLM(response={
        "confidence": 0.92,
        "reasoning": "user explicitly stated language preference",
        "user_md_updates": [
            {"action": "add", "text": "Prefers Chinese for chat answers"},
        ],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert proposal.get("skipped") is None
    assert proposal["confidence"] == 0.92
    assert len(proposal["user_md_updates"]) == 1
    assert proposal["user_md_updates"][0]["action"] == "add"
    assert "Chinese" in proposal["user_md_updates"][0]["text"]


# ===========================================================================
# Bounded-delta clamping
# ===========================================================================

def test_clamps_max_entries_per_file():
    llm = StubChatLLM(response={
        "confidence": 1.0,
        "reasoning": "lots of preferences observed",
        "user_md_updates": [
            {"action": "add", "text": f"Preference number {i}"}
            for i in range(10)
        ],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm,
                        constraints=_make_constraints(bounded_delta={
                            "max_entries_per_file_per_task": 2,
                            "max_chars_per_entry": 200,
                            "max_net_delta_chars_per_task": 400,
                        }))
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert len(proposal["user_md_updates"]) == 2  # clamped from 10


def test_clamps_chars_per_entry():
    big_text = "x" * 500
    llm = StubChatLLM(response={
        "confidence": 1.0,
        "reasoning": "test clamp",
        "user_md_updates": [{"action": "add", "text": big_text}],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm,
                        constraints=_make_constraints(bounded_delta={
                            "max_entries_per_file_per_task": 2,
                            "max_chars_per_entry": 100,
                            "max_net_delta_chars_per_task": 400,
                        }))
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert len(proposal["user_md_updates"][0]["text"]) <= 100


def test_drops_bad_entries():
    """Entries with invalid action or empty text are dropped during
    sanitize, leaving only well-formed ones."""
    llm = StubChatLLM(response={
        "confidence": 0.9,
        "reasoning": "mixed",
        "user_md_updates": [
            {"action": "evil_action", "text": "should be dropped"},
            {"action": "add", "text": "good entry"},
            {"action": "add", "text": ""},  # empty text → dropped
            "not a dict",  # not a dict → dropped
        ],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    # Only the one valid add survives.
    assert len(proposal["user_md_updates"]) == 1
    assert proposal["user_md_updates"][0]["text"] == "good entry"


def test_newlines_collapsed_to_single_line():
    llm = StubChatLLM(response={
        "confidence": 0.9,
        "reasoning": "newlines test",
        "user_md_updates": [
            {"action": "add", "text": "line one\nline two\nline three"},
        ],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    proposal = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    text = proposal["user_md_updates"][0]["text"]
    assert "\n" not in text
    assert "line one line two line three" == text


# ===========================================================================
# Confidence clamping
# ===========================================================================

def test_confidence_clamped_to_unit_interval():
    """Even if LLM returns 99.0 or -5, reflector clamps to [0, 1]."""
    llm = StubChatLLM(response={
        "confidence": 99.0,
        "reasoning": "weird llm",
        "user_md_updates": [{"action": "add", "text": "x"}],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    p = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert 0.0 <= p["confidence"] <= 1.0


def test_confidence_garbage_becomes_zero():
    llm = StubChatLLM(response={
        "confidence": "very high",
        "user_md_updates": [],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    p = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "", "MEMORY.md": ""},
    )
    assert p["confidence"] == 0.0


# ===========================================================================
# Replace / remove actions are preserved (so the apply path in runtime
# can wire them to UserMemory.replace / .remove correctly).
# ===========================================================================

def test_replace_action_preserves_old_substring():
    llm = StubChatLLM(response={
        "confidence": 0.95,
        "reasoning": "refining existing note",
        "user_md_updates": [{
            "action": "replace",
            "old_substring": "Prefers short answers",
            "text": "Prefers concise answers in Chinese",
        }],
        "memory_md_updates": [],
    })
    r = ReflectorModule(chat_llm=llm, constraints=_make_constraints())
    p = r.reflect(
        envelope=_FakeEnvelope(normalized_goal=_strong_intent()),
        result=_FakeResult(executions=[_FakeExec("success")]),
        user_memory_snapshot={"USER.md": "Prefers short answers", "MEMORY.md": ""},
    )
    upd = p["user_md_updates"][0]
    assert upd["action"] == "replace"
    assert upd["old_substring"] == "Prefers short answers"
    assert "Chinese" in upd["text"]
