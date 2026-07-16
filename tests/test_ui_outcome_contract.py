from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"
STYLE_CSS = ROOT / "static" / "style.css"
HARNESS = ROOT / "tests" / "ui_outcome_harness.html"


def _run_node_assertions(body: str) -> None:
    browser_stub = r"""
global.window = { fetch: async () => ({ status: 200 }) };
global.document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementById: () => null,
  addEventListener: () => {},
  documentElement: { setAttribute: () => {}, getAttribute: () => "light" },
};
global.localStorage = { getItem: () => null, setItem: () => {} };
global.location = { reload: () => {} };
global.setInterval = () => 0;
"""
    script = browser_stub + f"\nconst ui = require({json.dumps(str(APP_JS))});\n" + body
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_safe_stop_excludes_cover_and_verifier_synthetic() -> None:
    _run_node_assertions(
        r"""
const assert = require("assert");
const task = {
  status: "done",
  final_route: "INFEASIBLE",
  decisions: [
    { action_id: "cover", route: "BLUE" },
    { action_id: "claim", route: "INFEASIBLE" },
  ],
  executions: [
    { action_id: "cover", status: "success", output_summary:
      "I prepared 3 governed Markdown drafts: `a.md`, `b.md`, `c.md`.", affected_resources: [] },
    { action_id: "verifier_synthetic", status: "failed", affected_resources: [] },
  ],
  verification: { enabled: true, pass: false, checks: [
    { name: "school.execution_completeness", pass: false, reason: "not_successful:a2" },
  ] },
};
const view = ui.buildOutcomeView(task);
assert.equal(view.safeStop, true);
assert.equal(view.needsRepair, false);
assert.equal(view.executions.length, 0);
assert.equal(view.files.length, 0);
assert.match(ui.governedPrimaryAnswer("", task, view), /cannot be produced reliably/i);
"""
    )


def test_mixed_safe_outputs_are_partial_and_needs_repair() -> None:
    _run_node_assertions(
        r"""
const assert = require("assert");
const task = {
  status: "done",
  final_route: "GREEN",
  response_pack: { input_governance: {
    decision: "RED",
    reasons: ["sensitive_data_disclosure"],
    blocked_request: "Broadcast individual results to the group",
    safe_transformations: ["Prepare an anonymous group notice", "Prepare one private notice per guardian"],
  }},
  decisions: [
    { action_id: "draft", route: "BLUE" },
    { action_id: "release", route: "GREEN", approval_required: true },
  ],
  executions: [
    { action_id: "draft", status: "success", affected_resources: ["notice.md"] },
    { action_id: "release", status: "success", affected_resources: [] },
    { action_id: "verifier_synthetic", status: "failed", affected_resources: [] },
  ],
  verification: { enabled: true, pass: false, checks: [], judge: {
    pass: false, issues: ["The notice exposes individual results."],
  }},
};
const view = ui.buildOutcomeView(task);
assert.equal(view.partial, true);
assert.equal(view.needsRepair, true);
assert.equal(view.greenState, "approved");
assert.deepEqual(view.files, ["notice.md"]);
assert.equal(view.executions.length, 2);
assert.deepEqual(view.issues, ["The notice exposes individual results."]);
"""
    )


def test_input_boundary_with_safe_transformation_is_partial_not_safe_stop() -> None:
    _run_node_assertions(
        r"""
const assert = require("assert");
const task = {
  status: "awaiting_clarification", final_route: "RED",
  response_pack: { input_governance: {
    decision: "RED", blocked_request: true,
    reasons: ["sensitive_data_disclosure"],
    safe_transformations: ["Prepare an anonymous notice instead"],
  }},
  decisions: [{ action_id: "unsafe", route: "RED" }],
  executions: [], verification: null,
};
const view = ui.buildOutcomeView(task);
assert.equal(view.partial, true);
assert.equal(view.safeStop, false);
assert.equal(view.quality, "PARTIAL_GOVERNED");
"""
    )


def test_verified_output_has_no_machine_issue_copy() -> None:
    _run_node_assertions(
        r"""
const assert = require("assert");
const task = {
  status: "done", final_route: "BLUE",
  decisions: [{ action_id: "draft", route: "BLUE" }],
  executions: [{ action_id: "draft", status: "success", affected_resources: ["notice.md"] }],
  verification: { enabled: true, pass: true,
    checks: [{ name: "school.fact_grounding", pass: true, reason: "ok" }] },
};
const view = ui.buildOutcomeView(task);
assert.equal(view.verified, true);
assert.equal(view.needsRepair, false);
assert.deepEqual(view.issues, []);
"""
    )


def test_css_contract_wraps_pipeline_at_phone_widths() -> None:
    css = STYLE_CSS.read_text(encoding="utf-8")
    assert "overflow-wrap: anywhere" in css
    assert "word-break: break-word" in css
    assert "@media (max-width: 560px)" in css
    assert "grid-template-columns: 36px minmax(0, 1fr)" in css
    assert ".app, .app.with-audit { grid-template-columns: minmax(0, 1fr); }" in css
    assert "width: min(360px, 100vw)" in css


def test_raw_verification_summary_is_not_used_for_visible_failure_copy() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    pipeline = source[source.index("function renderGovPipeline"):source.index("function renderWorkflowPanel")]
    assert '"FAILED: " + (ver.summary' not in pipeline
    assert "humanizeVerificationIssues" in source
    assert 'chip.textContent = "VERIFY-FAIL"' not in source


@pytest.mark.parametrize("width", [320, 480, 760])
def test_headless_chrome_has_no_horizontal_outcome_overflow(tmp_path: Path, width: int) -> None:
    if os.environ.get("GOVGUARD_BROWSER_UI_TESTS") != "1":
        pytest.skip("set GOVGUARD_BROWSER_UI_TESTS=1 for headless viewport checks")
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    browser = next((path for path in candidates if path.exists()), None)
    if browser is None:
        pytest.skip("Chrome/Edge not installed; static responsive contract still runs")
    profile = tmp_path / f"browser-{width}"
    window_width = max(520, width + 40)
    result = subprocess.run(
        [
            str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
            "--disable-background-networking", "--disable-extensions", "--disable-sync",
            "--virtual-time-budget=1000", "--allow-file-access-from-files",
            f"--user-data-dir={profile}", f"--window-size={window_width},900",
            "--dump-dom", f"{HARNESS.as_uri()}?testWidth={width}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r'data-overflow-count="(\d+)"', result.stdout)
    assert match, result.stdout
    assert int(match.group(1)) == 0, result.stdout
    assert f'data-viewport-width="{width}"' in result.stdout
