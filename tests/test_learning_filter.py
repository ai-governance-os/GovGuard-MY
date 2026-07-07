"""Brief 3 #E — task-local tone/style learning boundary (shared foundation).

A one-off styling instruction ("keep this notice warm", "make it concise")
governs the current output only. The reflector must NOT distil it into a
durable USER.md preference — that over-records a task-local constraint as a
persistent personal fact. A communication-style preference becomes persistent
memory ONLY when the user explicitly asks to remember it.

These are pure-function tests on the deterministic filter (no LLM / no key).
"""
from __future__ import annotations

from teow_agl.modules.module_109_reflector import ReflectorModule


def _u(text: str) -> dict:
    return {"action": "add", "text": text}


# --- _is_task_local_style: style preference vs durable fact ----------------

def test_style_preference_lines_are_detected():
    for line in [
        "User prefers communication to be warm and clear",
        "User prefers concise messages",
        "User likes a more formal tone in letters",
        "User wants shorter replies",
        "Prefers a respectful, polite writing style",
    ]:
        assert ReflectorModule._is_task_local_style(line) is True, line


def test_durable_non_style_facts_are_not_flagged():
    for line in [
        "User is a primary school teacher in Johor",
        "User communicates in Bahasa Melayu",          # language pref, not style
        "User manages the school's national athletics reporting",
        "User prefers metric units",                   # a real durable pref, no style word
        "MEMORY: the demo server runs on port 8765",
    ]:
        assert ReflectorModule._is_task_local_style(line) is False, line


# --- _drop_task_local_style: gated on an explicit persist cue --------------

def test_task_local_style_dropped_without_persist_cue():
    updates = [_u("User prefers communication to be warm and clear")]
    kept, dropped = ReflectorModule._drop_task_local_style(
        updates, intent="Keep this parent notice warm and clear, please.")
    assert kept == []
    assert dropped == updates


def test_explicit_remember_keeps_the_preference():
    updates = [_u("User prefers a warm, concise tone")]
    kept, dropped = ReflectorModule._drop_task_local_style(
        updates, intent="Remember that I prefer a warm, concise tone from now on.")
    assert kept == updates
    assert dropped == []


def test_cjk_persist_cue_keeps_the_preference():
    updates = [_u("User prefers a formal tone")]
    kept, dropped = ReflectorModule._drop_task_local_style(
        updates, intent="以后给董事的信都用正式的语气")
    assert kept == updates and dropped == []


def test_durable_facts_survive_even_when_a_style_line_is_dropped():
    updates = [
        _u("User is a teacher at Johor SJK(C) Primary School"),
        _u("User prefers warmer tone"),               # task-local → dropped
    ]
    kept, dropped = ReflectorModule._drop_task_local_style(
        updates, intent="Draft this parent notice, warmer please.")
    assert kept == [updates[0]]
    assert dropped == [updates[1]]


def test_unscoped_style_observation_is_kept():
    # No "this/for-this" scope cue and no explicit remember → a possibly-durable
    # preference; the filter must NOT eat it (left to normal governance hooks).
    updates = [_u("User prefers concise answers in Chinese")]
    kept, dropped = ReflectorModule._drop_task_local_style(
        updates, intent="Help me organize my research notes by topic")
    assert kept == updates and dropped == []
