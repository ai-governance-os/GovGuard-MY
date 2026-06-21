"""
SQLite FTS5 index over past task traces.

Goal: let the agent answer "have we discussed X before?" / "show me past
tasks that involved Y" by full-text-searching every prior task's
raw_goal and the salient output_summary of each execution.

Design choices:

  * SQLite FTS5 ships with cpython's stdlib `sqlite3`. Zero extra
    dependency. Verified at runtime via _has_fts5(); on the very rare
    build without it we degrade to no-op (queries return []).
  * Append-only: each task gets one row inserted once. The (task_id)
    column has a UNIQUE constraint so re-indexing is naturally
    idempotent (an INSERT OR IGNORE replays cleanly).
  * Indexing is triggered by the runtime at task-finish, NOT on a
    background thread. Keeps the data model simple — a query at task
    N+1 sees everything through task N.
  * Privacy: tasks with envelope.metadata["no_index"] = True are
    skipped entirely. They never enter the index.
  * Bloat cap: the index keeps a hard upper bound on `max_indexed_tasks`
    (configurable, default 10000). Oldest tasks are evicted FIFO when
    the cap is exceeded. Trace JSONLs on disk are untouched — only
    the searchable index trims.

Schema (kept intentionally minimal so it's easy to evolve):

    -- The FTS5 virtual table holds the tokenized text and offers BM25.
    CREATE VIRTUAL TABLE sessions_fts USING fts5(
        task_id  UNINDEXED,    -- echoed back; not part of the search
        raw_goal,              -- the user's original prompt
        body,                  -- concatenated output_summary of execs
        tokenize = 'porter unicode61'
    );

    -- A regular table for non-search metadata (timestamps, route, etc).
    CREATE TABLE sessions_meta (
        task_id      TEXT PRIMARY KEY,
        started_at   TEXT,
        finished_at  TEXT,
        final_route  TEXT,
        rowid_fts    INTEGER
    );
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any


_DEFAULT_MAX_INDEXED_TASKS = 10_000


def _has_fts5(conn: sqlite3.Connection) -> bool:
    """Detect FTS5 availability. Most cpython builds since 3.11 ship
    with it; some musl / minimal builds don't."""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)"
        )
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


class SessionIndex:
    """File-backed FTS5 index over task summaries.

    Thread-safe (single internal lock around all writes). Read paths
    open short-lived connections so concurrent queries don't block
    each other.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_indexed_tasks: int = _DEFAULT_MAX_INDEXED_TASKS,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Minimum of 1 — production defaults sit at 10k, but tests pass
        # values like 3 to exercise the eviction path, so we don't clamp
        # to a high floor.
        self.max_indexed_tasks = max(1, int(max_indexed_tasks))
        self._lock = threading.Lock()
        self.available = self._init_schema()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------
    def _init_schema(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            if not _has_fts5(conn):
                return False
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                    task_id   UNINDEXED,
                    raw_goal,
                    body,
                    tokenize = 'porter unicode61'
                );

                CREATE TABLE IF NOT EXISTS sessions_meta (
                    task_id      TEXT PRIMARY KEY,
                    started_at   TEXT,
                    finished_at  TEXT,
                    final_route  TEXT,
                    rowid_fts    INTEGER
                );
                """
            )
            conn.commit()
        return True

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------
    def index_task(
        self,
        *,
        task_id: str,
        raw_goal: str,
        body: str,
        started_at: str = "",
        finished_at: str = "",
        final_route: str = "",
        no_index: bool = False,
    ) -> dict:
        """Insert (or re-insert) one task into the index.

        Returns {"ok": bool, "indexed": bool, "reason": str}.

        Never raises — indexing failures are non-fatal; the agent
        pipeline must keep running.
        """
        if not self.available:
            return {"ok": False, "indexed": False, "reason": "fts5_unavailable"}
        if no_index:
            return {"ok": True, "indexed": False, "reason": "opted_out_no_index"}
        if not task_id or not (raw_goal or body):
            return {"ok": True, "indexed": False, "reason": "nothing_to_index"}

        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                # Idempotent: if this task is already indexed, replace it.
                existing = conn.execute(
                    "SELECT rowid_fts FROM sessions_meta WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if existing is not None:
                    conn.execute(
                        "DELETE FROM sessions_fts WHERE rowid = ?",
                        (existing[0],),
                    )
                cur = conn.execute(
                    "INSERT INTO sessions_fts (task_id, raw_goal, body) "
                    "VALUES (?, ?, ?)",
                    (task_id, raw_goal or "", body or ""),
                )
                rowid_fts = cur.lastrowid
                conn.execute(
                    "INSERT INTO sessions_meta "
                    "(task_id, started_at, finished_at, final_route, rowid_fts) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(task_id) DO UPDATE SET "
                    "  started_at = excluded.started_at, "
                    "  finished_at = excluded.finished_at, "
                    "  final_route = excluded.final_route, "
                    "  rowid_fts = excluded.rowid_fts",
                    (task_id, started_at, finished_at, final_route, rowid_fts),
                )
                self._enforce_bloat_cap(conn)
                conn.commit()
        except Exception as exc:
            return {"ok": False, "indexed": False,
                    "reason": f"sqlite_error:{exc}"}
        return {"ok": True, "indexed": True, "reason": "ok"}

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------
    def query(self, q: str, *, top_k: int = 5) -> list[dict]:
        """Return ranked matches via BM25 (FTS5's default ranking).

        Each hit: {task_id, raw_goal, snippet, score, started_at,
                   finished_at, final_route}.

        BM25 is exposed as a negative score in FTS5 — we negate it back
        to positive so "higher is better" matches caller expectations.
        """
        if not self.available or not q or not q.strip():
            return []
        safe_q = _sanitize_query(q)
        if not safe_q:
            return []
        out: list[dict] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT s.task_id, s.raw_goal, "
                    "       snippet(sessions_fts, 2, '[', ']', '...', 32) AS snippet, "
                    "       -bm25(sessions_fts) AS score, "
                    "       m.started_at, m.finished_at, m.final_route "
                    "FROM sessions_fts AS s "
                    "JOIN sessions_meta AS m ON m.task_id = s.task_id "
                    "WHERE sessions_fts MATCH ? "
                    "ORDER BY score DESC "
                    "LIMIT ?",
                    (safe_q, max(1, int(top_k))),
                ).fetchall()
                for r in rows:
                    out.append({
                        "task_id": r["task_id"],
                        "raw_goal": (r["raw_goal"] or "")[:300],
                        "snippet": r["snippet"] or "",
                        "score": round(float(r["score"] or 0.0), 3),
                        "started_at": r["started_at"] or "",
                        "finished_at": r["finished_at"] or "",
                        "final_route": r["final_route"] or "",
                    })
        except Exception:
            return []
        return out

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM sessions_meta"
                ).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def _enforce_bloat_cap(self, conn: sqlite3.Connection) -> None:
        """If row count exceeds max_indexed_tasks, evict oldest by
        started_at. Caller holds the lock + open connection."""
        n = conn.execute("SELECT COUNT(*) FROM sessions_meta").fetchone()[0]
        if n <= self.max_indexed_tasks:
            return
        to_evict = n - self.max_indexed_tasks
        rows = conn.execute(
            "SELECT task_id, rowid_fts FROM sessions_meta "
            "ORDER BY started_at ASC LIMIT ?",
            (to_evict,),
        ).fetchall()
        for task_id, rowid_fts in rows:
            conn.execute("DELETE FROM sessions_fts WHERE rowid = ?",
                         (rowid_fts,))
            conn.execute("DELETE FROM sessions_meta WHERE task_id = ?",
                         (task_id,))


# ---------------------------------------------------------------------------
# Query sanitization
# ---------------------------------------------------------------------------
# FTS5 is picky about its MATCH grammar: bare special chars like ", :, *,
# and parens can throw syntax errors. We strip them down to a safe subset
# and OR the remaining tokens together so common-language queries
# ("anthropic latest news") just work.
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[A-Za-z0-9_一-鿿]+")


def _sanitize_query(q: str) -> str:
    if not q:
        return ""
    tokens = _TOKEN_RE.findall(q)
    if not tokens:
        return ""
    # Cap to first 16 tokens so a runaway query doesn't melt the planner.
    tokens = tokens[:16]
    # Quote each token so FTS5 treats it literally — protects against
    # accidental column-filter syntax (a token "raw_goal:foo" would
    # otherwise be parsed as a column-prefix and crash).
    return " OR ".join(f'"{t}"' for t in tokens)


# ---------------------------------------------------------------------------
# Helper used by runtime / server: build the "body" text we index from
# a task's executions. Keeps the index lean — we don't shove every event
# in, just the user-visible output_summary, which is what someone would
# search for ("did I ever ask about VIOLET routing?").
# ---------------------------------------------------------------------------
def build_indexable_body(executions: list[Any], *, max_chars: int = 4000) -> str:
    """Return a single text blob suitable for FTS5 indexing.

    `executions` is a list of either ExecutionResult instances or plain
    dicts (the server stores model_dump() form). We accept both so
    callers don't have to convert.
    """
    parts: list[str] = []
    for ex in executions or []:
        if isinstance(ex, dict):
            summary = ex.get("output_summary") or ""
            tool = ex.get("tool") or ""
        else:
            summary = getattr(ex, "output_summary", "") or ""
            tool = getattr(ex, "tool", "") or ""
        if not summary:
            continue
        # Skip pure file-marker outputs (no useful content for search).
        # Matches docx/pptx/xlsx_written:foo.docx and image_saved:foo.png
        # and image_placeholder_saved:foo.png — none of which have body
        # text worth FTS5-indexing.
        if re.match(
            r"^(docx|pptx|xlsx|image)_(written|saved|placeholder_saved):",
            summary,
        ):
            continue
        prefix = f"[{tool}] " if tool else ""
        parts.append(prefix + str(summary))
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[: max_chars - 1] + "…"
    return blob
