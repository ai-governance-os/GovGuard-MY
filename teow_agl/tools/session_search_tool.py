"""
Session search tool — query the FTS5 index of past task summaries.

LLM action shape:
    {
      "tool": "session_search",
      "operation": "query",
      "metadata": {
          "query":   "..."  (required)
          "top_k":   5       (optional)
      }
    }

The runtime ALSO injects results pre-planner whenever the user's
prompt contains episodic-recall phrases ("remember when", "上次",
"之前", etc.). In that case the LLM doesn't need to plan a
session_search action itself — the brief already contains
`prior_sessions`. The standalone tool is for the rarer "explicit
search" case (and for the future agent-loop path).
"""
from __future__ import annotations

from typing import Any

from ..models import CandidateAction
from ..util.fts5_indexer import SessionIndex


class SessionSearchTool:
    name = "session_search"

    def __init__(self, index: SessionIndex) -> None:
        self.index = index

    def __call__(self, action: CandidateAction) -> dict[str, Any]:
        op = (action.operation or "").lower()
        meta = action.metadata or {}

        if op in ("query", "search", ""):
            q = str(meta.get("query")
                    or meta.get("q")
                    or meta.get("prompt") or "").strip()
            if not q:
                return {"status": "failed",
                        "summary": "session_search_empty_query",
                        "error": "missing metadata.query",
                        "affected": []}
            top_k = int(meta.get("top_k", 5))
            hits = self.index.query(q, top_k=top_k)
            if not hits:
                return {"status": "success",
                        "summary": f"session_search_no_matches:'{q[:60]}'",
                        "affected": [], "hits": []}
            # Compact bullet rendering for output_summary so the chat
            # path can surface it as readable text. Full structured
            # hits also returned for downstream consumers.
            lines = [f"session_search for: {q}"]
            for i, h in enumerate(hits, 1):
                lines.append(
                    f"[{i}] {h.get('raw_goal','')[:120]} "
                    f"(task_id={h.get('task_id','')}, "
                    f"route={h.get('final_route','')}, "
                    f"score={h.get('score')})"
                )
                snip = (h.get("snippet") or "").strip()
                if snip:
                    lines.append(f"    …{snip}…")
            return {"status": "success",
                    "summary": "\n".join(lines),
                    "affected": [],
                    "hits": hits}

        if op in ("count", "stats"):
            n = self.index.count()
            return {"status": "success",
                    "summary": f"session_index_count:{n}",
                    "affected": [], "count": n}

        return {"status": "failed",
                "summary": f"unknown_session_search_op:{op}",
                "error": f"unknown operation '{op}'; valid: query, count",
                "affected": []}
