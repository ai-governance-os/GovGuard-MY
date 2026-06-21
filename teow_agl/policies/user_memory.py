"""
UserMemory — bounded curated notes about the user and the environment.

Two flat markdown files:
  USER.md    — what the agent has learned about the user (preferences,
               communication style, ongoing projects, etc.)
  MEMORY.md  — environment/working notes (tools, conventions, things
               learned about this workspace)

Both are bounded by character limit (1375 / 2200 by default) and use a
section-sign delimiter (§) between entries.

The LLM curates these via the memory tool — it decides what's worth
remembering. At task start, the runtime loads both files and injects
them into the planning_brief so 102 sees the agent's persistent notes.

Mid-task writes are durable on disk but the BRIEF for the current task
uses the snapshot loaded at task start — this is the same prefix-cache
discipline Hermes uses (frozen system prompt within a session).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path


ENTRY_DELIM = "\n§\n"


class UserMemory:
    """File-backed USER.md + MEMORY.md store."""

    def __init__(
        self,
        directory: str | Path,
        *,
        user_char_limit: int = 1375,
        env_char_limit: int = 2200,
    ) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.user_path = self.dir / "USER.md"
        self.env_path = self.dir / "MEMORY.md"
        self.user_char_limit = user_char_limit
        self.env_char_limit = env_char_limit
        self._lock = threading.Lock()

    # ---- read ----

    def snapshot(self) -> dict:
        """Return current text for both notebooks. Used to inject into brief."""
        return {
            "USER.md": self._read(self.user_path),
            "MEMORY.md": self._read(self.env_path),
        }

    def _read(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    # ---- write helpers ----

    def add(self, scope: str, entry: str) -> dict:
        """Append an entry. Returns {ok, error?, length}."""
        path, limit = self._target(scope)
        if path is None:
            return {"ok": False, "error": f"unknown_scope:{scope}"}
        clean = (entry or "").strip()
        if not clean:
            return {"ok": False, "error": "empty_entry"}
        # Inject security check on user-curated text — block prompt-injection
        # phrases that would be powerful because we inject the file content
        # straight into the system prompt next session.
        blocked = _scan_for_threats(clean)
        if blocked is not None:
            return {"ok": False, "error": f"blocked_by_safety:{blocked}"}
        with self._lock:
            existing = self._read(path)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            new_entry = f"[{now}] {clean}"
            new_content = (existing + ENTRY_DELIM + new_entry) if existing else new_entry
            if len(new_content) > limit:
                return {
                    "ok": False,
                    "error": f"would_exceed_char_limit:{limit}",
                    "current_length": len(existing),
                    "incoming_length": len(new_entry),
                }
            path.write_text(new_content, encoding="utf-8")
            return {"ok": True, "length": len(new_content)}

    def replace(self, scope: str, old_substring: str, new_text: str) -> dict:
        """Find first occurrence of old_substring and replace with new_text."""
        path, limit = self._target(scope)
        if path is None:
            return {"ok": False, "error": f"unknown_scope:{scope}"}
        if not old_substring:
            return {"ok": False, "error": "empty_old_substring"}
        blocked = _scan_for_threats(new_text or "")
        if blocked is not None:
            return {"ok": False, "error": f"blocked_by_safety:{blocked}"}
        with self._lock:
            existing = self._read(path)
            if old_substring not in existing:
                return {"ok": False, "error": "old_substring_not_found"}
            updated = existing.replace(old_substring, new_text or "", 1)
            if len(updated) > limit:
                return {"ok": False, "error": f"would_exceed_char_limit:{limit}"}
            path.write_text(updated, encoding="utf-8")
            return {"ok": True, "length": len(updated)}

    def remove(self, scope: str, substring: str) -> dict:
        """Delete first occurrence of substring (and any surrounding delim)."""
        path, _ = self._target(scope)
        if path is None:
            return {"ok": False, "error": f"unknown_scope:{scope}"}
        if not substring:
            return {"ok": False, "error": "empty_substring"}
        with self._lock:
            existing = self._read(path)
            if substring not in existing:
                return {"ok": False, "error": "substring_not_found"}
            updated = existing.replace(substring, "", 1)
            # collapse double delimiters
            updated = updated.replace(ENTRY_DELIM + ENTRY_DELIM, ENTRY_DELIM)
            updated = updated.strip("\n").strip(ENTRY_DELIM.strip())
            path.write_text(updated, encoding="utf-8")
            return {"ok": True, "length": len(updated)}

    def clear(self, scope: str) -> dict:
        path, _ = self._target(scope)
        if path is None:
            return {"ok": False, "error": f"unknown_scope:{scope}"}
        with self._lock:
            path.write_text("", encoding="utf-8")
        return {"ok": True, "length": 0}

    def _target(self, scope: str) -> tuple[Path | None, int]:
        s = (scope or "").lower()
        if s in ("user", "user.md"):
            return self.user_path, self.user_char_limit
        if s in ("memory", "memory.md", "env"):
            return self.env_path, self.env_char_limit
        return None, 0


# ---------------------------------------------------------------------------
# Security scanner — blocks prompt-injection phrases that would be dangerous
# because USER.md / MEMORY.md is injected into the system prompt each session.
# ---------------------------------------------------------------------------

import re

_THREAT_PATTERNS = (
    (r"ignore\s+(previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"you\s+are\s+now\s+", "role_hijack"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"hide\s+(this|that)\s+from\s+(the\s+)?user", "deception_hide"),
    # Exfiltration prep via memory
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.ssh)", "read_secrets"),
)


def _scan_for_threats(content: str) -> str | None:
    if not content:
        return None
    for pattern, label in _THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return label
    return None
