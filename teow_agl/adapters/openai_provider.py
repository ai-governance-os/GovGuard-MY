"""OpenAI provider — Phase 2 (skill abstraction + embeddings).

This adapter is dedicated to **Phase 2 use cases**:
  * `SKILL_ABSTRACTION_LLM=openai` — Module 109B Distiller's second pass
    that lifts a raw procedure into `## Principle` + `## Parameters`.
    GPT-4o-mini quality is materially better than Qwen3 here without
    blowing the budget (abstraction fires <1× per task average).
  * `SKILL_EMBEDDING_PROVIDER=openai` — `util/embeddings.py` calls into
    `openai_embed()` to vectorise skill descriptions / principles for
    cosine-similarity retrieval.

We deliberately keep this **separate** from `groq_provider` / `chat_llm`'s
backend dispatch. Rationale: the Groq stack is the agent's *main* chat
LLM, controlled by `TEOW_AGL_CHAT_LLM` / `TEOW_AGL_PLANNER`. OpenAI is a
*Phase-2-specific* sidecar — you don't want flipping `SKILL_ABSTRACTION_LLM`
to also yank the main planner to OpenAI. Two env namespaces, two
adapters.

That said, `chat_llm.ChatLLM` does grow an `openai` backend so the
generic ChatLLM contract still works (used by tests + the abstraction
pass invokes `chat_json` via this path).

Model selection precedence:
  1. explicit `model=` arg to constructor
  2. env var OPENAI_MODEL (legacy / chat use)
  3. env var SKILL_ABSTRACTION_MODEL (Phase 2 use)
  4. default `gpt-4o-mini`

Failure mode: every public method **returns "" / {} / None on any
error** instead of raising. The Phase 2 modules treat absent OpenAI
output as "no abstraction this round, fall back to raw procedure" —
this matches the failure-isolation contract Phase 1A established
(never let a learning-system error block the user's task).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# .env loader (zero-dependency, idempotent)
# ---------------------------------------------------------------------------
# We deliberately do NOT pull in python-dotenv — it's overkill for a single
# KEY=VALUE file and would force every user to install another package.
# This loader runs ONCE at module import and is a no-op if the file is
# missing.
#
# Process environment variables take precedence over `.env`.  This matches
# standard deployment behaviour and, importantly for a live demo, ensures a
# key/model explicitly set in the current PowerShell cannot be silently
# replaced by an old local file.  `.env` only fills values that are absent.
_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    """Walk up from this file looking for a `.env` and apply its values
    to missing `os.environ` keys. Idempotent."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            try:
                for raw in candidate.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'").strip('"')
                    if key:
                        os.environ.setdefault(key, value)
            except Exception:
                # .env malformed — silently skip; we'll fall back to
                # whatever's already in the shell environment.
                pass
            return  # Stop at the first .env we find.


_load_dotenv_once()


_DEFAULT_CHAT_MODEL = "gpt-4o-mini"
_DEFAULT_EMBED_MODEL = "text-embedding-3-small"

_CHAT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_EMBED_ENDPOINT = "https://api.openai.com/v1/embeddings"


def _resolve_chat_model(explicit: str | None = None) -> str:
    """Pick the chat model: explicit arg → OPENAI_MODEL →
    SKILL_ABSTRACTION_MODEL → default."""
    if explicit:
        return explicit
    return (
        os.environ.get("OPENAI_MODEL")
        or os.environ.get("SKILL_ABSTRACTION_MODEL")
        or _DEFAULT_CHAT_MODEL
    )


def _resolve_embed_model(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    return (
        os.environ.get("OPENAI_EMBED_MODEL")
        or os.environ.get("SKILL_EMBEDDING_MODEL")
        or _DEFAULT_EMBED_MODEL
    )


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


# ---------------------------------------------------------------------------
# Low-level: HTTP POST with one retry on 429 / 5xx.
# Kept private — public functions below handle response parsing.
# ---------------------------------------------------------------------------
def _post_with_retry(
    url: str,
    payload: dict,
    *,
    timeout: int = 60,
    retry_on_transient: bool = True,
) -> dict | None:
    """Return the parsed JSON response, or None on any failure.

    Transient failures (429, 500-504, network error) are retried once
    after honouring `Retry-After` (capped 60s). Non-transient errors
    (400, 401, 403) return None immediately — no point retrying a bad
    key or bad request.
    """
    key = _api_key()
    if not key:
        return None
    try:
        import httpx  # type: ignore
    except ImportError:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    def _do_post() -> "httpx.Response":  # type: ignore[name-defined]
        return httpx.post(url, headers=headers, json=payload, timeout=timeout)

    try:
        r = _do_post()
        if retry_on_transient and (r.status_code == 429
                                   or 500 <= r.status_code < 600):
            wait = _parse_retry_after(r.headers.get("Retry-After"), 5)
            time.sleep(wait)
            r = _do_post()
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception:
        return None


def _parse_retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        n = float(str(value).strip())
    except ValueError:
        return default
    return max(1.0, min(60.0, n))


# ---------------------------------------------------------------------------
# Public: chat (used by chat_llm.ChatLLM "openai" backend + Distiller
# abstraction pass)
# ---------------------------------------------------------------------------
def openai_chat(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    timeout: int = 60,
) -> str:
    """Plain-text chat completion. Returns "" on any failure."""
    chosen = _resolve_chat_model(model)
    payload = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = _post_with_retry(_CHAT_ENDPOINT, payload, timeout=timeout)
    if not data:
        return ""
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def openai_chat_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    timeout: int = 60,
) -> dict:
    """JSON-mode chat completion using OpenAI's `response_format=json_object`.

    Returns `{}` on any failure / unparseable output. We always pair this
    with an extra defensive `_extract_json` so even if the model wraps
    its JSON in prose (legacy behaviour) we still recover.
    """
    chosen = _resolve_chat_model(model)
    payload = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    data = _post_with_retry(_CHAT_ENDPOINT, payload, timeout=timeout)
    if not data:
        return {}
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return {}
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Public: embeddings (used by util/embeddings.py)
# ---------------------------------------------------------------------------
def openai_embed(
    texts: list[str],
    *,
    model: str | None = None,
    timeout: int = 30,
) -> list[list[float]] | None:
    """Batch-embed a list of strings. Returns:
      - list of vectors (one per input) on success
      - None on any failure (key missing / network / 4xx)

    Callers (skill_manager, util/embeddings) treat None as "embedding
    unavailable — fall back to BM25". This is the single failure-
    isolation channel for the embedding pipeline.

    Empty input list returns []. An empty STRING in the list is still
    embedded (OpenAI returns a zero-ish vector), so we don't filter.
    """
    if not texts:
        return []
    chosen = _resolve_embed_model(model)
    payload = {"model": chosen, "input": texts}
    data = _post_with_retry(_EMBED_ENDPOINT, payload, timeout=timeout)
    if not data:
        return None
    try:
        items = data["data"]
        return [item["embedding"] for item in items]
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Convenience: single-text embed (most callers only have one string)
# ---------------------------------------------------------------------------
def openai_embed_one(
    text: str,
    *,
    model: str | None = None,
    timeout: int = 30,
) -> list[float] | None:
    out = openai_embed([text], model=model, timeout=timeout)
    if not out:
        return None
    return out[0] if out else None


# ---------------------------------------------------------------------------
# Planner adapter (Phase B of SANDBOX_PLAN) — lets Module 102 run on an
# OpenAI model. Same contract as GroqPlanner: plan() returns either a
# plan dict or a refusal dict, never raises. Model precedence:
#   1. explicit `model=` arg
#   2. env OPENAI_PLANNER_MODEL  (planner-specific override)
#   3. env OPENAI_MODEL          (shared with chat use)
#   4. default gpt-4o-mini
# ---------------------------------------------------------------------------

def _planner_id_from_model(model: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"openai_{safe}"


class OpenAIPlanner:
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 timeout: int = 60) -> None:
        self.model = (
            model
            or os.environ.get("OPENAI_PLANNER_MODEL")
            or _resolve_chat_model(None)
        )
        self.planner_id = _planner_id_from_model(self.model)
        self.api_key = api_key if api_key is not None else _api_key()
        self.timeout = timeout
        self.endpoint = _CHAT_ENDPOINT

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        if not self.api_key:
            return self._refusal(planning_brief, "model_error",
                                 "OPENAI_API_KEY missing")
        try:
            import httpx  # type: ignore
        except ImportError:
            return self._refusal(planning_brief, "model_error",
                                 "httpx not installed")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",
                 "content": "PlanningBrief:\n" + json.dumps(planning_brief)},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        try:
            r = httpx.post(self.endpoint, headers=headers, json=payload,
                           timeout=self.timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                time.sleep(_parse_retry_after(r.headers.get("Retry-After"), 5))
                r = httpx.post(self.endpoint, headers=headers, json=payload,
                               timeout=self.timeout)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            return self._refusal(planning_brief, "model_error", str(exc))
        try:
            parsed = self._extract_json(text)
        except ValueError:
            return self._refusal(planning_brief, "format_failure",
                                 "non_json_response")
        if "refusal_type" in parsed:
            return parsed
        if not parsed.get("actions"):
            return self._refusal(planning_brief, "empty_plan",
                                 "no_actions_returned")
        import uuid as _uuid
        parsed.setdefault("plan_id", f"plan_{_uuid.uuid4().hex[:12]}")
        parsed.setdefault("task_id", planning_brief.get("task_id", "unknown"))
        parsed.setdefault("planner_id", self.planner_id)
        parsed.setdefault("planning_mode",
                          planning_brief.get("planning_mode", "direct"))
        parsed.setdefault("used_refusal_recovery", False)
        return parsed

    def _refusal(self, brief: dict, refusal_type: str, message: str) -> dict:
        import uuid as _uuid
        return {
            "refusal_id": f"refusal_{_uuid.uuid4().hex[:12]}",
            "task_id": brief.get("task_id", "unknown"),
            "planner_id": self.planner_id,
            "refusal_type": refusal_type, "message": message,
            "raw_output_hash": "",
            "recovery_allowed": refusal_type != "universal_hard_safety_refusal",
        }

    @staticmethod
    def _extract_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("no_json_object")
        return json.loads(match.group(0))
