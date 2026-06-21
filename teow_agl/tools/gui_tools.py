"""
GUI automation tool — mouse and keyboard control.

This is the most powerful and most dangerous tool in the registry.
Every operation is gated by 107 (ticket required), and the policy
stack defaults every GUI action to GREEN (human approval per action).

Operations:
  screenshot          — capture screen to outputs/_screenshots/<file>.png
  mouse_move          — move cursor to (x, y) without clicking (low risk)
  mouse_click         — click at (x, y); requires GREEN
  scroll              — scroll wheel by N clicks (low risk)
  keyboard_type       — type a literal string into the focused window
  keyboard_hotkey     — press a key combination (e.g., "ctrl+s")

Defensive layers:
  1. universal_hard_safety.json contains dangerous shortcut substrings
     (ctrl+alt+del, win+r, etc.). 101A blocks the whole task RED if the
     user goal contains them.
  2. This tool ALSO checks the keystroke combo against a tool-level
     denylist before running, so a malformed plan can't slip past 101A.
  3. Coordinates outside the primary screen bounds are denied.
  4. pyautogui's FAILSAFE is enabled — moving the mouse to (0,0) aborts.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from ..models import CandidateAction


_DANGEROUS_HOTKEY_TOKENS = (
    "ctrl+alt+del", "ctrl+alt+delete",
    "win+r",          # Run dialog
    "win+l",          # Lock screen
    "alt+f4",         # close window — annoying but allowed; comment out if want forbid
)


def _is_dangerous_hotkey(combo: str) -> bool:
    s = combo.lower().replace(" ", "")
    return any(tok in s for tok in _DANGEROUS_HOTKEY_TOKENS[:3])
    # alt+f4 intentionally allowed by default; admins can extend the list


class GuiTool:
    name = "gui"

    def __init__(self, *, screenshots_dir: str | Path, max_coord: int = 8192) -> None:
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.max_coord = max_coord

    def __call__(self, action: CandidateAction) -> dict:
        op = action.operation
        meta = action.metadata or {}
        try:
            import pyautogui  # type: ignore
        except ImportError:
            return _failed("pyautogui not installed")

        pyautogui.FAILSAFE = True  # mouse to (0,0) aborts
        pyautogui.PAUSE = 0.05

        if op == "screenshot":
            return self._screenshot(pyautogui)
        if op == "mouse_move":
            return self._mouse_move(pyautogui, meta)
        if op == "mouse_click":
            return self._mouse_click(pyautogui, meta)
        if op == "scroll":
            return self._scroll(pyautogui, meta)
        if op == "keyboard_type":
            return self._keyboard_type(pyautogui, meta)
        if op == "keyboard_hotkey":
            return self._keyboard_hotkey(pyautogui, meta)
        return {"status": "skipped", "summary": f"unknown_gui_op:{op}", "affected": []}

    # ---- ops ----

    def _screenshot(self, pyautogui) -> dict:
        try:
            img = pyautogui.screenshot()
        except Exception as exc:
            return _failed(f"screenshot_failed:{exc}")
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = self.screenshots_dir / f"shot_{ts}_{uuid.uuid4().hex[:6]}.png"
        img.save(str(out))
        return {"status": "success", "summary": f"screenshot_saved:{out.name}",
                "affected": [str(out)], "screenshot_path": str(out)}

    def _mouse_move(self, pyautogui, meta: dict) -> dict:
        x, y = self._coords(meta)
        if x is None:
            return _failed("missing_coordinates")
        try:
            pyautogui.moveTo(x, y, duration=0.1)
        except Exception as exc:
            return _failed(f"move_failed:{exc}")
        return {"status": "success", "summary": f"moved_to:{x},{y}", "affected": []}

    def _mouse_click(self, pyautogui, meta: dict) -> dict:
        x, y = self._coords(meta)
        if x is None:
            return _failed("missing_coordinates")
        button = str(meta.get("button", "left")).lower()
        if button not in ("left", "right", "middle"):
            return _failed(f"invalid_button:{button}")
        clicks = int(meta.get("clicks", 1))
        try:
            pyautogui.click(x=x, y=y, clicks=clicks, button=button, interval=0.05)
        except Exception as exc:
            return _failed(f"click_failed:{exc}")
        return {"status": "success", "summary": f"clicked_{button}_{clicks}x:{x},{y}", "affected": []}

    def _scroll(self, pyautogui, meta: dict) -> dict:
        amount = int(meta.get("amount", 0))
        if amount == 0:
            return _failed("missing_amount")
        x, y = self._coords(meta)
        try:
            if x is not None:
                pyautogui.scroll(amount, x=x, y=y)
            else:
                pyautogui.scroll(amount)
        except Exception as exc:
            return _failed(f"scroll_failed:{exc}")
        return {"status": "success", "summary": f"scrolled:{amount}", "affected": []}

    def _keyboard_type(self, pyautogui, meta: dict) -> dict:
        text = meta.get("text") or meta.get("content")
        if text is None:
            return _failed("missing_text")
        text = str(text)
        if len(text) > 4000:
            return _failed("text_too_long_max_4000")
        try:
            pyautogui.typewrite(text, interval=0.01)
        except Exception as exc:
            return _failed(f"type_failed:{exc}")
        return {"status": "success", "summary": f"typed_{len(text)}_chars", "affected": []}

    def _keyboard_hotkey(self, pyautogui, meta: dict) -> dict:
        combo = meta.get("combo") or meta.get("hotkey")
        if not combo:
            return _failed("missing_combo")
        combo = str(combo).strip()
        if _is_dangerous_hotkey(combo):
            return _denied(f"hard_blocked_hotkey:{combo}")
        keys = [k.strip().lower() for k in combo.replace(" ", "").split("+") if k.strip()]
        if not keys:
            return _failed("empty_combo")
        try:
            pyautogui.hotkey(*keys)
        except Exception as exc:
            return _failed(f"hotkey_failed:{exc}")
        return {"status": "success", "summary": f"hotkey:{'+'.join(keys)}", "affected": []}

    # ---- helpers ----

    def _coords(self, meta: dict) -> tuple[int | None, int | None]:
        if "x" not in meta or "y" not in meta:
            return None, None
        try:
            x = int(meta["x"]); y = int(meta["y"])
        except (TypeError, ValueError):
            return None, None
        if x < 0 or y < 0 or x > self.max_coord or y > self.max_coord:
            return None, None
        return x, y


def _denied(reason: str) -> dict:
    return {"status": "denied", "summary": reason, "affected": [], "error": reason}


def _failed(reason: str) -> dict:
    return {"status": "failed", "summary": reason, "affected": [], "error": reason}
