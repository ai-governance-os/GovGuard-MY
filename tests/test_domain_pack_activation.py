"""Domain-pack activation tests (adapted from build1 for the GovGuard MY spine).

Why adapted, not ported verbatim: the 10.7.4 spine diverged from build1 — it
has no ``teow_agl.policies.learning_hygiene`` module, no approval-card-template
subsystem on its GovernanceModule, and the public build ships only the
``public_school`` flagship pack (+ a portability stub), not the legal/medical
packs. So these tests assert the SAME architectural guarantees the build1 test
checked — additive-only strengthening, activation, judge rubric + learning
exclusions, weakening rejected, domain_context reaching the planner brief and
trace — but against the ``public_school`` pack and what the spine actually has.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from teow_agl.config_loader import load_config
from teow_agl.modules.module_105_human_gate import HumanGate
from teow_agl.policies.domain_pack import (
    apply_domain_pack_to_profile,
    load_domain_pack,
)
from teow_agl.runtime import Runtime
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.mock_tools import MockTool
from teow_agl.tools.report_tools import ReportTool


ROOT = Path(__file__).resolve().parents[1]

SCHOOL_APPROVAL_ACTIONS = [
    "parent_notice_broadcast",
    "student_record_modification",
    "external_communication_send",
    "official_school_circular_release",
    "student_attendance_update",
    "disciplinary_record_reference",
]


class _RecordingPlanner:
    planner_id = "domain_context_recording_planner"

    def __init__(self, body: str) -> None:
        self.body = body
        self.calls = 0
        self.briefs: list[dict] = []

    def plan(self, brief: dict, system_prompt: str) -> dict:
        self.calls += 1
        self.briefs.append(dict(brief))
        return {
            "plan_id": "plan_domain_context",
            "task_id": brief.get("task_id", "unknown"),
            "planner_id": self.planner_id,
            "planning_mode": brief.get("planning_mode", "direct"),
            "used_refusal_recovery": False,
            "notes": [],
            "actions": [
                {
                    "action_id": "act_domain_context_report",
                    "tool": "chat",
                    "operation": "answer",
                    "target": "",
                    "purpose": "write report with domain context available",
                    "expected_effect": "user receives a report",
                    "reversibility": "high",
                    "uncertainty": "low",
                    "risk_factors": [],
                    "requires_governance": True,
                    "metadata": {"body": self.body},
                }
            ],
        }


def _runtime(
    workspace: Path,
    *,
    domain_pack: str | None = "public_school",
    gate: str = "reject_all",
    planner=None,
) -> Runtime:
    workspace_roots = [
        str(workspace / "workspace"),
        str(workspace / "outputs"),
        str(workspace / "client_exports"),
    ]
    (workspace / "client_exports").mkdir(exist_ok=True)
    from teow_agl.adapters.mock_planner import MockPlanner
    from teow_agl.tools.chat_tool import ChatTool

    tools = {
        "fs": FilesystemTool(workspace_roots),
        "chat": ChatTool(),
        "report": ReportTool(),
        "docx": MockTool("docx"),
        "pptx": MockTool("pptx"),
        "xlsx": MockTool("xlsx"),
        "desktop": MockTool("desktop"),
        "gui": MockTool("gui"),
        "email": MockTool("email"),
        "publish": MockTool("publish"),
        "code": MockTool("code"),
        "shell": MockTool("shell"),
        "human": MockTool("human"),
    }
    rt = Runtime(
        config_dir=workspace / "configs",
        prompts_dir=workspace / "prompts",
        planner=planner or MockPlanner(),
        tool_registry=tools,
        human_gate=HumanGate(gate),
        trace_dir=workspace / "traces",
        profile_filename="default_user_governance_profile.json",
        domain_pack=domain_pack,
    )
    rt.profile.profile["workspace_roots"] = workspace_roots
    return rt


# ---------------------------------------------------------------------------
# Pure overlay semantics — additive-only strengthening
# ---------------------------------------------------------------------------
def test_public_school_pack_parses_and_only_strengthens():
    cfg = load_config(ROOT / "configs")
    base_profile = cfg.governance_profile
    pack = load_domain_pack(ROOT / "configs", "public_school")
    assert pack is not None
    merged = apply_domain_pack_to_profile(base_profile, pack)

    assert merged is not base_profile
    assert "public_school" in merged["active_domain_packs"]
    assert (
        "domain:public_school@10.7.3-public-school-governance-overlay"
        in merged["profile_version"]
    )

    # Additive only: every base entry survives in the merged profile.
    for key in (
        "approval_required_actions",
        "hard_block_actions",
        "high_value_assets",
        "sensitive_patterns",
    ):
        assert set(base_profile.get(key, [])).issubset(set(merged.get(key, [])))

    for action in SCHOOL_APPROVAL_ACTIONS:
        assert action in merged["approval_required_actions"]
    assert "autonomous_external_send_to_parents" in merged["hard_block_actions"]
    assert "**/students/**" in merged["high_value_assets"]
    assert "**/student/**" in merged["sensitive_patterns"]


# ---------------------------------------------------------------------------
# Runtime activation
# ---------------------------------------------------------------------------
def test_runtime_activates_public_school_pack(isolated_workspace: Path):
    rt = _runtime(isolated_workspace, domain_pack="public_school")

    assert rt.domain_pack is not None
    assert rt.domain_pack.name == "public_school"
    assert rt.profile.profile["active_domain_packs"] == ["public_school"]
    assert (
        "domain:public_school@10.7.3-public-school-governance-overlay"
        in rt.cfg.policy_version()
    )

    # Profile gains the six school approval actions + the hard block.
    have = set(rt.profile.profile["approval_required_actions"])
    assert all(a in have for a in SCHOOL_APPROVAL_ACTIONS)
    assert (
        "autonomous_external_send_to_parents"
        in rt.profile.profile["hard_block_actions"]
    )

    # Judge rubric + learning exclusions overlaid.
    assert "school_notice_draft" in rt.judge_rubrics
    assert "student_personal_data" in rt.learning_exclusion_rules[
        "exclude_content_patterns"
    ]
    assert "public_school" in rt.learning_exclusion_rules["domain_pack_exclusions"]

    # Approval cards merged (available for the GREEN review gate / UI).
    card_ids = {t.get("id") for t in rt.approval_templates.get("templates", [])}
    assert "parent_notice_broadcast" in card_ids


# ---------------------------------------------------------------------------
# Additive-only enforcement — a pack may never weaken base governance
# ---------------------------------------------------------------------------
def test_domain_pack_cannot_remove_or_weaken_base_policy(isolated_workspace: Path):
    bad = isolated_workspace / "configs" / "domain_packs" / "bad_remove"
    bad.mkdir(parents=True)
    (bad / "governance_profile_overlay.json").write_text(
        json.dumps(
            {
                "policy_version": "test-bad-remove",
                "approval_required_actions_remove": ["run_shell_command"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="attempted weakening"):
        _runtime(isolated_workspace, domain_pack="bad_remove")


def test_invalid_domain_pack_name_is_rejected():
    with pytest.raises(ValueError, match="invalid domain pack name"):
        load_domain_pack(ROOT / "configs", "../public_school")


# ---------------------------------------------------------------------------
# domain_context reaches the planner brief AND the audit trace
# ---------------------------------------------------------------------------
def test_domain_context_reaches_planner_brief_and_trace(isolated_workspace: Path):
    planner = _RecordingPlanner(
        "A trilingual parent notice should be framed as a draft for educator "
        "review, with aligned Bahasa Melayu, Chinese, and English versions, "
        "explicit assumptions, and no student personal data."
    )
    rt = _runtime(
        isolated_workspace,
        domain_pack="public_school",
        gate="approve_all",
        planner=planner,
    )
    result = rt.run(
        raw_goal="Write a short report about the trilingual parent-notice workflow"
    )

    assert planner.calls == 1
    brief = planner.briefs[0]
    domain_context = brief["domain_context"]
    assert domain_context["domain"] == "public_school"
    assert domain_context["active"] is True
    assert "domain_context_note" in brief
    assert any(
        "educator review" in item
        for item in domain_context["planner_guidance"]["must_do"]
    )

    events = rt.trace.read_all()
    domain_events = [
        event
        for event in events
        if event["task_id"] == result.task_id
        and event["module"] == "DOMAIN"
        and event["event_type"] == "domain_pack_active"
    ]
    assert domain_events
    assert (
        domain_events[0]["details"]["domain_context"]["domain"] == "public_school"
    )


def test_no_domain_pack_keeps_planner_brief_clean(isolated_workspace: Path):
    planner = _RecordingPlanner(
        "Ordinary productivity workflows should clarify the request, produce "
        "the requested output, and avoid unnecessary side effects."
    )
    rt = _runtime(
        isolated_workspace,
        domain_pack=None,
        gate="approve_all",
        planner=planner,
    )
    result = rt.run(raw_goal="Write a short report about ordinary productivity")

    assert planner.calls == 1
    assert "domain_context" not in planner.briefs[0]
    assert not any(
        event["task_id"] == result.task_id and event["module"] == "DOMAIN"
        for event in rt.trace.read_all()
    )
