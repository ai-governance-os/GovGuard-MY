"""OpenAI-compatible provider — OpenAI/DeepSeek chat plus OpenAI embeddings.

This adapter supports the competition Mixed Live path and **Phase 2 use cases**:
  * OpenAI or DeepSeek Chat Completions for semantic intake, drafting, judging,
    and planning. The actual provider/model is exposed in the task audit.
  * `SKILL_ABSTRACTION_LLM=openai` — Module 109B Distiller's second pass
    that lifts a raw procedure into `## Principle` + `## Parameters`.
    GPT-4o-mini quality is materially better than Qwen3 here without
    blowing the budget (abstraction fires <1× per task average).
  * `SKILL_EMBEDDING_PROVIDER=openai` — `util/embeddings.py` calls into
    `openai_embed()` to vectorise skill descriptions / principles for
    cosine-similarity retrieval.

The transport is intentionally shared because DeepSeek implements the OpenAI
Chat Completions shape. Provider identity, credentials, endpoint, model, and
thinking controls remain explicit and auditable. There is no cross-provider
fallback: a provider failure returns the existing deterministic governed
fallback. OpenAI embeddings remain a separate optional lane so a DeepSeek key
is never sent to an OpenAI endpoint.

OpenAI model selection precedence:
  1. explicit `model=` arg to constructor
  2. env var OPENAI_MODEL (legacy / chat use)
  3. env var SKILL_ABSTRACTION_MODEL (Phase 2 use)
  4. default `gpt-4o-mini`

DeepSeek model selection precedence is explicit `model=` → `DEEPSEEK_MODEL` →
compatible `OPENAI_MODEL` → `deepseek-v4-flash`.

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
    if os.environ.get("TEOW_AGL_SKIP_DOTENV", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        _DOTENV_LOADED = True
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
_DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
_DEFAULT_EMBED_MODEL = "text-embedding-3-small"

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
_DEFAULT_EMBED_BASE_URL = "https://api.openai.com/v1"
_VALID_CHAT_PROVIDERS = {"openai", "deepseek"}


def _provider_name(explicit: str | None = None) -> str:
    """Return the actual chat provider without making a network call.

    ``OPENAI_*`` remains a supported compatibility namespace because
    DeepSeek implements the OpenAI Chat Completions shape.  Provider identity
    is nevertheless inferred and recorded honestly so a DeepSeek request is
    never labelled as OpenAI in the audit trail.
    """
    configured = (explicit or os.environ.get("TEOW_AGL_LIVE_PROVIDER") or "")
    configured = configured.strip().lower()
    if configured in _VALID_CHAT_PROVIDERS:
        return configured
    base = os.environ.get("OPENAI_BASE_URL", "").strip().lower()
    model = (
        os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("OPENAI_PLANNER_MODEL")
        or ""
    ).strip().lower()
    if "deepseek" in base or model.startswith("deepseek-"):
        return "deepseek"
    return "openai"


def _base_url(provider: str | None = None) -> str:
    name = _provider_name(provider)
    configured = os.environ.get("OPENAI_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    if name == "deepseek":
        return _DEFAULT_DEEPSEEK_BASE_URL
    return _DEFAULT_OPENAI_BASE_URL


def _chat_endpoint(provider: str | None = None) -> str:
    return _base_url(provider) + "/chat/completions"


def _embed_endpoint() -> str:
    base = (
        os.environ.get("OPENAI_EMBED_BASE_URL")
        or _DEFAULT_EMBED_BASE_URL
    )
    return base.strip().rstrip("/") + "/embeddings"


def _provider_config_error(
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Fail closed on an obvious provider/model/endpoint mismatch.

    This prevents the common expensive mistake where a DeepSeek model/key is
    accidentally aimed at OpenAI, or an OpenAI model/key is aimed at
    DeepSeek.  Unknown compatible gateways remain operator-controlled.
    """
    name = _provider_name(provider)
    base = _base_url(name).lower()
    chosen = (model or _resolve_chat_model(provider=name)).strip().lower()
    if name == "deepseek":
        if "api.openai.com" in base:
            return "deepseek_provider_cannot_use_openai_endpoint"
        if chosen and not chosen.startswith("deepseek-"):
            return "deepseek_provider_requires_deepseek_model"
    else:
        if "deepseek" in base:
            return "openai_provider_cannot_use_deepseek_endpoint"
        if chosen.startswith("deepseek-"):
            return "openai_provider_cannot_use_deepseek_model"
    return ""


def _thinking_field(provider: str | None = None) -> dict | None:
    """Return DeepSeek's thinking control only for a DeepSeek request."""
    if _provider_name(provider) != "deepseek":
        return None
    mode = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower()
    if mode not in ("enabled", "disabled"):
        mode = "disabled"
    return {"type": mode}


def _apply_thinking(payload: dict, provider: str | None = None) -> None:
    field = _thinking_field(provider)
    if field is not None:
        payload["thinking"] = field


def _resolve_chat_model(
    explicit: str | None = None,
    provider: str | None = None,
) -> str:
    """Pick the chat model: explicit arg → OPENAI_MODEL →
    SKILL_ABSTRACTION_MODEL → default."""
    if explicit:
        return explicit
    if _provider_name(provider) == "deepseek":
        return (
            os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or _DEFAULT_DEEPSEEK_MODEL
        )
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


def _api_key(provider: str | None = None) -> str:
    if _provider_name(provider) == "deepseek":
        return (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
    return os.environ.get("OPENAI_API_KEY", "").strip()


def _embedding_api_key() -> str:
    explicit = os.environ.get("OPENAI_EMBED_API_KEY", "").strip()
    if explicit:
        return explicit
    if os.environ.get("SKILL_EMBEDDING_PROVIDER", "").strip().lower() == "openai":
        # The embedding lane has its own explicit provider switch. It must not
        # inherit the chat provider identity (for example DeepSeek) after the
        # operator deliberately selects OpenAI embeddings.
        return os.environ.get("OPENAI_API_KEY", "").strip()
    # In DeepSeek compatibility mode OPENAI_API_KEY contains a DeepSeek key.
    # Never send that credential to OpenAI's embedding endpoint.
    if _provider_name() == "deepseek":
        return ""
    return os.environ.get("OPENAI_API_KEY", "").strip()


def active_chat_provider() -> str:
    return _provider_name()


def active_chat_model() -> str:
    return _resolve_chat_model(provider=_provider_name())


def chat_api_configured() -> bool:
    provider = _provider_name()
    model = _resolve_chat_model(provider=provider)
    return bool(_api_key(provider)) and not _provider_config_error(
        provider, model,
    )


def embedding_api_configured() -> bool:
    return bool(_embedding_api_key())


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
    api_key: str | None = None,
) -> dict | None:
    """Return the parsed JSON response, or None on any failure.

    Transient failures (429, 500-504, network error) are retried once
    after honouring `Retry-After` (capped 60s). Non-transient errors
    (400, 401, 403) return None immediately — no point retrying a bad
    key or bad request.
    """
    key = _api_key() if api_key is None else api_key.strip()
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


def _message_content(data: dict | None) -> str:
    """Extract final assistant content from an OpenAI-compatible response.

    DeepSeek V4 thinking responses place the private reasoning beside
    ``content`` as ``reasoning_content``. Only the final ``content`` is part
    of the application contract; reasoning must never be parsed as a plan or
    exposed in an artifact. A defensive list-content branch also keeps the
    adapter compatible with gateways that return typed text parts.
    """
    try:
        content = data["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content") or ""
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts).strip()
    return ""


def _extract_json_object(text: str) -> dict:
    """Return one JSON object from final model text, never from reasoning."""
    cleaned = (text or "").strip()
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _json_chat_with_recovery(
    payload: dict,
    *,
    provider: str,
    timeout: int,
    api_key: str,
) -> tuple[dict, str]:
    """Make a JSON chat call with one bounded DeepSeek V4 recovery.

    DeepSeek documents two relevant behaviours: JSON mode can occasionally
    return empty final content, and in thinking mode ``max_tokens`` covers
    both reasoning and the final answer. A schema-sized call can therefore
    spend its entire budget before emitting JSON. We keep the operator's
    preferred thinking mode for the first attempt, then retry exactly once in
    non-thinking mode with explicit JSON wording. No provider is switched,
    and no reasoning text is accepted as application data.
    """
    data = _post_with_retry(
        _chat_endpoint(provider), payload, timeout=timeout, api_key=api_key,
    )
    parsed = _extract_json_object(_message_content(data))
    if parsed:
        return parsed, "primary"
    if data is None:
        # Recovery repairs a successful HTTP response whose final content is
        # empty or malformed. It must not repeat an outage, rate-limit or
        # server failure with a second complete request sequence.
        return {}, "transport_or_http_error"
    if provider != "deepseek":
        return {}, "empty_or_unparseable"

    recovery = dict(payload)
    recovery["thinking"] = {"type": "disabled"}
    recovery.pop("reasoning_effort", None)
    recovery["max_tokens"] = max(int(payload.get("max_tokens") or 0), 4096)
    messages = [dict(item) for item in payload.get("messages") or []]
    if messages:
        last = dict(messages[-1])
        last["content"] = (
            str(last.get("content") or "")
            + "\n\nJSON RECOVERY: Return one complete, non-empty JSON object "
              "matching the requested schema. Do not output prose or fences."
        )
        messages[-1] = last
    recovery["messages"] = messages
    data = _post_with_retry(
        _chat_endpoint(provider), recovery, timeout=timeout,
        retry_on_transient=False, api_key=api_key,
    )
    parsed = _extract_json_object(_message_content(data))
    if parsed:
        return parsed, "deepseek_nonthinking_recovery"
    return {}, (
        "transport_or_http_error_after_recovery" if data is None
        else "deepseek_recovery_empty_or_unparseable"
    )


def _parse_retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        n = float(str(value).strip())
    except ValueError:
        return default
    return max(1.0, min(60.0, n))


# ---------------------------------------------------------------------------
# Public: chat (used by ChatLLM's OpenAI and DeepSeek backends + Distiller)
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
    provider = _provider_name()
    chosen = _resolve_chat_model(model, provider=provider)
    if _provider_config_error(provider, chosen):
        return ""
    payload = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    _apply_thinking(payload, provider)
    data = _post_with_retry(
        _chat_endpoint(provider), payload, timeout=timeout,
        api_key=_api_key(provider),
    )
    if not data:
        return ""
    return _message_content(data)


def openai_chat_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    timeout: int = 60,
) -> dict:
    """JSON-mode chat completion using OpenAI-compatible `json_object` mode.

    Returns `{}` on any failure / unparseable output. We always pair this
    with an extra defensive `_extract_json` so even if the model wraps
    its JSON in prose (legacy behaviour) we still recover.
    """
    provider = _provider_name()
    chosen = _resolve_chat_model(model, provider=provider)
    if _provider_config_error(provider, chosen):
        return {}
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
    _apply_thinking(payload, provider)
    parsed, _ = _json_chat_with_recovery(
        payload,
        provider=provider,
        timeout=timeout,
        api_key=_api_key(provider),
    )
    return parsed


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
    data = _post_with_retry(
        _embed_endpoint(), payload, timeout=timeout,
        api_key=_embedding_api_key(),
    )
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
# compatible provider. Same contract as GroqPlanner: plan() returns either a
# plan dict or a refusal dict, never raises. Model precedence:
#   1. explicit `model=` arg
#   2. env OPENAI_PLANNER_MODEL  (planner-specific override)
#   3. env OPENAI_MODEL          (shared with chat use)
#   4. default gpt-4o-mini
# ---------------------------------------------------------------------------

def _planner_id_from_model(model: str, provider: str = "openai") -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    prefix = provider if provider in _VALID_CHAT_PROVIDERS else "openai"
    return f"{prefix}_{safe}"


class OpenAIPlanner:
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 timeout: int = 60, provider: str | None = None) -> None:
        self.provider = _provider_name(provider)
        self.model = (
            model
            or os.environ.get("OPENAI_PLANNER_MODEL")
            or _resolve_chat_model(None, provider=self.provider)
        )
        self.planner_id = _planner_id_from_model(self.model, self.provider)
        self.api_key = (
            api_key if api_key is not None else _api_key(self.provider)
        )
        self.timeout = timeout
        self.endpoint = _chat_endpoint(self.provider)
        self.config_error = _provider_config_error(
            self.provider, self.model,
        )
        try:
            configured_max = int(os.environ.get(
                "TEOW_AGL_PLANNER_MAX_TOKENS", "2000",
            ))
        except ValueError:
            configured_max = 2000
        self.max_tokens = max(256, min(8000, configured_max))

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        if self.config_error:
            return self._refusal(
                planning_brief, "model_error",
                "provider_config_error:" + self.config_error,
            )
        if not self.api_key:
            return self._refusal(planning_brief, "model_error",
                                 f"{self.provider}_api_key_missing")
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
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        _apply_thinking(payload, self.provider)
        parsed, recovery = _json_chat_with_recovery(
            payload,
            provider=self.provider,
            timeout=self.timeout,
            api_key=self.api_key,
        )
        if not parsed:
            refusal_type = (
                "model_error" if recovery.startswith("transport_or_http_error")
                else "format_failure"
            )
            return self._refusal(
                planning_brief, refusal_type,
                "non_json_response:" + recovery,
            )
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
