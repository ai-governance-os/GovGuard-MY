"""Embeddings — Phase 2 (L4.2).

Provider-pluggable wrapper around vector embeddings + cosine similarity.

Phase 2 ships one provider (`openai`); the dispatch table is deliberately
kept simple — extra providers (sentence-transformers, voyage, etc.) plug
in by adding a branch in `_embed_via_provider` and exposing it through
`SKILL_EMBEDDING_PROVIDER`.

Public API:
    embed_texts(texts)  -> list[list[float]] | None
    embed_one(text)     -> list[float] | None
    cosine_similarity(a, b)  -> float
    embedding_provider_available() -> bool
    embedding_dim() -> int | None    (probes provider; cached after first call)

Failure contract — matches Phase 1A's pattern: every public function
returns `None` (or `0.0` for similarity) on any failure instead of
raising. SkillManager.find_relevant treats `None` as "embedding lane
unavailable — fall back to BM25". This is the single failure-isolation
channel for the entire Phase 2 retrieval pipeline.

Why pure-Python cosine (no numpy):
  * 1536-dim cosine in pure Python is ~5000 float ops; over 500 skills
    that's <50ms total, well under the budget for a single retrieval.
  * Adding numpy just for one math function bloats the install footprint
    (~15 MB) for negligible win.
  * If we ever index >10K skills, revisit and add numpy.
"""
from __future__ import annotations

import math
import os
from typing import Sequence


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------
def _resolve_provider() -> str:
    """Resolve the skill-embedding provider.

    Precedence:
      1. `SKILL_EMBEDDING_PROVIDER` (explicit operator choice) — wins always,
         so a demo operator who WANTS cosine retrieval sets it to `openai`.
      2. Demo / smart-mock mode (`MAIC_DEMO_MODE` truthy, or
         `TEOW_AGL_PLANNER=smart_mock`) → `none` (local BM25). Keeps the
         competition demo deterministic and fast: it never silently pays an
         embedding network round-trip just because an `OPENAI_API_KEY`
         happens to be present in the shell (Brief 1 #4 — the key-present
         BLUE-latency fix).
      3. Otherwise → `openai` (unchanged production default).
    """
    explicit = os.environ.get("SKILL_EMBEDDING_PROVIDER")
    if explicit is not None:
        return explicit.strip().lower()
    if (os.environ.get("TEOW_AGL_PLANNER", "").strip().lower() == "smart_mock"
            or os.environ.get("MAIC_DEMO_MODE", "").strip().lower()
            in {"1", "true", "yes", "on"}):
        return "none"
    return "openai"


def embedding_provider_available() -> bool:
    """Quick capability check WITHOUT making a network call. Used by
    callers that want to decide whether to even bother going through
    the embedding lane.

    Returns True only when:
      - provider is recognised, AND
      - the provider's required env is configured (e.g. OPENAI_API_KEY)
    """
    p = _resolve_provider()
    if p == "openai":
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if p == "none":
        return False
    return False


def _embed_via_provider(texts: list[str]) -> list[list[float]] | None:
    """Internal dispatcher. Adds a new provider by appending a branch."""
    p = _resolve_provider()
    if p == "openai":
        from teow_agl.adapters.openai_provider import openai_embed
        return openai_embed(texts)
    if p == "none":
        # Explicit "no embeddings" — used by tests to force BM25 path.
        return None
    # Unknown provider name — fail closed.
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def embed_texts(texts: Sequence[str]) -> list[list[float]] | None:
    """Embed a batch of strings.

    Returns:
      - list of vectors on success (same order as input)
      - None on any failure (missing key, network error, unknown provider)
      - [] if `texts` is empty (no API call)
    """
    if not texts:
        return []
    # Defensively coerce to plain list[str]; callers sometimes pass tuples
    # or numpy-style iterables.
    items = [str(t) for t in texts]
    return _embed_via_provider(items)


def embed_one(text: str) -> list[float] | None:
    """Embed a single string. Convenience around `embed_texts`."""
    out = embed_texts([text])
    if out is None:
        return None
    return out[0] if out else None


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Plain cosine similarity in [-1.0, 1.0].

    Defensive returns:
      - 0.0 on dim mismatch
      - 0.0 if either vector has zero norm (all-zeros)
      - 0.0 if either input is empty

    The 0.0-on-error convention means a broken vector ranks NEUTRAL in
    a retrieval pass — not "most similar" (which 1.0 would imply) nor
    "least similar". Callers can apply a min_score threshold without
    needing to special-case the error path.
    """
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Probe — cached after first call
# ---------------------------------------------------------------------------
_DIM_CACHE: int | None = None
_DIM_PROBED: bool = False


def embedding_dim() -> int | None:
    """Return the vector dimension the active provider produces, or None
    if the provider can't be reached.

    Probes the provider ONCE with a tiny string and caches the result.
    Useful in tests + capability checks; production code should just
    handle `len(vec)` from a real embed call.
    """
    global _DIM_CACHE, _DIM_PROBED
    if _DIM_PROBED:
        return _DIM_CACHE
    _DIM_PROBED = True
    if not embedding_provider_available():
        _DIM_CACHE = None
        return None
    vec = embed_one("probe")
    if not vec:
        _DIM_CACHE = None
        return None
    _DIM_CACHE = len(vec)
    return _DIM_CACHE


def _reset_dim_cache_for_tests() -> None:
    """Test-only helper: clear the probe cache. Production code MUST NOT
    call this — the cache is correct under normal use (provider doesn't
    change vector dimension mid-process)."""
    global _DIM_CACHE, _DIM_PROBED
    _DIM_CACHE = None
    _DIM_PROBED = False
