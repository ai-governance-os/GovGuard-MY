"""
Server-side persistence — JSONL files under ./state/.

We keep this deliberately simple (no DB, no migrations) because the
runtime's source of truth lives in:
  * configs/learned_contextual_policy.json (104 patches, after approval)
  * configs/model_behavior_profile.json (LLM stats)
  * traces/trace_YYYYMMDD.jsonl (full audit trail)

The state files here are just for UI continuity:
  state/tasks.jsonl     — one row per finished/in-progress task summary
  state/proposals.jsonl — one row per proposal (status: proposed/approved/rejected)

Append-only; on read we replay.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonlStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def replay(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def latest_by_id(self, id_field: str) -> dict[str, dict]:
        """Replay file and return last-write-wins map keyed by id_field."""
        out: dict[str, dict] = {}
        for rec in self.replay():
            key = rec.get(id_field)
            if key:
                out[key] = rec
        return out
