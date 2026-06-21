"""Minimal CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters.smart_mock_planner import SmartMockPlanner
from .modules.module_105_human_gate import HumanGate
from .runtime import Runtime
from .tools.filesystem_tools import FilesystemTool
from .tools.mock_tools import MockTool
from .tools.report_tools import ReportTool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="teow_agl")
    parser.add_argument("goal", help="raw user goal")
    parser.add_argument("--config-dir", default="configs")
    parser.add_argument("--prompts-dir", default="prompts")
    parser.add_argument("--profile", default="default_user_governance_profile.json")
    parser.add_argument("--trace-dir", default="traces")
    args = parser.parse_args(argv)

    workspace_roots = ["./workspace", "./outputs"]
    tools = {
        "fs": FilesystemTool(workspace_roots),
        "report": ReportTool(),
        "email": MockTool("email"),
        "publish": MockTool("publish"),
        "code": MockTool("code"),
        "shell": MockTool("shell"),
        "human": MockTool("human"),
    }
    runtime = Runtime(
        config_dir=args.config_dir, prompts_dir=args.prompts_dir,
        planner=SmartMockPlanner(default_outputs_dir="./outputs"),
        tool_registry=tools, human_gate=HumanGate("prompt"),
        trace_dir=args.trace_dir, profile_filename=Path(args.profile).name,
    )
    result = runtime.run(raw_goal=args.goal)
    summary = {
        "task_id": result.envelope.task_id, "category": result.pre_assessment.task_category,
        "planning_mode": result.pre_assessment.planning_mode,
        "routes": result.routes, "final_route": result.final_route,
        "executions": [e.status for e in result.executions],
        "blocks": len(result.blocks),
        "proposals": [p.patch_type for p in result.proposals],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
