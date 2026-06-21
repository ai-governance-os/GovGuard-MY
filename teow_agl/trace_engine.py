"""Append-only JSONL trace."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from .models import TraceEvent


_REDACT_KEYS = ("password", "token", "api_key", "secret", "authorization", "credential")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in _REDACT_KEYS):
                out[k] = "<REDACTED>"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def hash_text(text: str) -> str:
    if not text:
        return ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class TraceEngine:
    def __init__(self, trace_dir: str | Path = "./traces") -> None:
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self) -> Path:
        return self.trace_dir / f"trace_{date.today().strftime('%Y%m%d')}.jsonl"

    def emit(
        self,
        *,
        session_id: str,
        task_id: str,
        module: str,
        event_type: str,
        summary: str = "",
        details: dict | None = None,
        input_text: str = "",
        output_text: str = "",
    ) -> TraceEvent:
        ev = TraceEvent(
            session_id=session_id,
            task_id=task_id,
            module=module,
            event_type=event_type,
            summary=summary,
            details=_redact(details or {}),
            input_hash=hash_text(input_text),
            output_hash=hash_text(output_text),
        )
        # always ensure dir still exists (defensive: a destructive op
        # may have removed it; we want trace itself to never crash)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        with self._file_path().open("a", encoding="utf-8") as f:
            f.write(ev.model_dump_json() + "\n")
        return ev

    def read_all(self) -> list[dict]:
        path = self._file_path()
        if not path.exists():
            return []
        out = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out
