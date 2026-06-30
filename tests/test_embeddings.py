"""Embeddings module unit tests — Phase 2 (L4.2).

The OpenAI HTTP path is covered by `tests/test_openai_provider.py`;
here we test the provider-dispatch + cosine math in isolation.
"""
from __future__ import annotations

import math

import pytest

from teow_agl.util import embeddings as emb


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def test_provider_default_is_openai(monkeypatch):
    # Production default (no demo/smart-mock signal): openai.
    monkeypatch.delenv("SKILL_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("MAIC_DEMO_MODE", raising=False)
    monkeypatch.delenv("TEOW_AGL_PLANNER", raising=False)
    assert emb._resolve_provider() == "openai"


def test_provider_env_override(monkeypatch):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "NONE")
    assert emb._resolve_provider() == "none"


def test_provider_demo_mode_defaults_to_local(monkeypatch):
    """Brief 1 #4 — in demo / smart-mock mode the skill-embedding lane defaults
    to local BM25 (`none`), so the competition demo stays deterministic and
    never pays an embedding network round-trip just because OPENAI_API_KEY is
    present. An explicit SKILL_EMBEDDING_PROVIDER still wins."""
    monkeypatch.delenv("SKILL_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("TEOW_AGL_PLANNER", raising=False)
    monkeypatch.setenv("MAIC_DEMO_MODE", "1")
    assert emb._resolve_provider() == "none"
    monkeypatch.delenv("MAIC_DEMO_MODE", raising=False)
    monkeypatch.setenv("TEOW_AGL_PLANNER", "smart_mock")
    assert emb._resolve_provider() == "none"
    # explicit operator choice overrides demo mode
    monkeypatch.setenv("MAIC_DEMO_MODE", "1")
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    assert emb._resolve_provider() == "openai"


def test_provider_available_requires_api_key(monkeypatch):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-some-key")
    assert emb.embedding_provider_available() is True

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert emb.embedding_provider_available() is False


def test_provider_none_never_available(monkeypatch):
    """provider=none is the explicit kill-switch (used by tests to
    force BM25 fallback). Even with a key present it must report
    unavailable."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-irrelevant")
    assert emb.embedding_provider_available() is False


def test_unknown_provider_returns_none(monkeypatch):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "voyager-9000")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert emb.embed_texts(["hi"]) is None
    assert emb.embedding_provider_available() is False


# ---------------------------------------------------------------------------
# embed_texts / embed_one
# ---------------------------------------------------------------------------

def test_embed_texts_empty_input_no_provider_call(monkeypatch):
    """Empty input → return [] immediately, never touching the provider."""
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: pytest.fail("should not be called"))
    assert emb.embed_texts([]) == []


def test_embed_texts_delegates_to_provider(monkeypatch):
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda texts: [[0.1, 0.2]] * len(texts))
    out = emb.embed_texts(["a", "b", "c"])
    assert out == [[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]]


def test_embed_texts_coerces_non_strings(monkeypatch):
    seen = []

    def _capture(texts):
        seen.extend(texts)
        return [[0.0]] * len(texts)

    monkeypatch.setattr(emb, "_embed_via_provider", _capture)
    # Pass mixed types — module should str() them all.
    out = emb.embed_texts(("alpha", 42, 3.14))
    assert out is not None
    assert seen == ["alpha", "42", "3.14"]


def test_embed_texts_provider_failure_returns_none(monkeypatch):
    monkeypatch.setattr(emb, "_embed_via_provider", lambda _: None)
    assert emb.embed_texts(["x"]) is None


def test_embed_one_returns_first_vector(monkeypatch):
    monkeypatch.setattr(emb, "_embed_via_provider",
                        lambda _: [[1.0, 2.0, 3.0]])
    assert emb.embed_one("hello") == [1.0, 2.0, 3.0]


def test_embed_one_provider_failure(monkeypatch):
    monkeypatch.setattr(emb, "_embed_via_provider", lambda _: None)
    assert emb.embed_one("hello") is None


# ---------------------------------------------------------------------------
# cosine_similarity — boundary conditions
# ---------------------------------------------------------------------------

def test_cosine_identical_vectors_is_one():
    v = [0.3, 0.4, 0.5, -0.2]
    assert emb.cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-9)


def test_cosine_orthogonal_is_zero():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert emb.cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-9)


def test_cosine_opposite_is_minus_one():
    a = [1.0, 2.0, 3.0]
    b = [-1.0, -2.0, -3.0]
    assert emb.cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-9)


def test_cosine_scale_invariant():
    """cosine(v, 7v) == 1.0 regardless of magnitude."""
    a = [0.1, 0.2, 0.3]
    b = [0.7, 1.4, 2.1]
    assert emb.cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-9)


def test_cosine_dim_mismatch_returns_zero():
    assert emb.cosine_similarity([1, 2, 3], [1, 2]) == 0.0


def test_cosine_zero_vector_returns_zero():
    assert emb.cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0
    assert emb.cosine_similarity([1, 2, 3], [0, 0, 0]) == 0.0


def test_cosine_empty_inputs_return_zero():
    assert emb.cosine_similarity([], [1, 2]) == 0.0
    assert emb.cosine_similarity([1, 2], []) == 0.0
    assert emb.cosine_similarity([], []) == 0.0


def test_cosine_handles_real_world_floats():
    """Sanity check with values in the range produced by OpenAI
    text-embedding-3-small (small magnitudes, mixed signs)."""
    a = [0.012, -0.034, 0.089, -0.022, 0.0001]
    b = [0.011, -0.036, 0.090, -0.020, 0.0002]
    score = emb.cosine_similarity(a, b)
    assert 0.99 < score < 1.0  # very similar but not identical


# ---------------------------------------------------------------------------
# embedding_dim probe + cache
# ---------------------------------------------------------------------------

def test_embedding_dim_when_unavailable(monkeypatch):
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "none")
    emb._reset_dim_cache_for_tests()
    assert emb.embedding_dim() is None


def test_embedding_dim_probes_and_caches(monkeypatch):
    """First call hits provider, second call uses cache."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    emb._reset_dim_cache_for_tests()

    calls = []

    def _fake(texts):
        calls.append(texts)
        return [[0.0] * 1536]

    monkeypatch.setattr(emb, "_embed_via_provider", _fake)
    assert emb.embedding_dim() == 1536
    assert emb.embedding_dim() == 1536  # cached, doesn't probe again
    assert len(calls) == 1


def test_embedding_dim_caches_failure_too(monkeypatch):
    """A failed probe is cached as None — we don't hammer a broken
    provider on every capability check."""
    monkeypatch.setenv("SKILL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    emb._reset_dim_cache_for_tests()

    calls = []

    def _fake(texts):
        calls.append(texts)
        return None

    monkeypatch.setattr(emb, "_embed_via_provider", _fake)
    assert emb.embedding_dim() is None
    assert emb.embedding_dim() is None
    assert len(calls) == 1
