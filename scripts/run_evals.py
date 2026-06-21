"""Run the behavior eval set against the offline pipeline.

Usage:
    python -X utf8 scripts/run_evals.py [--cases evals/seed_cases.jsonl]
                                        [--out outputs/evals]

Exit code 0 only when every evaluated case passes — wire it into CI or
run it before every demo / deploy. Uses SmartMockPlanner + mock tools:
classification, routing and answer-shape are real; content generation
is not exercised (that's the live LLM-judge lane, Phase D step 2).

State isolation: traces go to a temp dir, the cost guard is disabled
for the run, and no learning-store paths are wired — an eval run never
pollutes the deployment's state/.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["COST_GUARD_ENABLED"] = "0"          # eval runs are free
os.environ.setdefault("WEB_SEARCH_PROVIDER", "disabled")  # no live calls

from teow_agl.adapters.smart_mock_planner import SmartMockPlanner  # noqa: E402
from teow_agl.eval_harness import load_cases, run_cases, write_report  # noqa: E402
from teow_agl.modules.module_105_human_gate import HumanGate  # noqa: E402
from teow_agl.runtime import Runtime  # noqa: E402
from teow_agl.tools.mock_tools import MockTool  # noqa: E402


def _runtime(trace_dir: str) -> Runtime:
    tools = {n: MockTool(n) for n in
             ["fs", "report", "docx", "pptx", "xlsx", "desktop", "gui",
              "email", "publish", "code", "shell", "memory", "chat",
              "image_gen", "web_search"]}
    return Runtime(
        config_dir=ROOT / "configs", prompts_dir=ROOT / "prompts",
        planner=SmartMockPlanner(default_outputs_dir=str(ROOT / "outputs")),
        tool_registry=tools, human_gate=HumanGate("approve_all"),
        trace_dir=trace_dir,
        # Flagship pack active so the public-school eval cases exercise the
        # real overlaid governance (parent-notice / record-change → GREEN).
        domain_pack=os.environ.get("TEOW_AGL_DOMAIN_PACK", "public_school"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="TEOW-AGL behavior evals")
    ap.add_argument("--cases", default=str(ROOT / "evals" / "seed_cases.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "outputs" / "evals"))
    args = ap.parse_args()

    cases = load_cases(args.cases)
    if not cases:
        print(f"no cases found in {args.cases}")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        rt = _runtime(tmp)
        report = run_cases(rt, cases)
    json_path, md_path = write_report(report, args.out)

    print(f"cases:     {report['total']} "
          f"(evaluated {report['evaluated']}, skipped {report['skipped']})")
    print(f"passed:    {report['passed']}")
    print(f"failed:    {report['failed']}")
    print(f"errored:   {report['errored']}")
    print(f"pass rate: {report['pass_rate']}")
    print(f"report:    {md_path}")
    for c in report["cases"]:
        if c.get("status") == "fail":
            print(f"  FAIL {c['case_id']}: {'; '.join(c['failures'])}")
        elif c.get("status") == "error":
            print(f"  ERROR {c['case_id']}: {c['error']}")
    return 0 if (report["failed"] == 0 and report["errored"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
