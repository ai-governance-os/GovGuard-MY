"""ImageGenTool: placeholder PNG when no API key, real path when key present."""
from __future__ import annotations

import os
from pathlib import Path

from teow_agl.adapters.smart_mock_planner import SmartMockPlanner
from teow_agl.models import CandidateAction
from teow_agl.tools.image_tool import ImageGenTool


def _action(operation: str, metadata: dict) -> CandidateAction:
    return CandidateAction(
        action_id="a1", tool="image_gen", operation=operation, target="",
        purpose="t", expected_effect="t", reversibility="high",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata=metadata,
    )


def test_offline_generates_placeholder_png(tmp_path: Path, monkeypatch):
    # Explicit placeholder mode: deterministic, no network. (Default is now
    # IMAGE_PROVIDER=pollinations, which would touch the network — bad for
    # an "offline" test.)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    tool = ImageGenTool(images_dir=tmp_path, workspace_roots=[str(tmp_path)])
    res = tool(_action("generate_image", {"prompt": "a red apple on a wooden table"}))
    assert res["status"] == "success"
    assert res["image_source"] == "placeholder"
    saved = Path(res["affected"][0])
    assert saved.exists()
    assert saved.suffix == ".png"
    # PNG magic bytes
    assert saved.read_bytes()[:8].startswith(b"\x89PNG\r\n\x1a\n")


def test_missing_prompt_fails(tmp_path: Path):
    tool = ImageGenTool(images_dir=tmp_path)
    res = tool(_action("generate_image", {}))
    assert res["status"] == "failed"
    assert "missing_prompt" in res["error"]


def test_unknown_op_fails(tmp_path: Path):
    tool = ImageGenTool(images_dir=tmp_path)
    res = tool(_action("evil_op", {"prompt": "x"}))
    assert res["status"] == "failed"


def test_classifier_routes_image_generation(make_runtime_factory):
    rt = make_runtime_factory(planner=SmartMockPlanner())
    result = rt.run(raw_goal="Generate an image of a red apple on a wooden table")
    assert result.pre_assessment.task_category == "image_generation"
    assert result.plan is not None
    ops = [a.operation for a in result.plan.actions]
    assert "generate_image" in ops
    a = result.plan.actions[0]
    assert a.tool == "image_gen"
    # prompt extraction stripped the imperative prefix
    assert "red apple" in a.metadata.get("prompt", "")


def test_filename_is_safe_slug(tmp_path: Path, monkeypatch):
    # Use deterministic placeholder mode so this test exercises the
    # filename-slug guard without depending on any image provider.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    tool = ImageGenTool(images_dir=tmp_path, workspace_roots=[str(tmp_path)])
    res = tool(_action("generate_image", {"prompt": "../../etc/passwd ;rm -rf /"}))
    assert res["status"] == "success"
    saved = Path(res["affected"][0])
    assert ".." not in saved.name
    assert "/" not in saved.name
    assert "\\" not in saved.name
