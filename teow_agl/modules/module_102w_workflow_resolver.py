"""Module 102W — Workflow Resolver (config-driven, deterministic, offline).

Detects whether a user goal matches a configured public-sector workflow
template and, if so, builds the corresponding ``CandidatePlan``.

Detection is 100% offline — no remote LLM is involved (the LLM is never the
safety authority). A workflow fires only when:

  1. a ``strong_trigger_phrase`` is present (confidence 0.9), OR
  2. at least one ``anchor_term`` AND at least one ``action_cue`` co-occur
     (confidence ~0.7).

An ``action_cue`` alone never fires — false negatives are safer than false
positives (brief §D). Sub-goals (decomposition leaves) never resolve a
workflow, so the task-tree and the workflow engine can't fight over a task.

The resolver only changes WHERE a plan comes from. Every action it emits
still flows through 101B → 103 → 105/107 like any other plan; it never
bypasses governance.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..models import CandidateAction, CandidatePlan

# Strip CJK + ASCII whitespace/punctuation before substring matching so that
# "成绩出来了，处理一下。" matches the phrase "成绩出来了，处理一下" and the English
# "Sports day results are ready." matches "sports day results are ready".
_PUNCT = re.compile(
    r"[\s，。！？、；：,.!?;:·…—\-_/\\（）()\[\]【】「」『』\"'“”‘’~`@#$%^&*+=|<>]+"
)


def _norm(text: Any) -> str:
    return _PUNCT.sub("", str(text or "")).lower()


class WorkflowResolver:
    """Deterministic, config-driven workflow detection + plan construction."""

    def __init__(self, config_dir: str | Path, domain: str | None = None) -> None:
        self.config_dir = Path(config_dir)
        self.domain = domain
        self.templates: list[dict] = self._load_templates()

    # ------------------------------------------------------------------ load
    def _load_templates(self) -> list[dict]:
        root = self.config_dir / "workflows"
        if not root.is_dir():
            return []
        # Prefer the active domain's folder; fall back to scanning all domains
        # so a mis-set domain never silently disables every workflow.
        if self.domain and (root / self.domain).is_dir():
            dirs = [root / self.domain]
        else:
            dirs = [d for d in sorted(root.iterdir()) if d.is_dir()]
        templates: list[dict] = []
        for d in dirs:
            for fp in sorted(d.glob("*.json")):
                try:
                    with fp.open("r", encoding="utf-8") as f:
                        tpl = json.load(f)
                except Exception:
                    continue
                tpl["_source_path"] = str(fp)
                tpl["_norm_strong"] = [
                    (_norm(p), p) for p in tpl.get("strong_trigger_phrases", [])
                ]
                tpl["_norm_anchors"] = [
                    (_norm(a), a) for a in tpl.get("anchor_terms", [])
                ]
                tpl["_norm_cues"] = [
                    (_norm(c), c) for c in tpl.get("action_cues", [])
                ]
                templates.append(tpl)
        return templates

    # --------------------------------------------------------------- resolve
    def resolve(self, envelope, pre, domain_context=None) -> dict | None:
        # Never resolve a sub-goal (a decomposition leaf) — the tree owns it.
        if getattr(envelope, "metadata", {}).get("_is_subgoal"):
            return None
        goal = (
            f"{getattr(envelope, 'normalized_goal', '') or ''} "
            f"{getattr(envelope, 'raw_goal', '') or ''}"
        )
        norm = _norm(goal)
        if not norm:
            return None
        for tpl in self.templates:
            res = self._match(tpl, norm)
            if res is not None:
                return res
        return None

    def _match(self, tpl: dict, norm: str) -> dict | None:
        # 1) strong phrase substring → high confidence
        for nphrase, raw in tpl["_norm_strong"]:
            if nphrase and nphrase in norm:
                return self._resolution(tpl, confidence=0.9, matched=raw)
        # 2) >=1 anchor AND >=1 cue → medium confidence
        anchor_hit = next(
            (raw for n, raw in tpl["_norm_anchors"] if n and n in norm), None
        )
        cue_hit = next(
            (raw for n, raw in tpl["_norm_cues"] if n and n in norm), None
        )
        if anchor_hit and cue_hit:
            return self._resolution(
                tpl, confidence=0.7, matched=f"{anchor_hit}+{cue_hit}"
            )
        # 3) an action_cue alone never detects (conservative threshold)
        return None

    def _resolution(self, tpl: dict, confidence: float, matched: str) -> dict:
        return {
            "workflow_detected": True,
            "workflow_id": tpl.get("workflow_id"),
            "workflow_name": tpl.get("display_name", tpl.get("workflow_id")),
            "workflow_version": tpl.get("version"),
            "confidence": confidence,
            "matched_trigger": matched,
            "deadline_hours": tpl.get("deadline_hours", 24),
            "priority": tpl.get("default_priority", "high"),
            "steps": tpl.get("steps", []),
            "curated_drafts": tpl.get("curated_drafts"),
            "results_source": tpl.get("results_source"),
            "_source_path": tpl.get("_source_path"),
        }

    # ------------------------------------------------------------ build_plan
    def build_plan(self, resolution, envelope, pre, tool_catalog=None) -> CandidatePlan:
        steps = resolution.get("steps", [])
        deadline_h = resolution.get("deadline_hours") or 24
        due_at = self._due_at(deadline_h)
        valid_tools = set((tool_catalog or {}).get("tools", {}).keys())
        actions: list[CandidateAction] = []
        for idx, step in enumerate(steps):
            tool = step.get("tool_hint", "chat")
            op = step.get("operation_hint", "answer")
            # Defensive: never emit a tool outside the closed catalog.
            if valid_tools and tool not in valid_tools:
                tool, op = "chat", "answer"
            route_hint = step.get("route_hint", "BLUE")
            output_scope = step.get("output_scope", "internal")
            target = self._target_for(op, step, envelope)
            md: dict = {
                "workflow_id": resolution.get("workflow_id"),
                "workflow_step_id": step.get("step_id"),
                "workflow_step_name": step.get("display_name", step.get("step_id")),
                "workflow_name": resolution.get("workflow_name"),
                "workflow_detected": True,
                "workflow_source": "config_template",
                "workflow_confidence": resolution.get("confidence"),
                "workflow_step_index": idx,
                "due_at": due_at,
                "priority": step.get("priority", resolution.get("priority", "high")),
                "time_rule": step.get("time_rule"),
                "execution_window": step.get("time_rule"),
                "output_scope": output_scope,
                "allowed_data": step.get("allowed_data", []),
                "blocked_data": step.get("blocked_data", []),
                "data_categories": step.get("allowed_data", []),
                "data_use_purpose": step.get("goal", ""),
                "approval_boundary": step.get("approval_boundary", "none"),
                "route_hint": route_hint,
            }
            # Latency / cleanliness: steps that are NOT content deliverables get
            # a deterministic template body and never call the live LLM —
            #   * the report-stub step (its body is overwritten by ReportTool),
            #   * the self-block step (route RED — blocked, never executed),
            #   * the release/status step (public_release — a "ready for
            #     approval" status, not free-form content).
            # The real content drafts (internal report save, FB post, parent
            # notice) are the only steps the live model drafts.
            if (op == "draft_report" or route_hint == "RED"
                    or output_scope in ("public_release", "audit", "official_record")):
                md["workflow_template_only"] = True
            # Content is produced by the 102B synthesizer (a presentable
            # bilingual workflow draft with no key, or richer text under a live
            # provider) — see `_workflow_fallback_body`. We only fix the output
            # filename here so the saved draft lands predictably under outputs/.
            if op == "save_under_outputs":
                md.setdefault("filename", f"{step.get('step_id', 'draft')}.md")
            actions.append(
                CandidateAction(
                    tool=tool,
                    operation=op,
                    target=target,
                    purpose=step.get("goal", step.get("display_name", "")),
                    expected_effect=step.get("display_name", ""),
                    reversibility="medium" if output_scope == "public_release" else "high",
                    uncertainty="low",
                    risk_factors=[],
                    requires_governance=True,
                    metadata=md,
                )
            )
        return CandidatePlan(
            task_id=envelope.task_id,
            planner_id="102W_workflow_resolver",
            planning_mode=getattr(pre, "planning_mode", "draft_first"),
            used_refusal_recovery=False,
            actions=actions,
            notes=[
                f"workflow:{resolution.get('workflow_id')}",
                f"confidence:{resolution.get('confidence')}",
                f"matched:{resolution.get('matched_trigger')}",
            ],
        )

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _due_at(deadline_hours: int) -> str:
        try:
            hrs = int(deadline_hours)
        except Exception:
            hrs = 24
        return (datetime.now(timezone.utc) + timedelta(hours=hrs)).isoformat()

    @staticmethod
    def _target_for(op: str, step: dict, envelope) -> str:
        """Build a target the FilesystemTool will accept — an absolute path
        under the task's workspace/outputs roots. A bare relative name would
        resolve to the CWD (outside the roots) and be denied."""
        roots = list(getattr(envelope, "workspace_roots", []) or [])
        outputs = Path(roots[1]) if len(roots) > 1 else (
            Path(roots[0]) if roots else Path("outputs"))
        inputs = Path(roots[0]) if roots else Path("workspace")
        step_id = step.get("step_id", "draft")
        if op == "read_safe":
            # A workflow may name its own (public-safe) results file via the
            # step's `read_target`; defaults to results.md for the generic case.
            rel = step.get("read_target") or "results.md"
            return str(inputs / rel)
        if op == "save_under_outputs":
            return str(outputs / f"{step_id}.md")
        if op == "draft_report":
            return str(outputs / f"{step_id}.md")
        return ""
