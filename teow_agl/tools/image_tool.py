"""
Image generation tool.

Backends (selected by env IMAGE_PROVIDER, default = pollinations):

  IMAGE_PROVIDER=pollinations  → https://image.pollinations.ai (DEFAULT)
                                 Truly free, no auth, no signup. Quality is
                                 mid-tier (not Midjourney/DALL-E), but it
                                 works out of the box for everybody.

  IMAGE_PROVIDER=gemini        → Google AI Studio (needs GEMINI_API_KEY).
                                 As of 2025 the free tier no longer ships
                                 with image generation in most regions —
                                 every `gemini-*-image-*` model returns
                                 404, and `imagen-*` returns 400 "Imagen
                                 is only available on paid plans".
                                 Use this if you've upgraded to paid.

  IMAGE_PROVIDER=openai        → OpenAI DALL-E 3 (needs OPENAI_API_KEY).
                                 Paid (~$0.04/img standard, $0.08/HD).
                                 Best quality of the three; minimal setup.

  (no provider, nothing set)   → falls back to pollinations.

If a provider fails (network down, rate-limited, region-blocked, etc.)
the tool drops to a labelled placeholder PNG so the rest of the
governance pipeline / UI still has something to show.

Operations:
  generate_image — produce an image from a text prompt; save under
                   outputs/_images/<filename>.png

Action.metadata fields:
  prompt:    str (required) — what to draw
  size:      str ("1024x1024" default; "1024x768", "768x1024" supported)
  model:     str — optional override of the provider's model name
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..models import CandidateAction
from ..util.path_guard import resolve_safe
from ._safety import safe_target_check


# Free-tier-friendly default. Uses the `generateContent` endpoint which is
# included in Google AI Studio's free quota. The older `imagen-3.0-*` names
# go through `:predict` and require paid Vertex AI access — keep them
# available behind explicit opt-in but don't default to them.
DEFAULT_MODEL = "gemini-2.5-flash-image-preview"
# If the primary free-tier model returns 404 / "not found" we transparently
# retry with this fallback (the older preview name is still around as of
# 2025 for many keys).
FALLBACK_FREE_TIER_MODELS = (
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image-generation",
    "gemini-2.0-flash-exp",
)


class ImageGenTool:
    name = "image_gen"

    def __init__(
        self,
        *,
        images_dir: str | Path,
        workspace_roots: list[str] | None = None,
        api_key_env: str = "GEMINI_API_KEY",
        timeout: int = 60,
    ) -> None:
        self.images_dir = Path(images_dir)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_roots = list(workspace_roots or [str(self.images_dir)])
        self.api_key_env = api_key_env
        self.timeout = timeout

    def __call__(self, action: CandidateAction) -> dict[str, Any]:
        op = (action.operation or "").lower()
        if op not in ("generate_image", "generate", "image"):
            return _failed(f"unknown_image_op:{op}")
        meta = action.metadata or {}
        prompt = (meta.get("prompt") or meta.get("description") or "").strip()
        if not prompt:
            return _failed("missing_prompt")
        size = (meta.get("size") or "1024x1024").strip()
        # Resolution order for the model name:
        #   1. action.metadata.model (the planner can override per-action)
        #   2. env IMAGE_MODEL (operator's deployment-wide override)
        #   3. DEFAULT_MODEL (free-tier-friendly default)
        model = (meta.get("model")
                 or os.environ.get("IMAGE_MODEL")
                 or DEFAULT_MODEL).strip()
        api_key = os.environ.get(self.api_key_env, "").strip()

        # Synthesize a filename from the prompt (safe, deterministic).
        slug = _slugify(prompt)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{slug[:40]}_{uuid.uuid4().hex[:6]}.png"
        target = self.images_dir / filename
        # safety: ensure within configured workspace_roots
        ok, reason = safe_target_check(str(target), self.workspace_roots)
        if not ok and reason != "target_is_workspace_root_itself":
            return _failed(f"target_unsafe:{reason}")

        try:
            import httpx  # type: ignore
        except ImportError:
            return _failed("httpx_not_installed")

        # Provider selection. Default = pollinations because it works for
        # everyone without an API key. Explicit GEMINI_API_KEY does NOT
        # auto-flip to gemini any more — too many free-tier accounts get
        # 400s / 404s, so opt-in via IMAGE_PROVIDER=gemini.
        provider = (os.environ.get("IMAGE_PROVIDER")
                    or meta.get("provider") or "pollinations").strip().lower()

        if provider == "placeholder":
            # Deterministic offline mode (used by tests + as an emergency
            # fallback when the operator wants to disable outbound calls).
            try:
                _write_placeholder_png(target, prompt=prompt, size=size)
            except Exception as exc:
                return _failed(f"placeholder_write_failed:{exc}")
            return {
                "status": "success",
                "summary": f"image_placeholder_saved:{target.name}",
                "affected": [str(target)],
                "image_path": str(target),
                "image_filename": target.name,
                "image_source": "placeholder",
                "prompt": prompt,
            }

        if provider == "pollinations":
            result = _call_pollinations(httpx, prompt, size, target, self.timeout)
            if result is not None:
                return result
            # fall through to placeholder

        elif provider == "openai":
            openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not openai_key:
                _write_placeholder_png(target, prompt=prompt, size=size,
                                       error="OPENAI_API_KEY missing")
                return _failed(
                    "openai_api_key_missing; placeholder_written:" + target.name)
            result = _call_openai_dalle(httpx, openai_key, prompt, size, target,
                                        self.timeout, model)
            if result is not None:
                return result
            # fall through to placeholder

        elif provider == "gemini":
            if not api_key:
                _write_placeholder_png(target, prompt=prompt, size=size,
                                       error="GEMINI_API_KEY missing")
                return _failed(
                    "gemini_api_key_missing; placeholder_written:" + target.name)
            result = self._call_gemini_with_fallbacks(
                httpx, api_key, model, prompt, size, target)
            if result is not None:
                return result
            # fall through to placeholder

        else:
            return _failed(f"unknown_image_provider:{provider}")

        # Universal fallback: a labelled placeholder PNG.
        try:
            _write_placeholder_png(target, prompt=prompt, size=size,
                                   error=f"{provider}_failed")
        except Exception as exc:
            return _failed(f"placeholder_write_failed:{exc}")
        return {
            "status": "failed",
            "summary": f"{provider}_failed; placeholder_written:{target.name}",
            "affected": [str(target)],
            "error": f"{provider}_failed",
            "image_path": str(target),
            "image_filename": target.name,
            "image_source": "placeholder",
            "prompt": prompt,
        }

    # ------------------------------------------------------------------
    # Gemini path (kept as opt-in for users with paid Google AI plans)
    # ------------------------------------------------------------------
    def _call_gemini_with_fallbacks(self, httpx_mod, api_key: str, model: str,
                                    prompt: str, size: str,
                                    target: Path) -> dict[str, Any] | None:
        """Original Gemini logic, factored out so the dispatcher above stays
        readable. Returns the executor-success dict on success, or None to
        signal 'fell through; caller should write a placeholder'."""

        # Build the ordered list of models to try.
        #   1. Whatever the planner / env asked for (highest priority).
        #   2. Our hardcoded free-tier fallback list.
        #   3. AUTO-DISCOVERY: ask Google "what models does THIS key support",
        #      filter to image-capable ones. This handles Google's frequent
        #      preview-name renames — we never need to ship a new fallback
        #      list when they rebrand.
        models_to_try: list[str] = [model]
        if not model.lower().startswith("imagen"):
            for alt in FALLBACK_FREE_TIER_MODELS:
                if alt != model and alt not in models_to_try:
                    models_to_try.append(alt)
        # Auto-discovery (best-effort; never raises).
        for alt in _discover_image_capable_models(httpx_mod, api_key, self.timeout):
            if alt not in models_to_try:
                models_to_try.append(alt)

        last_error: str = ""
        last_status: int | None = None
        last_body_snippet: str = ""
        attempted: list[str] = []
        for candidate in models_to_try:
            attempted.append(candidate)
            try:
                if candidate.lower().startswith("imagen"):
                    b64, used_endpoint = _call_imagen_predict(
                        httpx_mod, api_key, candidate, prompt, size, self.timeout)
                else:
                    b64, used_endpoint = _call_gemini_generate_content(
                        httpx_mod, api_key, candidate, prompt, size, self.timeout)
            except _ApiError as exc:
                last_error = exc.message
                last_status = exc.status
                last_body_snippet = exc.body_snippet
                # 404 / "model not found" → try the next candidate
                if exc.status in (404,) or "not found" in exc.message.lower():
                    continue
                # Other errors (403 perms, 429 rate, 5xx) → stop and report
                break
            if not b64:
                last_error = "no_image_in_response"
                continue
            # Success: decode + write with magic-byte-derived extension
            try:
                actual = _save_image_with_correct_extension(
                    target, base64.b64decode(b64))
            except Exception as exc:
                return _failed(f"write_failed:{exc}")
            return {
                "status": "success",
                "summary": f"image_saved:{actual.name}",
                "affected": [str(actual)],
                "image_path": str(actual),
                "image_filename": actual.name,
                "image_source": candidate,
                "endpoint": used_endpoint,
                "prompt": prompt,
            }

        # All candidates exhausted → write the placeholder + return a
        # FAILED result that carries the Google error verbatim so the UI
        # can show the user what went wrong. (We do not fall through to
        # the universal placeholder writer above, because we already wrote
        # one here with the specific error text drawn on it.)
        full_error = (
            f"gemini_api_failed[{last_status or 'n/a'}]:{last_error}"
            + (f" tried={','.join(attempted)}" if attempted else "")
            + (f" | body={last_body_snippet}" if last_body_snippet else "")
        )
        _write_placeholder_png(target, prompt=prompt, size=size, error=full_error)
        return {
            "status": "failed",
            "summary": f"{full_error}; placeholder_written:{target.name}",
            "affected": [str(target)],
            "error": full_error,
            "image_path": str(target),
            "image_filename": target.name,
            "image_source": "placeholder",
            "prompt": prompt,
        }


# ---------------------------------------------------------------------------
# API call helpers
# ---------------------------------------------------------------------------

# Process-wide cache for discovered model names. Google sometimes changes
# preview model names; rather than ship a stale hardcoded list we ask the
# /models endpoint what THIS key can access and filter to image-capable
# names. Keyed by api_key suffix so we don't re-query per request.
_DISCOVERY_CACHE: dict[str, list[str]] = {}


def _discover_image_capable_models(httpx_mod, api_key: str, timeout: int) -> list[str]:
    """Return a list of model short-names this API key can use for image
    generation, ordered roughly by 'most likely to be image-capable first'.

    Best-effort. Never raises — returns [] on any failure so callers can
    treat it as 'no extra candidates'.
    """
    if not api_key:
        return []
    cache_key = api_key[-12:]
    cached = _DISCOVERY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        r = httpx_mod.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=timeout,
        )
        if r.status_code >= 400:
            _DISCOVERY_CACHE[cache_key] = []
            return []
        data = r.json()
    except Exception:
        _DISCOVERY_CACHE[cache_key] = []
        return []

    out: list[str] = []
    for m in (data.get("models") or []):
        if not isinstance(m, dict):
            continue
        # `name` looks like "models/gemini-2.5-flash-image-preview" — strip
        # the prefix to get the short name the rest of the tool expects.
        full = str(m.get("name") or "")
        short = full.split("/", 1)[1] if "/" in full else full
        if not short:
            continue
        display = str(m.get("displayName") or "").lower()
        desc = str(m.get("description") or "").lower()
        methods = m.get("supportedGenerationMethods") or []
        haystack = f"{short.lower()} {display} {desc}"
        # Heuristic: any model whose name or description mentions image
        # generation. We exclude obvious chat-only models even if their
        # description happens to mention "image understanding".
        looks_image = (
            "image" in short.lower()
            or "imagen" in short.lower()
            or "image generation" in display
            or "image generation" in desc
            or "generates images" in desc
            or "text-to-image" in desc
        )
        if not looks_image:
            continue
        # Free-tier image models go through generateContent; Imagen 3
        # paid models through predict. Either is supported by our two
        # call paths upstream — we'll dispatch based on the name.
        if methods and not (
            "generateContent" in methods
            or "predict" in methods
            or "generate_content" in methods
        ):
            continue
        out.append(short)
    # Stable ordering: prefer newer-looking version numbers first.
    out.sort(key=_rough_version_key, reverse=True)
    _DISCOVERY_CACHE[cache_key] = out
    return out


def _rough_version_key(name: str) -> tuple:
    """Cheap heuristic for ordering 'gemini-2.5-flash-image-preview' >
    'gemini-2.0-...' > 'imagen-3.0-...'. Doesn't need to be exact."""
    m = re.search(r"(\d+)\.(\d+)", name or "")
    if m:
        return (int(m.group(1)), int(m.group(2)), name)
    return (0, 0, name)


class _ApiError(Exception):
    """Carries enough detail to make UI/log diagnostics useful."""

    def __init__(self, message: str, *, status: int | None = None,
                 body_snippet: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body_snippet = body_snippet


def _call_gemini_generate_content(httpx_mod, api_key: str, model: str,
                                  prompt: str, size: str,
                                  timeout: int) -> tuple[str | None, str]:
    """Call the free-tier `:generateContent` endpoint and return (b64, endpoint).

    Used by `gemini-2.x-*-image-*` style names. Returns the first inline
    image bytes found in the response. Raises _ApiError on HTTP failure.
    """
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    # Minimal payload. The Gemini image-preview models reject any
    # generationConfig key they don't recognize with HTTP 400, so we
    # keep this stripped down to the two fields the docs *require*:
    # `contents` and `responseModalities`. No temperature, no role.
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"],
        },
    }
    try:
        r = httpx_mod.post(endpoint, json=payload, timeout=timeout)
    except Exception as exc:
        raise _ApiError(f"network_error:{exc}")
    if r.status_code >= 400:
        raise _ApiError(
            message=f"http_{r.status_code}",
            status=r.status_code,
            body_snippet=(r.text or "")[:300],
        )
    try:
        data = r.json()
    except Exception:
        raise _ApiError("invalid_json_response", status=r.status_code)
    return (_extract_inline_image_from_generate_content(data),
            f"{model}:generateContent")


def _call_imagen_predict(httpx_mod, api_key: str, model: str, prompt: str,
                         size: str, timeout: int) -> tuple[str | None, str]:
    """Call the legacy `:predict` endpoint for `imagen-*` models.

    Usually requires PAID Vertex AI billing — a vanilla AI Studio free
    key will get 403/404 here.
    """
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:predict?key={api_key}"
    )
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": _aspect_ratio_for(size)},
    }
    try:
        r = httpx_mod.post(endpoint, json=payload, timeout=timeout)
    except Exception as exc:
        raise _ApiError(f"network_error:{exc}")
    if r.status_code >= 400:
        raise _ApiError(
            message=f"http_{r.status_code}",
            status=r.status_code,
            body_snippet=(r.text or "")[:300],
        )
    try:
        data = r.json()
    except Exception:
        raise _ApiError("invalid_json_response", status=r.status_code)
    return (_extract_b64_image(data), f"{model}:predict")


def _call_pollinations(httpx_mod, prompt: str, size: str, target: Path,
                       timeout: int) -> dict[str, Any] | None:
    """Free, no-auth image generation via pollinations.ai.

    The service is a plain GET that returns PNG bytes. We URL-encode the
    prompt into the path. Quality is roughly Stable Diffusion XL level —
    not Midjourney, but good enough to demonstrate end-to-end image
    generation without any API key or billing.

    Returns the executor-success dict on success, or None on any failure
    (so the caller writes a labelled placeholder instead).
    """
    import urllib.parse
    w, h = _parse_size(size)
    safe_prompt = urllib.parse.quote(prompt[:1500], safe="")
    seed = int(time.time())
    url = (
        f"https://image.pollinations.ai/prompt/{safe_prompt}"
        f"?width={w}&height={h}&nologo=true&seed={seed}"
    )
    try:
        # Pollinations sometimes takes 10-30 s to render — bump the timeout
        # if the caller's default is shorter.
        effective_timeout = max(timeout, 90)
        r = httpx_mod.get(url, timeout=effective_timeout,
                          follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    # Sanity-check that we actually got an image (the service occasionally
    # returns an error HTML page with 200 — rare, but cheap to guard).
    content = r.content
    if not content or len(content) < 1024:
        return None
    if not (content.startswith(b"\x89PNG") or content.startswith(b"\xff\xd8")):
        return None
    try:
        # P2 — pick the real extension from magic bytes (Pollinations
        # sometimes ships JPEG behind a request that looks PNG-shaped).
        actual = _save_image_with_correct_extension(target, content)
    except Exception:
        return None
    return {
        "status": "success",
        "summary": f"image_saved:{actual.name}",
        "affected": [str(actual)],
        "image_path": str(actual),
        "image_filename": actual.name,
        "image_source": "pollinations.ai",
        "endpoint": "image.pollinations.ai",
        "prompt": prompt,
    }


def _call_openai_dalle(httpx_mod, api_key: str, prompt: str, size: str,
                       target: Path, timeout: int,
                       model: str) -> dict[str, Any] | None:
    """Paid OpenAI DALL-E call. Activates when IMAGE_PROVIDER=openai.

    Uses the /v1/images/generations endpoint with response_format=b64_json
    so we get image bytes inline rather than a temporary URL we'd have to
    download separately.

    Returns the executor-success dict on success, or None on any failure.
    """
    # OpenAI's images endpoint accepts a fixed set of sizes for DALL-E 3:
    # 1024x1024, 1024x1792, 1792x1024. Map our generic size accordingly.
    requested = (size or "").lower().strip()
    if "1024x1792" in requested or "portrait" in requested or "9:16" in requested:
        dalle_size = "1024x1792"
    elif "1792x1024" in requested or "landscape" in requested or "16:9" in requested:
        dalle_size = "1792x1024"
    else:
        dalle_size = "1024x1024"
    dalle_model = model if model and "dall" in model.lower() else "dall-e-3"
    payload = {
        "model": dalle_model,
        "prompt": prompt[:4000],
        "n": 1,
        "size": dalle_size,
        "response_format": "b64_json",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        r = httpx_mod.post(
            "https://api.openai.com/v1/images/generations",
            headers=headers, json=payload, timeout=max(timeout, 60),
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
        b64 = data["data"][0]["b64_json"]
        actual = _save_image_with_correct_extension(
            target, base64.b64decode(b64))
    except Exception:
        return None
    return {
        "status": "success",
        "summary": f"image_saved:{actual.name}",
        "affected": [str(actual)],
        "image_path": str(actual),
        "image_filename": actual.name,
        "image_source": dalle_model,
        "endpoint": "api.openai.com/v1/images/generations",
        "prompt": prompt,
    }


def _extract_inline_image_from_generate_content(resp: dict) -> str | None:
    """Pull the first inline image's base64 out of a generateContent reply.

    Shape (simplified):
      {
        "candidates": [{
          "content": {
            "parts": [
              {"text": "..."},
              {"inlineData": {"mimeType": "image/png", "data": "<b64>"}}
              -- or camelCase variants depending on API version --
            ]
          }
        }]
      }
    """
    if not isinstance(resp, dict):
        return None
    for cand in (resp.get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        content = cand.get("content") or {}
        for part in (content.get("parts") or []):
            if not isinstance(part, dict):
                continue
            # camelCase + snake_case for safety
            inline = (part.get("inlineData")
                      or part.get("inline_data")
                      or {})
            if isinstance(inline, dict):
                data = inline.get("data") or inline.get("bytesBase64Encoded")
                if isinstance(data, str) and len(data) > 100:
                    return data
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ext_for_bytes(content: bytes) -> str:
    """Pick the right file extension from the image's magic bytes.

    Pollinations.ai sometimes returns JPEG even when we requested a PNG
    URL; before this helper we always wrote `.png` regardless, which
    confuses strict image libraries that check the extension matches the
    magic. Fix is config-free: look at the first few bytes.
    """
    if not content:
        return ".png"
    if content.startswith(b"\xff\xd8"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if content.startswith(b"RIFF") and len(content) >= 12 \
            and content[8:12] == b"WEBP":
        return ".webp"
    return ".png"  # safe default — most providers do PNG


def _save_image_with_correct_extension(target: Path, content: bytes) -> Path:
    """Write bytes to disk and return the actual path used, with the
    extension derived from magic bytes (not from the requested name).
    The caller passes a `target` whose stem (the synthesized filename
    minus extension) is preserved; only the suffix is replaced.
    Idempotent: if the magic-byte ext already matches, no rename.
    """
    actual = target.with_suffix(_ext_for_bytes(content))
    actual.write_bytes(content)
    return actual


def _slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "image").strip("_").lower()
    return s or "image"


def _aspect_ratio_for(size: str) -> str:
    s = (size or "").lower().strip()
    if "1024x768" in s or "16:9" in s or "landscape" in s:
        return "16:9"
    if "768x1024" in s or "9:16" in s or "portrait" in s:
        return "9:16"
    return "1:1"


def _extract_b64_image(resp: dict) -> str | None:
    """Imagen response has shape {predictions: [{bytesBase64Encoded: ...}, ...]}.
    Be defensive: providers nest differently."""
    if not isinstance(resp, dict):
        return None
    preds = resp.get("predictions") or resp.get("candidates") or []
    if isinstance(preds, list) and preds:
        p0 = preds[0] if isinstance(preds[0], dict) else {}
        for k in ("bytesBase64Encoded", "imageBytes", "data", "b64", "base64"):
            v = p0.get(k)
            if isinstance(v, str) and len(v) > 100:
                return v
        # nested under "image"
        img = p0.get("image")
        if isinstance(img, dict):
            for k in ("bytesBase64Encoded", "data", "b64"):
                v = img.get(k)
                if isinstance(v, str) and len(v) > 100:
                    return v
    return None


def _write_placeholder_png(path: Path, *, prompt: str, size: str = "1024x1024",
                           error: str | None = None) -> None:
    """Render a labeled placeholder PNG so the UI has something to show even
    without a live API. Uses Pillow (already pulled in by python-pptx)."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError:
        # last-resort: write a tiny solid-color PNG via raw bytes (1x1)
        path.write_bytes(bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15"
            "C4890000000A49444154789C6300010000000500015AE9D9F100000000494E"
            "44AE426082"
        ))
        return
    w, h = _parse_size(size)
    img = Image.new("RGB", (w, h), color=(245, 246, 250))
    draw = ImageDraw.Draw(img)
    # corner label
    draw.rectangle([(0, 0), (w, 60)], fill=(31, 111, 235))
    title = "TEOW-AGL · image preview (placeholder)"
    if error:
        title = f"TEOW-AGL · placeholder (no API: {error[:40]})"
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.text((20, 20), title, fill="white", font=font)
    # body: prompt wrapped
    wrapped = _wrap(prompt, max_len=70)
    y = 90
    for line in wrapped[:18]:
        draw.text((30, y), line, fill=(40, 50, 80), font=font)
        y += 30
    img.save(str(path), "PNG")


def _parse_size(size: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", size or "")
    if m:
        return (max(64, min(2048, int(m.group(1)))),
                max(64, min(2048, int(m.group(2)))))
    return (1024, 1024)


def _wrap(text: str, max_len: int = 70) -> list[str]:
    out: list[str] = []
    cur = ""
    for word in (text or "").split():
        if len(cur) + len(word) + 1 > max_len:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


def _failed(reason: str) -> dict:
    return {"status": "failed", "summary": reason, "error": reason, "affected": []}
