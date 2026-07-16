"""Browser-level regression tests for readable governance reasons.

The UI must show the full verification explanation.  Long English prose,
Chinese text and unbroken audit identifiers may wrap, but they must never
escape, disappear behind hidden overflow, or be replaced by an ellipsis.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = ROOT / "static" / "style.css"
HARNESS = ROOT / "tests" / "ui_governance_overflow_harness.html"
VIEWPORTS = (1920, 768, 498, 375)


def _browser() -> Path | None:
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    return next((path for path in candidates if path.exists()), None)


def _attribute(dom: str, name: str) -> int:
    match = re.search(rf'data-{re.escape(name)}="(\d+)"', dom)
    assert match, f"missing data-{name} in rendered harness\n{dom}"
    return int(match.group(1))


def test_pipeline_css_preserves_complete_reasons() -> None:
    css = STYLE_CSS.read_text(encoding="utf-8")
    start = css.index(".gov-pipeline {")
    end = css.index("/* Workflow Autonomy panel", start)
    pipeline_css = css[start:end]

    assert "overflow-wrap: anywhere" in pipeline_css
    assert "word-break: break-word" in pipeline_css
    assert ".gov-pipeline .gp-step > * { min-width: 0; max-width: 100%; }" in pipeline_css
    assert "text-overflow: ellipsis" not in pipeline_css
    assert "-webkit-line-clamp" not in pipeline_css
    assert "overflow: hidden" not in pipeline_css


@pytest.mark.parametrize("width", VIEWPORTS)
def test_governance_reasons_fit_and_remain_readable(tmp_path: Path, width: int) -> None:
    if os.environ.get("GOVGUARD_BROWSER_UI_TESTS") != "1":
        pytest.skip("set GOVGUARD_BROWSER_UI_TESTS=1 for headless viewport checks")
    browser = _browser()
    if browser is None:
        pytest.skip("Chrome/Edge not installed; static CSS contract still runs")

    profile = tmp_path / f"profile-{width}"
    result = subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-sync",
            "--allow-file-access-from-files",
            "--virtual-time-budget=1500",
            f"--user-data-dir={profile}",
            f"--window-size={max(520, width + 40)},1200",
            "--dump-dom",
            f"{HARNESS.as_uri()}?testWidth={width}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'data-ready="true"' in result.stdout
    assert f'data-viewport-width="{width}"' in result.stdout
    assert _attribute(result.stdout, "overflow-count") == 0
    assert _attribute(result.stdout, "clipped-count") == 0
    assert _attribute(result.stdout, "missing-count") == 0
    assert _attribute(result.stdout, "wrapped-count") >= 2
