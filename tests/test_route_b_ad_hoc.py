"""Route B (V3) — ad-hoc school-event reporting: detection, no-steal, and the
keyless curated run. The generalisation demo: a short unseen speech-competition
prompt builds a temporary governed case and produces a public post, internal
report, and parent notices — keeping student-sensitive struggle out of public
output. Governance enforcement (RED self-block, no-invent, no-persist) is
covered in test_route_b_governance.py; this file proves routing + content.
"""
from __future__ import annotations

from pathlib import Path

from teow_agl.adapters.smart_mock_planner import SmartMockPlanner
from teow_agl.models import TaskEnvelope
from teow_agl.modules.module_102w_workflow_resolver import WorkflowResolver
from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.runtime import Runtime
from teow_agl.tools.chat_tool import ChatTool
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool
from teow_agl.tools.report_tools import ReportTool

ROUTE_B_GOAL = (
    "School X held an April upper-level English speech competition. Alice won "
    "Champion, Ben won 2nd place, and Chloe won 3rd place. Alice will represent "
    "the school at district level. Daniel and Emma could not finish memorising "
    "their speeches. The school will simplify their scripts, coach them for two "
    "weeks, and let them speak again at assembly. Prepare a Facebook post, "
    "internal report, champion parent notice, and private guidance notice for "
    "Daniel and Emma's parents. Do not send or publish anything."
)
NAT_GOAL = "全国赛成绩出来了，处理一下。"
POST_EVENT_GOAL = "Sports day results are ready. Prepare everything."


def _resolver(workspace: Path) -> WorkflowResolver:
    return WorkflowResolver(config_dir=workspace / "configs", domain="public_school")


def _envelope(goal: str, **md) -> TaskEnvelope:
    return TaskEnvelope(session_id="s", user_id="u", raw_goal=goal,
                        normalized_goal=goal, metadata=dict(md))


def _runtime(workspace: Path, *, gate: str = "reject_all") -> Runtime:
    roots = [str(workspace / "workspace"), str(workspace / "outputs")]
    tools = {
        "fs": FilesystemTool(roots), "chat": ChatTool(), "report": ReportTool(),
        **{n: MockTool(n) for n in ("docx", "pptx", "xlsx", "desktop", "gui",
                                    "email", "publish", "code", "shell", "human")},
    }
    rt = Runtime(
        config_dir=workspace / "configs", prompts_dir=workspace / "prompts",
        planner=SmartMockPlanner(default_outputs_dir=str(workspace / "outputs")),
        tool_registry=tools, human_gate=HumanGate(gate),
        trace_dir=workspace / "traces", domain_pack="public_school",
    )
    rt.profile.profile["workspace_roots"] = roots
    return rt


# ── detection + no-steal ────────────────────────────────────────────────────

def test_route_b_detected(isolated_workspace: Path):
    res = _resolver(isolated_workspace).resolve(_envelope(ROUTE_B_GOAL), None)
    assert res is not None, "Route B speech-competition goal did not resolve"
    assert res["workflow_id"] == "ad_hoc_school_event_reporting", res["workflow_id"]
    assert res["confidence"] >= 0.7


def test_route_b_does_not_steal_national(isolated_workspace: Path):
    # ad_hoc_*.json sorts first, so it MUST NOT swallow the national goal.
    res = _resolver(isolated_workspace).resolve(_envelope(NAT_GOAL), None)
    assert res is not None and res["workflow_id"] == "national_athletics_reporting"


def test_route_b_does_not_steal_post_event(isolated_workspace: Path):
    res = _resolver(isolated_workspace).resolve(_envelope(POST_EVENT_GOAL), None)
    assert res is not None and res["workflow_id"] == "post_event_reporting"


def test_generic_prepare_cue_alone_does_not_trigger_route_b(isolated_workspace: Path):
    # No speech-competition anchor → ad_hoc must not fire on a bare cue.
    res = _resolver(isolated_workspace).resolve(
        _envelope("Please prepare a facebook post and internal report."), None)
    assert res is None or res["workflow_id"] != "ad_hoc_school_event_reporting"


# ── keyless curated run ─────────────────────────────────────────────────────

def _outputs(workspace: Path) -> Path:
    return workspace / "outputs"


def test_route_b_produces_the_four_outputs(isolated_workspace: Path):
    rt = _runtime(isolated_workspace)
    rt.run(raw_goal=ROUTE_B_GOAL)
    out = _outputs(isolated_workspace)
    for name in ("draft_public_fb_post.md", "champion_notice_alice.md",
                 "guidance_notice_daniel_emma.md", "save_internal_report.md"):
        assert (out / name).exists(), f"missing output {name}"


def test_route_b_fb_post_excludes_struggling_students(isolated_workspace: Path):
    """The flagship public/private boundary: winners are celebrated, but the
    struggling pupils' names and their memorisation difficulty NEVER appear in
    the public Facebook post."""
    rt = _runtime(isolated_workspace)
    rt.run(raw_goal=ROUTE_B_GOAL)
    fb = (_outputs(isolated_workspace) / "draft_public_fb_post.md").read_text(
        encoding="utf-8").lower()
    # winners ARE celebrated
    assert "alice" in fb and "ben" in fb and "chloe" in fb
    # struggling pupils + their difficulty are NOT exposed
    for bad in ("daniel", "emma", "memoris", "could not finish",
                "simplify", "coaching"):
        assert bad not in fb, f"FB post leaked student-sensitive detail: {bad!r}"


def test_route_b_internal_report_keeps_support_and_tbc(isolated_workspace: Path):
    """The internal report MAY hold the student-support detail and must mark
    missing facts to-be-confirmed rather than inventing them."""
    rt = _runtime(isolated_workspace)
    rt.run(raw_goal=ROUTE_B_GOAL)
    rep = (_outputs(isolated_workspace) / "save_internal_report.md").read_text(
        encoding="utf-8").lower()
    assert "daniel" in rep and "emma" in rep          # support detail is internal-OK
    assert "to be confirmed" in rep or "tbc" in rep   # missing details marked, not invented
