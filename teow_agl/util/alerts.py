"""Operator alerting (Phase C) — fire-and-forget webhook notification.

When ALERT_WEBHOOK_URL is set, `send_alert` POSTs a small JSON payload
to it. Works with anything that accepts a JSON POST: ntfy.sh topics,
Slack/Discord incoming webhooks, a phone-push relay, or a one-line
Flask receiver. When unset (the default, and in all tests), it is a
no-op returning False.

Contract: NEVER raises, NEVER blocks the caller for more than the
timeout. An alerting failure must not break the task that triggered it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

_ENV_URL = "ALERT_WEBHOOK_URL"


def send_alert(message: str, *, details: dict | None = None,
               kind: str = "error", timeout: int = 10) -> bool:
    """POST an alert to the configured webhook. Returns True only when
    the webhook accepted it (2xx)."""
    url = (os.environ.get(_ENV_URL) or "").strip()
    if not url:
        return False
    try:
        import httpx  # type: ignore
    except ImportError:
        return False
    payload = {
        "source": "teow-agl",
        "kind": kind,
        "text": f"[TEOW-AGL][{kind}] {message}",
        "details": details or {},
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        return 200 <= r.status_code < 300
    except Exception:
        return False
