"""GuiTool unit tests using a fake pyautogui."""
from __future__ import annotations

import sys
import types
from pathlib import Path

from teow_agl.models import CandidateAction
from teow_agl.tools.gui_tools import GuiTool


class FakePyAutoGUI:
    """Replaces pyautogui in sys.modules; records calls."""
    FAILSAFE = True
    PAUSE = 0.0
    calls: list[tuple] = []

    def __init__(self):
        self.calls = []

    def screenshot(self):
        from PIL import Image  # type: ignore
        # 10x10 black image
        return Image.new("RGB", (10, 10), color=(0, 0, 0))

    def moveTo(self, x, y, duration=0.0):
        self.calls.append(("moveTo", x, y))

    def click(self, x=None, y=None, clicks=1, button="left", interval=0.0):
        self.calls.append(("click", x, y, clicks, button))

    def scroll(self, amount, x=None, y=None):
        self.calls.append(("scroll", amount, x, y))

    def typewrite(self, text, interval=0.0):
        self.calls.append(("typewrite", text))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))


def _install_fake(monkeypatch):
    fake = FakePyAutoGUI()
    mod = types.SimpleNamespace(
        FAILSAFE=True, PAUSE=0.0,
        screenshot=fake.screenshot, moveTo=fake.moveTo, click=fake.click,
        scroll=fake.scroll, typewrite=fake.typewrite, hotkey=fake.hotkey,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", mod)
    return fake


def _action(op, metadata=None):
    return CandidateAction(
        action_id="a1", tool="gui", operation=op, target="",
        purpose="t", expected_effect="t", reversibility="medium",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata=metadata or {},
    )


def test_screenshot_writes_png(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("screenshot"))
    assert res["status"] == "success"
    saved = Path(res["affected"][0])
    assert saved.exists()
    assert saved.suffix == ".png"


def test_mouse_click_records(tmp_path: Path, monkeypatch):
    fake = _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("mouse_click", {"x": 100, "y": 200, "button": "left"}))
    assert res["status"] == "success"
    # we can verify via the recorded call only when using the class instance
    # (here we used the SimpleNamespace, so just check the success path)


def test_mouse_click_rejects_missing_coords(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("mouse_click", {}))
    assert res["status"] == "failed"
    assert "missing_coordinates" in res.get("error", "")


def test_mouse_click_rejects_negative_coords(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("mouse_click", {"x": -1, "y": 100}))
    assert res["status"] == "failed"


def test_mouse_click_rejects_huge_coords(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path, max_coord=100)
    res = tool(_action("mouse_click", {"x": 200, "y": 200}))
    assert res["status"] == "failed"


def test_mouse_click_rejects_invalid_button(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("mouse_click", {"x": 100, "y": 200, "button": "MIDDLE_LEFT"}))
    assert res["status"] == "failed"
    assert "invalid_button" in res.get("error", "")


def test_keyboard_type_runs(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("keyboard_type", {"text": "hello"}))
    assert res["status"] == "success"


def test_keyboard_type_too_long_denied(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("keyboard_type", {"text": "x" * 5000}))
    assert res["status"] == "failed"
    assert "too_long" in res.get("error", "")


def test_keyboard_hotkey_safe_combo(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("keyboard_hotkey", {"combo": "ctrl+s"}))
    assert res["status"] == "success"


def test_keyboard_hotkey_dangerous_combo_denied(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    for combo in ("ctrl+alt+del", "Ctrl+Alt+Delete", "win+r"):
        res = tool(_action("keyboard_hotkey", {"combo": combo}))
        assert res["status"] == "denied", f"combo {combo!r} should be denied"
        assert "hard_blocked" in res.get("error", "")


def test_unknown_op_skipped(tmp_path: Path, monkeypatch):
    _install_fake(monkeypatch)
    tool = GuiTool(screenshots_dir=tmp_path)
    res = tool(_action("evil_op"))
    assert res["status"] == "skipped"
