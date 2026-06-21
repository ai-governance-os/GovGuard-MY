"""Phase A1 — lock in the PlanningBrief slimming that fixes Groq 413.

Background: Qwen3-32B on Groq's free tier rejects oversized requests with
HTTP 413. The full tool catalog (~6.7 KB) plus verbose constraint prose
blocks pushed every planner request near ~10 K tokens — over the free-tier
per-minute budget. These helpers cut the brief to essentials. If a future
change re-bloats it, these tests fail loudly.
"""
from __future__ import annotations

import json

from teow_agl.runtime import _compact_tool_catalog, _trim_web_hits


# ---------------------------------------------------------------------------
# _compact_tool_catalog
# ---------------------------------------------------------------------------

_SAMPLE_FULL = {
    "fs": {
        "description": "Filesystem inside workspace_roots. " * 5,
        "operations": ["save_under_outputs", "read_safe"],
        "metadata_hints": {
            "save_under_outputs": "metadata.content (str)",
            "read_safe": "no metadata",
        },
    },
    "chat": {
        "description": "Respond to the user. " * 10,
        "operations": ["answer"],
        "metadata_hints": {"answer": "metadata.body (str, required)"},
    },
    "_human_DEPRECATED": {
        "description": "DEPRECATED",
        "operations": [],
    },
}


def test_compact_keeps_operations():
    c = _compact_tool_catalog(_SAMPLE_FULL)
    assert c["fs"]["operations"] == ["save_under_outputs", "read_safe"]
    assert c["chat"]["operations"] == ["answer"]


def test_compact_drops_underscore_tools():
    """_human_DEPRECATED must never reach the planner."""
    c = _compact_tool_catalog(_SAMPLE_FULL)
    assert "_human_DEPRECATED" not in c


def test_compact_drops_verbose_description():
    """The long `description` field is not carried — the system prompt
    explains tool choice instead."""
    c = _compact_tool_catalog(_SAMPLE_FULL)
    for entry in c.values():
        assert "description" not in entry


def test_compact_keeps_a_metadata_hint():
    c = _compact_tool_catalog(_SAMPLE_FULL)
    assert "metadata" in c["fs"]
    assert "metadata.content" in c["fs"]["metadata"]


def test_compact_caps_metadata_hint_length():
    """A pathologically long hint is truncated so it can't re-bloat."""
    huge = {
        "x": {
            "operations": ["go"],
            "metadata_hints": {"go": "y" * 5000},
        }
    }
    c = _compact_tool_catalog(huge)
    assert len(c["x"]["metadata"]) <= 180


def test_compact_is_substantially_smaller_than_full():
    """The real catalog must shrink a lot — this is the whole point."""
    full = json.load(open(
        "configs/tool_catalog.json", encoding="utf-8"
    )).get("tools", {})
    full_size = len(json.dumps(full, ensure_ascii=False))
    compact_size = len(json.dumps(
        _compact_tool_catalog(full), ensure_ascii=False))
    # Expect at least a 50% reduction.
    assert compact_size < full_size * 0.5, (
        f"compact {compact_size} not < 50% of full {full_size}")


def test_compact_handles_empty_and_garbage():
    assert _compact_tool_catalog({}) == {}
    assert _compact_tool_catalog({"bad": "not-a-dict"}) == {}


# ---------------------------------------------------------------------------
# _trim_web_hits
# ---------------------------------------------------------------------------

def _hits(n: int, content_len: int = 2000) -> list[dict]:
    return [
        {"title": f"Title {i}", "url": f"https://x/{i}",
         "content": "z" * content_len, "source": "tavily"}
        for i in range(n)
    ]


def test_trim_caps_hit_count():
    out = _trim_web_hits(_hits(10))
    assert len(out) == 3  # default max_hits


def test_trim_caps_content_length():
    out = _trim_web_hits(_hits(3, content_len=5000))
    for h in out:
        # 400 chars + the ellipsis
        assert len(h["content"]) <= 401


def test_trim_preserves_url_verbatim():
    """URLs must NOT be truncated — citations depend on them."""
    out = _trim_web_hits(_hits(2))
    assert out[0]["url"] == "https://x/0"
    assert out[1]["url"] == "https://x/1"


def test_trim_short_content_untouched():
    hits = [{"title": "T", "url": "u", "content": "short", "source": "x"}]
    out = _trim_web_hits(hits)
    assert out[0]["content"] == "short"


def test_trim_handles_empty():
    assert _trim_web_hits([]) == []
    assert _trim_web_hits(None) == []


def test_trim_skips_non_dict_entries():
    out = _trim_web_hits(["garbage", {"title": "ok", "url": "u",
                                       "content": "c", "source": "s"}])
    assert len(out) == 1
    assert out[0]["title"] == "ok"
