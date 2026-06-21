"""
Web search tool.

Backends (selected by env WEB_SEARCH_PROVIDER, default = auto):

  WEB_SEARCH_PROVIDER=auto         → Tavily if TAVILY_API_KEY is set,
                                     otherwise DuckDuckGo (no key required).
  WEB_SEARCH_PROVIDER=tavily       → Force Tavily (requires TAVILY_API_KEY).
  WEB_SEARCH_PROVIDER=duckduckgo   → Force DuckDuckGo HTML (free, no auth).
  WEB_SEARCH_PROVIDER=disabled     → No external calls; returns [].

The tool exposes a single operation `search` that takes a query string in
`metadata.query` (or `metadata.prompt`, or `action.target`) and returns
a structured list of hits as `output_summary` JSON for the executor's
trace and (via the runtime) for the chat synthesis step.

The module also exposes a plain function `search_web(query, max_results)`
so the runtime can call it BEFORE the planner runs (the "🅰️" path —
pre-planner injection, similar to RAG). That way the planner sees real
web excerpts in its brief and can ground its answer in them.
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Any
from urllib.parse import quote_plus, unquote

from ..models import CandidateAction


# ---------------------------------------------------------------------------
# Public function — used by runtime.py for pre-planner injection
# ---------------------------------------------------------------------------
def search_web(query: str, *, max_results: int = 5, timeout: int = 12) -> list[dict]:
    """Return a list of {title, url, content, source} dicts, or [] on failure.

    Provider chosen by env. Never raises — best-effort, with empty list
    standing for "no usable results" so callers degrade gracefully.
    """
    q = (query or "").strip()
    if not q:
        return []
    provider = (os.environ.get("WEB_SEARCH_PROVIDER") or "auto").strip().lower()
    if provider == "disabled":
        return []

    try:
        import httpx  # type: ignore
    except ImportError:
        return []

    if provider == "tavily":
        return _tavily(httpx, q, max_results, timeout)
    if provider == "duckduckgo":
        return _duckduckgo(httpx, q, max_results, timeout)
    # auto:
    if os.environ.get("TAVILY_API_KEY"):
        results = _tavily(httpx, q, max_results, timeout)
        if results:
            return results
        # fall through to DDG on empty
    return _duckduckgo(httpx, q, max_results, timeout)


# ---------------------------------------------------------------------------
# Tool registration shim — used by Module 107 when a planner action picks
# the `web_search` tool directly (the future agent-loop path).
# ---------------------------------------------------------------------------
class WebSearchTool:
    name = "web_search"

    def __init__(self, *, timeout: int = 12) -> None:
        self.timeout = timeout

    def __call__(self, action: CandidateAction) -> dict[str, Any]:
        op = (action.operation or "").lower()
        if op not in ("search", "query", "web_search", ""):
            return {"status": "failed", "summary": "",
                    "error": f"web_search_unknown_op:{action.operation}",
                    "affected": []}
        meta = action.metadata or {}
        query = (
            meta.get("query") or meta.get("prompt") or meta.get("q")
            or action.target or meta.get("user_intent") or ""
        ).strip()
        if not query:
            return {"status": "failed", "summary": "",
                    "error": "web_search_missing_query", "affected": []}
        max_results = int(meta.get("max_results", 5))
        results = search_web(query, max_results=max_results, timeout=self.timeout)
        if not results:
            return {"status": "failed", "summary": "no_results",
                    "error": "web_search_no_results", "affected": []}
        # The executor stores `output_summary` as a string. Pack the hits
        # as a compact human-readable bullet list so it's useful even when
        # downstream consumers don't parse JSON.
        lines = [f"web_search for: {query}"]
        for i, hit in enumerate(results, 1):
            title = (hit.get("title") or "").strip()
            url = (hit.get("url") or "").strip()
            content = (hit.get("content") or "").strip().replace("\n", " ")
            if len(content) > 280:
                content = content[:277] + "..."
            lines.append(f"[{i}] {title}\n    {url}\n    {content}")
        return {
            "status": "success",
            "summary": "\n".join(lines),
            "affected": [],
            "results": results,  # structured payload; carried in trace details
        }


# ---------------------------------------------------------------------------
# Tavily — preferred when the user supplies a key. Free tier = 1000/mo.
# ---------------------------------------------------------------------------
def _tavily(httpx_mod, query: str, max_results: int, timeout: int) -> list[dict]:
    api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not api_key:
        return []
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max(1, min(max_results, 10)),
    }
    try:
        r = httpx_mod.post(
            "https://api.tavily.com/search",
            json=payload, timeout=timeout,
        )
        if r.status_code >= 400:
            return []
        data = r.json()
    except Exception:
        return []
    out: list[dict] = []
    for hit in (data.get("results") or [])[:max_results]:
        if not isinstance(hit, dict):
            continue
        out.append({
            "title": str(hit.get("title") or "").strip(),
            "url": str(hit.get("url") or "").strip(),
            "content": str(hit.get("content") or "").strip(),
            "source": "tavily",
            "score": hit.get("score"),
        })
    return out


# ---------------------------------------------------------------------------
# DuckDuckGo — zero-config fallback. Scrapes the HTML endpoint with a
# minimal regex; works without auth. May rate-limit if abused.
# ---------------------------------------------------------------------------
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.S | re.I,
)


# DuckDuckGo HTML occasionally returns ad redirect URLs (`y.js`, Bing
# aclick wrappers) instead of clean organic links. Filtering them out:
#   (a) keeps the planning brief smaller (no junk long tracking strings),
#   (b) keeps the user-visible Sources block free of unhelpful ad URLs.
_DDG_AD_PATTERNS = (
    "duckduckgo.com/y.js",
    "bing.com/aclick",
    "ad_domain=",
    "ad_provider=",
    "msclkid=",
    "?ad=",
    "&ad=",
    "/ads/",
    "/sponsored/",
)


def _looks_like_ad_url(url: str) -> bool:
    if not url:
        return True
    low = url.lower()
    return any(pat in low for pat in _DDG_AD_PATTERNS)


def _duckduckgo(httpx_mod, query: str, max_results: int, timeout: int) -> list[dict]:
    headers = {
        # DDG returns a different HTML to obvious bots; pretend to be a
        # normal browser. This is the html.duckduckgo.com endpoint that
        # explicitly serves a no-JS layout.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        r = httpx_mod.get(url, headers=headers, timeout=timeout,
                          follow_redirects=True)
    except Exception:
        return []
    if r.status_code != 200 or not r.text:
        return []
    out: list[dict] = []
    for raw_url, raw_title, raw_snippet in _DDG_RESULT_RE.findall(r.text):
        title = _strip_html(raw_title)
        snippet = _strip_html(raw_snippet)
        # DDG wraps the real URL in a redirect like
        # //duckduckgo.com/l/?uddg=ENCODED_URL&...
        real_url = _unwrap_ddg_redirect(raw_url)
        if not real_url or not title:
            continue
        # Filter ad redirect URLs — they bloat the planning brief AND
        # pollute the final Sources block.
        if _looks_like_ad_url(real_url):
            continue
        out.append({
            "title": title,
            "url": real_url,
            "content": snippet,
            "source": "duckduckgo",
        })
        if len(out) >= max_results:
            break
    return out


def _strip_html(s: str) -> str:
    """Cheap HTML → plain text. Good enough for snippet rendering."""
    if not s:
        return ""
    # Drop tags, then unescape entities.
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _unwrap_ddg_redirect(raw_url: str) -> str:
    """DDG returns //duckduckgo.com/l/?uddg=<encoded> — unwrap that."""
    if not raw_url:
        return ""
    m = re.search(r"[?&]uddg=([^&]+)", raw_url)
    if m:
        return unquote(m.group(1))
    # Sometimes DDG returns the URL directly with a protocol-relative slash.
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url
