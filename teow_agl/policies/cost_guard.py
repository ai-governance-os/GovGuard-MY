"""Cost guard — daily LLM-call budget (Phase C of SANDBOX_PLAN).

The self-fix loop, task-tree decomposition, synthesizer and judge can
each multiply LLM calls per task. On free tiers that burns quota; on
paid planners (OpenAI / Anthropic) it burns money. This guard enforces
a simple, auditable budget: a date-keyed ledger of call counts checked
against limits from configs/cost_guard.json.

Two enforcement points:
  * Runtime — before a remote planner call (101C/identity/cache direct
    paths cost nothing and are never blocked). Over budget → a normal
    `model_error` PlannerRefusal with message "budget_exhausted:…" so
    102R's graceful-fallback message reaches the user and the self-fix
    loop does NOT retry (model_error is its bail-out condition).
  * ChatLLM — every chat backend call. Over budget → "" which every
    caller already handles gracefully (the adapter contract since day
    one: empty output means degrade, never crash).

Counts, not tokens: calls are the dominant cost driver here and counts
are exactly auditable; token estimation is noisy. Ledger resets when
the (local) date changes.

Env kill-switch: COST_GUARD_ENABLED=0 disables all checks.
Ledger override: TEOW_AGL_COST_LEDGER=<path> (used by tests and by
deployments that keep state elsewhere).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path

_ENV_KILL_SWITCH = "COST_GUARD_ENABLED"
_ENV_LEDGER = "TEOW_AGL_COST_LEDGER"

_LOCK = threading.Lock()


class CostGuard:
    def __init__(self, config: dict, ledger_path: str | Path) -> None:
        self.config = dict(config or {})
        self.ledger_path = Path(ledger_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def allow(self, kind: str) -> bool:
        """True when one more call of `kind` fits today's budget."""
        if not self._enabled():
            return True
        limit = (self.config.get("daily_limits") or {}).get(kind)
        if limit is None:
            return True
        with _LOCK:
            ledger = self._load()
        return int(ledger["counts"].get(kind, 0)) < int(limit)

    def record(self, kind: str, n: int = 1) -> None:
        """Count `n` calls of `kind` against today's ledger."""
        if not self._enabled():
            return
        with _LOCK:
            ledger = self._load()
            ledger["counts"][kind] = int(ledger["counts"].get(kind, 0)) + n
            self._save(ledger)

    def snapshot(self) -> dict:
        """Status block for /api/health and audits."""
        with _LOCK:
            ledger = self._load()
        limits = dict(self.config.get("daily_limits") or {})
        remaining = {
            k: max(0, int(v) - int(ledger["counts"].get(k, 0)))
            for k, v in limits.items()
        }
        return {
            "enabled": self._enabled(),
            "date": ledger["date"],
            "counts": dict(ledger["counts"]),
            "daily_limits": limits,
            "remaining": remaining,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _enabled(self) -> bool:
        if os.environ.get(_ENV_KILL_SWITCH, "1").lower() in (
                "0", "false", "no", "off"):
            return False
        return bool(self.config.get("enabled", False))

    def _load(self) -> dict:
        today = date.today().isoformat()
        try:
            with self.ledger_path.open("r", encoding="utf-8") as f:
                ledger = json.load(f)
        except (OSError, json.JSONDecodeError):
            ledger = {}
        if not isinstance(ledger, dict) or ledger.get("date") != today:
            ledger = {"date": today, "counts": {}}
        ledger.setdefault("counts", {})
        return ledger

    def _save(self, ledger: dict) -> None:
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("w", encoding="utf-8") as f:
                json.dump(ledger, f)
        except OSError:
            pass  # ledger write failure must never break a task


# ---------------------------------------------------------------------------
# Module-level default guard — used by ChatLLM, which has no config_dir.
# Reads the repo's configs/cost_guard.json; ledger path from env override
# or the repo's state/ directory. Runtime instances build their OWN
# CostGuard from their config_dir so isolated tests stay isolated.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_default_guard: CostGuard | None = None
_default_loaded = False


def default_guard() -> CostGuard | None:
    global _default_guard, _default_loaded
    if not _default_loaded:
        _default_loaded = True
        cfg_path = _REPO_ROOT / "configs" / "cost_guard.json"
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            cfg = None
        if cfg:
            ledger = os.environ.get(_ENV_LEDGER) or str(
                _REPO_ROOT / "state" / "cost_ledger.json")
            _default_guard = CostGuard(cfg, ledger)
    return _default_guard
