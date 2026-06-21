"""
Subject confidence — per-task-category historical success tracking.

Simplified version of Phase 4's subject_confidence dimension. The full
Phase 4 design tracks 8 cognitive dimensions per task class. This Tier-1
version tracks just one thing: did this category of task succeed when
we did it before?

Score formula uses Laplace smoothing so new categories start at a
neutral 0.5 and rare events don't swing the score wildly:

    score = (successes + smoothing_alpha) /
            (successes + failures + rejections + smoothing_alpha * 2)

A category needs `min_observations` events before its score is considered
"confident" (used for EXECUTE_DIRECT gating, etc.).

The ledger is an append-only JSONL — every outcome is one line. View
aggregates by replaying.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

Outcome = Literal[
    "success",          # execution succeeded
    "failure",          # execution failed
    "human_rejected",   # human said no at 105
    "infeasible",       # marked INFEASIBLE
]


class SubjectConfidence:
    """Append-only outcome ledger keyed by task_category."""

    def __init__(
        self,
        path: str | Path,
        *,
        smoothing_alpha: float = 1.0,
        min_observations_for_confident: int = 3,
        confident_threshold: float = 0.7,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.smoothing_alpha = smoothing_alpha
        self.min_observations_for_confident = min_observations_for_confident
        self.confident_threshold = confident_threshold
        self._lock = threading.Lock()
        # Cached aggregates: category -> {successes, failures, rejections, infeasibles, total}
        self._cache: dict[str, dict[str, int]] | None = None

    # ---- write ----

    def record(self, *, category: str, outcome: Outcome, task_id: str = "") -> None:
        if not category:
            return
        entry = {
            "category": category,
            "outcome": outcome,
            "task_id": task_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._cache = None  # invalidate

    # ---- read ----

    def _aggregate(self) -> dict[str, dict[str, int]]:
        with self._lock:
            if self._cache is not None:
                return self._cache
            counts: dict[str, dict[str, int]] = {}
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        cat = rec.get("category")
                        out = rec.get("outcome")
                        if not cat or not out:
                            continue
                        slot = counts.setdefault(
                            cat,
                            {"successes": 0, "failures": 0, "rejections": 0,
                             "infeasibles": 0, "total": 0},
                        )
                        slot["total"] += 1
                        if out == "success":
                            slot["successes"] += 1
                        elif out == "failure":
                            slot["failures"] += 1
                        elif out == "human_rejected":
                            slot["rejections"] += 1
                        elif out == "infeasible":
                            slot["infeasibles"] += 1
            self._cache = counts
            return counts

    def score(self, category: str) -> float:
        """Return Laplace-smoothed success score in [0, 1]."""
        agg = self._aggregate()
        slot = agg.get(category)
        if not slot:
            return 0.5  # neutral prior for unknown categories
        a = self.smoothing_alpha
        s = slot["successes"]
        denom = s + slot["failures"] + slot["rejections"] + 2 * a
        if denom <= 0:
            return 0.5
        return (s + a) / denom

    def observations(self, category: str) -> int:
        agg = self._aggregate()
        slot = agg.get(category)
        return slot["total"] if slot else 0

    def is_confident(self, category: str) -> bool:
        return (
            self.observations(category) >= self.min_observations_for_confident
            and self.score(category) >= self.confident_threshold
        )

    def snapshot(self) -> dict[str, dict]:
        """Return per-category aggregate + score for /api/stats."""
        agg = self._aggregate()
        out: dict[str, dict] = {}
        for cat, slot in agg.items():
            out[cat] = {
                **slot,
                "score": round(self.score(cat), 3),
                "confident": self.is_confident(cat),
            }
        return out

    # ---- Phase-1A additions (used by module_109b_skill_distiller) ----

    def aggregate_for(self, category: str) -> dict[str, int]:
        """Return the aggregate slot for a single category, or an empty
        dict-of-zeros if we haven't seen the category yet.

        Convenience for the Skill Distiller's trigger checks (cumulative
        successes, success rate). The empty-but-typed return keeps
        downstream code from sprinkling `or {}` and `.get(..., 0)` calls.
        """
        slot = self._aggregate().get(category)
        if slot is None:
            return {"successes": 0, "failures": 0, "rejections": 0,
                    "infeasibles": 0, "total": 0}
        return dict(slot)  # defensive copy — callers must not mutate cache

    def recent_outcomes(self, category: str, n: int = 5) -> list[Outcome]:
        """Replay the JSONL tail and return the last `n` outcomes for
        `category` (oldest → newest order).

        Used by Skill Distiller's "no recent failure" check (Phase 1A,
        prevents proposing a skill from a category that's currently in a
        failure streak even though aggregate looks fine). The full ledger
        read is cheap for typical sizes (< 1MB); if the file grows huge
        a future optimisation can walk the file backwards.

        Returns [] if the category has never been seen.
        """
        if not self.path.exists() or n <= 0:
            return []
        results: list[Outcome] = []
        # Read the whole file; for typical sizes (< 1MB) the cost is
        # dominated by JSON parsing, not I/O. Worth revisiting only if
        # the ledger ever crosses ~10MB.
        with self._lock:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("category") == category:
                        out = rec.get("outcome")
                        if out:
                            results.append(out)
        return results[-n:] if len(results) > n else results
