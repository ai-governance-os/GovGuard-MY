"""Generic chat-LLM adapter — plain-text in, plain-text out.

This is intentionally simpler than the PlannerAdapter (which returns JSON).
It exists so non-planning components can talk to an LLM:

  * ChatTool        — answer free-form questions
  * ContentSynthesizer (Module 102B) — expand docx/pptx/xlsx bodies

Backends supported (selected by env var TEOW_AGL_CHAT_LLM, defaulting to
whatever TEOW_AGL_PLANNER is set to):

  - groq     : llama-3.3-70b-versatile via Groq API
  - gemini   : gemini-2.0-flash via Google API
  - ollama   : local Ollama HTTP server
  - openai   : gpt-4o-mini via OpenAI API (Phase 2 — abstraction pass)
  - mock     : echo (returns "" — caller decides what to do)

If credentials are missing or the chosen backend is unavailable the
adapter returns "" rather than raising — callers MUST handle empty
output gracefully so the rest of the governance pipeline keeps going.
"""
from __future__ import annotations

import json
import os
import re


def _parse_retry_after(value: str | None, default: float) -> float:
    """Parse a Retry-After header. Groq returns either an integer number
    of seconds (e.g. "30") or an HTTP-date. We only care about the
    seconds form; on anything weird or absent fall back to `default`.
    Clamped to [1, 60] so we never sleep silly long values."""
    if not value:
        return default
    try:
        n = float(str(value).strip())
    except ValueError:
        return default
    return max(1.0, min(60.0, n))


class ChatLLM:
    """Simple system+user -> text wrapper, swappable backend."""

    def __init__(self, backend: str | None = None, timeout: int = 60) -> None:
        self.backend = (backend or self._default_backend()).lower()
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def chat(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        """Return the model's plain-text reply, or '' on any failure.

        Phase C: every backend call is metered by the cost guard.
        Over budget → '' — the same degradation path callers already
        handle for missing keys / network failures."""
        if self.backend in ("groq", "gemini", "ollama", "openai", "anthropic"):
            guard = self._cost_guard()
            if guard is not None and not guard.allow("chat_calls"):
                return ""
        out = ""
        if self.backend == "groq":
            out = self._groq(system, user, max_tokens)
        elif self.backend == "gemini":
            out = self._gemini(system, user, max_tokens)
        elif self.backend == "ollama":
            out = self._ollama(system, user, max_tokens)
        elif self.backend == "openai":
            out = self._openai(system, user, max_tokens)
        elif self.backend == "anthropic":
            out = self._anthropic(system, user, max_tokens)
        # mock / unknown -> empty, caller decides fallback
        if out:
            guard = self._cost_guard()
            if guard is not None:
                guard.record("chat_calls")
        return out

    @staticmethod
    def _cost_guard():
        """Late import — policies must stay importable without adapters."""
        try:
            from teow_agl.policies.cost_guard import default_guard
            return default_guard()
        except Exception:
            return None

    def chat_json(self, system: str, user: str, *, max_tokens: int = 1500) -> dict:
        """Same as chat() but tries to parse a JSON object from the reply.
        Returns {} when nothing parseable comes back."""
        # OpenAI supports native JSON mode.  Use it for structured school
        # artifact bundles and judges instead of hoping plain-text JSON is
        # syntactically valid (the original school-semantics failure produced
        # correct-looking but unquoted enum values).
        if self.backend == "openai":
            guard = self._cost_guard()
            if guard is not None and not guard.allow("chat_calls"):
                return {}
            try:
                from teow_agl.adapters.openai_provider import openai_chat_json
                out = openai_chat_json(
                    system, user, max_tokens=max_tokens, timeout=self.timeout)
            except Exception:
                out = {}
            if out and guard is not None:
                guard.record("chat_calls")
            return out if isinstance(out, dict) else {}
        text = self.chat(system, user, max_tokens=max_tokens)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------
    @staticmethod
    def _default_backend() -> str:
        # Mirror the planner's backend selection so a single key setup works.
        return (os.environ.get("TEOW_AGL_CHAT_LLM")
                or os.environ.get("TEOW_AGL_PLANNER")
                or "mock").lower()

    def _groq(self, system: str, user: str, max_tokens: int) -> str:
        """Chat call against Groq with two layers of robustness:

        1. **429 retry-with-backoff** (Phase-A patch P1): rate-limit
           hits during bursty demo runs now wait briefly and retry once
           instead of returning empty (which would trip the synthesizer's
           "kept_planner_content" branch and write a near-empty file).
        2. **Empty-content retry with bumped max_tokens** (P0.2): Qwen3
           with `reasoning_format=hidden` sometimes uses the whole token
           budget on reasoning, leaving no room for the actual answer.
           On empty-after-strip we retry once with doubled max_tokens.
        """
        raw = self._groq_once(system, user, max_tokens, retry_429=True)
        if raw.strip():
            return raw
        # Empty after strip → likely Qwen3 ran out of headroom for the
        # final answer (reasoning ate the budget). Retry with much more
        # room. Cap at 16k tokens so we don't blow past Groq context.
        bumped = min(max(max_tokens * 2, 6000), 16000)
        if bumped > max_tokens:
            raw = self._groq_once(system, user, bumped, retry_429=False)
        return raw

    def _groq_once(self, system: str, user: str, max_tokens: int,
                   retry_429: bool) -> str:
        """Single Groq call. When `retry_429` is True a 429 response
        triggers one short sleep + retry; otherwise 429 → empty."""
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return ""
        try:
            import httpx  # type: ignore
        except ImportError:
            return ""
        # Resolve short aliases ("qwen", "kimi", ...) the same way the
        # planner does, so users only have to set GROQ_MODEL in one place.
        # Also reuse the reasoning-suppression helpers so <think> blocks
        # from Qwen3 / DeepSeek-R1 don't leak into chat output (which would
        # then become a chat.answer body and trip executor quality checks).
        from teow_agl.adapters.groq_provider import (
            _resolve_model,
            _add_reasoning_suppression,
            strip_think_blocks,
        )
        model = _resolve_model(os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        _add_reasoning_suppression(payload, model)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        def _post():
            return httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers, json=payload, timeout=self.timeout,
            )

        try:
            r = _post()
            if r.status_code == 429 and retry_429:
                # Honour Retry-After if present; otherwise a short fixed
                # wait. Free-tier RPM window is ~60s but most bursts
                # clear in 10-30s.
                import time as _time
                wait = _parse_retry_after(r.headers.get("Retry-After"), 25)
                _time.sleep(wait)
                r = _post()
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
        except Exception:
            return ""
        # Defensive: strip any residual <think> block the API may have
        # leaked even with reasoning_format=hidden set.
        return strip_think_blocks(raw or "").strip()

    def _gemini(self, system: str, user: str, max_tokens: int) -> str:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return ""
        try:
            import httpx  # type: ignore
        except ImportError:
            return ""
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
        }
        try:
            r = httpx.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip()
        except Exception:
            return ""

    def _ollama(self, system: str, user: str, max_tokens: int) -> str:
        try:
            import httpx  # type: ignore
        except ImportError:
            return ""
        endpoint = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": max_tokens},
        }
        try:
            r = httpx.post(f"{endpoint}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except Exception:
            return ""

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        """Chat call against OpenAI's chat-completions endpoint.

        Delegates to `adapters.openai_provider.openai_chat` so the
        retry / JSON-mode / model-resolution logic lives in one place
        and stays consistent with the embeddings + abstraction paths.
        """
        from teow_agl.adapters.openai_provider import openai_chat
        return openai_chat(
            system, user, max_tokens=max_tokens, timeout=self.timeout,
        )

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        """Chat call against Anthropic's /v1/messages endpoint.

        Delegates to `adapters.anthropic_provider.anthropic_chat` so
        the retry / model-alias logic lives next to the planner.
        """
        from teow_agl.adapters.anthropic_provider import anthropic_chat
        return anthropic_chat(
            system, user, max_tokens=max_tokens, timeout=self.timeout,
        )

    def chat_json_openai(
        self, system: str, user: str, *, max_tokens: int = 1500,
    ) -> dict:
        """Public OpenAI JSON-mode shortcut for callers (Distiller
        abstraction pass) that want the strict json_object format
        regardless of `self.backend`. Bypasses the regex fallback in
        the generic `chat_json()` — we trust OpenAI's native flag."""
        from teow_agl.adapters.openai_provider import openai_chat_json
        return openai_chat_json(
            system, user, max_tokens=max_tokens, timeout=self.timeout,
        )
