"""Offline evaluation harness (Phase D of SANDBOX_PLAN).

The 900+ unit tests prove the PIPELINE works; this harness measures
BEHAVIOR against a labelled case set: does a given user phrasing land
in the right category, the right lane, the right answer shape? It is
the regression baseline the legal pilot's go/no-go gate reads from.

Case schema (JSONL, one object per line, or a JSON list):

    {
      "case_id": "office_zh_001",
      "goal": "做一份Q3销售的PPT",
      "expect": {
        "category":   "office_doc_generation",   # optional
        "behavior":   "plan",                    # optional, see below
        "final_route": "BLUE",                   # optional exact match
        "route_not":  "RED"                      # optional negative match
      },
      "requires": "semantic_llm",                # optional — skip offline
      "tags": ["zh", "office"]
    }

Behaviors:
    plan          — pipeline produced a normal candidate plan
    direct_answer — served from the capability card (identity/greeting/
                    boundary), no remote planner
    clarify       — 101C asked a clarifying question instead of guessing
    hard_block    — RED at pre-governance
    infeasible    — declined honestly as INFEASIBLE

Cases marked ``requires: semantic_llm`` need a live L2 classifier and
are skipped in offline runs (counted separately, never failed).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_DIRECT_ANSWER_IDS = (
    "identity_direct", "greeting_direct", "desktop_boundary_direct",
)


def load_cases(path: str | Path) -> list[dict]:
    """Load JSONL (one case per line) or a JSON list."""
    p = Path(path)
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        return [c for c in data if isinstance(c, dict)]
    cases = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def behavior_of(result) -> str:
    """Map a TaskRunResult onto the behavior vocabulary above."""
    if result.pre_assessment.hard_block:
        if any(d.route == "INFEASIBLE" for d in result.decisions):
            return "infeasible"
        return "hard_block"
    plan = result.plan
    if plan is None:
        return "none"
    if plan.planner_id == "semantic_clarify":
        return "clarify"
    if plan.planner_id in _DIRECT_ANSWER_IDS:
        return "direct_answer"
    return "plan"


def evaluate_case(result, expect: dict) -> list[str]:
    """Return the list of failed assertions (empty == pass)."""
    failures: list[str] = []
    want_cat = expect.get("category")
    if want_cat:
        got = result.pre_assessment.task_category
        if got != want_cat:
            failures.append(f"category: want={want_cat} got={got}")
    want_beh = expect.get("behavior")
    if want_beh:
        got_beh = behavior_of(result)
        if got_beh != want_beh:
            failures.append(f"behavior: want={want_beh} got={got_beh}")
    want_route = expect.get("final_route")
    if want_route and result.final_route != want_route:
        failures.append(
            f"final_route: want={want_route} got={result.final_route}")
    route_not = expect.get("route_not")
    if route_not and result.final_route == route_not:
        failures.append(f"route_not: must not be {route_not}")
    return failures


def run_cases(
    runtime,
    cases: list[dict],
    *,
    skip_requires: tuple[str, ...] = ("semantic_llm",),
) -> dict:
    """Run every case through `runtime` and aggregate a report dict."""
    case_rows: list[dict] = []
    passed = failed = skipped = errored = 0
    cat_stats: dict[str, dict[str, int]] = {}

    for case in cases:
        cid = str(case.get("case_id") or f"case_{len(case_rows)+1}")
        req = case.get("requires")
        if req and req in skip_requires:
            skipped += 1
            case_rows.append({"case_id": cid, "status": "skipped",
                              "requires": req})
            continue
        expect = case.get("expect") or {}
        try:
            result = runtime.run(raw_goal=str(case.get("goal") or ""))
        except Exception as exc:  # harness must finish the whole set
            errored += 1
            case_rows.append({"case_id": cid, "status": "error",
                              "error": str(exc)})
            continue
        failures = evaluate_case(result, expect)
        want_cat = expect.get("category")
        if want_cat:
            stats = cat_stats.setdefault(want_cat, {"total": 0, "correct": 0})
            stats["total"] += 1
            if result.pre_assessment.task_category == want_cat:
                stats["correct"] += 1
        row = {
            "case_id": cid,
            "goal": case.get("goal"),
            "status": "pass" if not failures else "fail",
            "got": {
                "category": result.pre_assessment.task_category,
                "behavior": behavior_of(result),
                "final_route": result.final_route,
            },
            "expect": expect,
        }
        if failures:
            failed += 1
            row["failures"] = failures
        else:
            passed += 1
        case_rows.append(row)

    evaluated = passed + failed
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(cases),
        "evaluated": evaluated,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errored": errored,
        "pass_rate": round(passed / evaluated, 4) if evaluated else None,
        "category_accuracy": {
            cat: {
                "total": s["total"], "correct": s["correct"],
                "accuracy": round(s["correct"] / s["total"], 4),
            }
            for cat, s in sorted(cat_stats.items())
        },
        "cases": case_rows,
    }
    return report


def write_report(report: dict, out_dir: str | Path) -> tuple[Path, Path]:
    """Write JSON + human-readable Markdown. Returns both paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out / f"eval_report_{stamp}.json"
    md_path = out / f"eval_report_{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [
        "# TEOW-AGL Eval Report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"| total | evaluated | passed | failed | skipped | errored | pass rate |",
        f"|---|---|---|---|---|---|---|",
        f"| {report['total']} | {report['evaluated']} | {report['passed']} "
        f"| {report['failed']} | {report['skipped']} | {report['errored']} "
        f"| {report['pass_rate']} |",
        "",
        "## Category accuracy",
        "",
        "| category | correct / total | accuracy |",
        "|---|---|---|",
    ]
    for cat, s in report["category_accuracy"].items():
        lines.append(f"| {cat} | {s['correct']} / {s['total']} | {s['accuracy']} |")
    fails = [c for c in report["cases"] if c.get("status") == "fail"]
    if fails:
        lines += ["", "## Failures", ""]
        for c in fails:
            lines.append(f"### {c['case_id']}")
            lines.append(f"- goal: `{c.get('goal')}`")
            for f in c.get("failures", []):
                lines.append(f"- ❌ {f}")
            lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path
