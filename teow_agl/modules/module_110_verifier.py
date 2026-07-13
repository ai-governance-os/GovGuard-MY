"""
Module 110 — Verifier (light).

After the executor (107) finishes the plan, the verifier asks one
question: *did the output actually satisfy the user's goal?*

Three deterministic checks, no LLM:

  1. length_check   — "user asked for ~N words → did we deliver
                      between N×0.5 and N×2.5?"
  2. format_check   — "user asked for a .docx → is there a non-empty
                      .docx in affected_resources?"
  3. refusal_sniff  — "the chat answer literally says 'I can't help
                      with that' AND governance said it's fine to
                      proceed (BLUE/GREEN) — that's a soft failure"

Returns a structured verification dict the runtime stores on the
TaskRunResult. The runtime decides what to do with a failure (record
a failure outcome in 104, emit a trace event); the verifier itself is
side-effect free.

Phase 14 will upgrade this module with an LLM-as-judge step. The
plumbing here (rules JSON, dict shape, runtime hook) is designed so
that upgrade is purely additive — the light checks remain as a cheap
pre-filter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import CandidateAction, ExecutionResult, TaskEnvelope
from .module_school_artifact_guard import (
    is_school_output_contract,
    school_artifact_verification_checks,
)


# Tools whose output is conversational prose (counted by words against
# user's word-count intent).
_PROSE_TOOLS = {"chat", "docx", "report", "fs"}


class VerifierModule:
    """Module 110 — verifier. Phase 12 mechanical checks + Phase 14
    LLM-as-judge upgrade. Both layers are side-effect-free; the runtime
    decides what to do with failures (record + retry / record + give up).
    """

    module_id = "110"

    def __init__(
        self,
        *,
        rules: dict | None = None,
        rubrics: dict | None = None,
        chat_llm: Any | None = None,
    ) -> None:
        """`rules` is the parsed configs/verifier_rules.json dict.
        `rubrics` is the parsed configs/judge_rubrics.json dict.
        `chat_llm` is required only if LLM-judge will be used; when
        absent, llm_judge() short-circuits to a skipped result."""
        self.rules = rules or {}
        self.rubrics = rubrics or {}
        self.chat_llm = chat_llm
        self._compiled_word_patterns: list[re.Pattern] = []
        for pat in (self.rules.get("length_check", {}) or {})\
                .get("word_intent_patterns", []) or []:
            try:
                self._compiled_word_patterns.append(
                    re.compile(pat, re.IGNORECASE))
            except re.error:
                continue
        self._compiled_ext_patterns: dict[str, re.Pattern] = {}
        ext_map = (self.rules.get("format_check", {}) or {})\
            .get("extension_patterns", {}) or {}
        for ext, pat in ext_map.items():
            try:
                self._compiled_ext_patterns[ext] = re.compile(pat, re.IGNORECASE)
            except re.error:
                continue

    # ------------------------------------------------------------------
    # Public entry — called by runtime once per task.
    # ------------------------------------------------------------------
    def verify(
        self,
        *,
        envelope: TaskEnvelope,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        final_route: str,
        task_category: str | None = None,
        used_adapted_skill: bool = False,
        adapted_target_tool: str = "",
    ) -> dict:
        """Return a verification report dict. Never raises.

        `task_category` (Phase B) is the category 101A assigned to the
        task. When supplied, scenario-specific sub-checks from
        `verifier_rules.scenario_checks.by_category[<category>]` are
        applied in addition to the three baseline checks.

        `used_adapted_skill` (Phase 2 L4.6) — set True by the runtime when
        this task was solved using a CROSS-CONTEXT ADAPTED skill (the
        synthesizer rewrote a stored procedure from one tool to another).
        When True, the `_skill_adapted_strict_mode` sub-checks run: a
        higher length floor and a mandatory target-tool format match.
        `adapted_target_tool` is the tool the skill was adapted TO
        (e.g. "pptx"); it drives the format-match requirement.

        Shape:
          {
            "enabled": bool,
            "pass": bool,
            "checks": [
              {"name": "length_check", "pass": bool,
               "reason": str, "details": {...}},
              ...
            ],
            "summary": "short string for UI",
            "fail_outcome": "failure"  # passed through from config
          }
        """
        out: dict = {
            "enabled": bool(self.rules.get("enabled", True)),
            "pass": True,
            "checks": [],
            "summary": "",
            "fail_outcome": self.rules.get("fail_outcome", "failure"),
        }
        if not out["enabled"]:
            out["summary"] = "verifier_disabled"
            return out

        any_success = any(e.status == "success" for e in executions)
        # A pure RED / INFEASIBLE task intentionally produces no artifact or
        # external side effect. A mixed response pack can still have useful
        # BLUE artifacts alongside one blocked step; those successful outputs
        # must continue through verification.
        if ((final_route or "").upper() in {"RED", "INFEASIBLE"}
                and not any_success):
            out["summary"] = f"skipped:route_exempt:{final_route.upper()}"
            return out

        # If nothing executed successfully, there's nothing to verify;
        # we leave the upstream failure handling to do its job and pass
        # silently. (We do NOT mark this as a verifier-pass; we mark it
        # as not-applicable.)
        if not any_success:
            if is_school_output_contract(plan_actions):
                school_checks = school_artifact_verification_checks(
                    envelope, plan_actions, executions)
                out["checks"].extend(school_checks)
                out["pass"] = False
                out["summary"] = (
                    "failed: school.execution_completeness="
                    "no_successful_executions"
                )
                return out
            out["pass"] = True
            out["summary"] = "skipped:no_successful_executions"
            return out

        user_intent = (envelope.normalized_goal or "").strip()

        # Run checks; collect into `checks`.
        len_check = self._length_check(user_intent, plan_actions, executions)
        if len_check is not None:
            out["checks"].append(len_check)

        preferred_ext = ""
        if used_adapted_skill:
            preferred_ext = self._TOOL_EXTENSION.get(
                (adapted_target_tool or "").lower(), "")
        fmt_check = self._format_check(
            user_intent, plan_actions, executions,
            preferred_ext=preferred_ext,
        )
        if fmt_check is not None:
            out["checks"].append(fmt_check)

        ref_check = self._refusal_sniff(plan_actions, executions, final_route)
        if ref_check is not None:
            out["checks"].append(ref_check)

        # P0.2 — no internal generation-failure / apology text inside any
        # generated office artifact (the judge sees the file, not the reason).
        art_check = self._artifact_failure_sniff(plan_actions, executions)
        if art_check is not None:
            out["checks"].append(art_check)

        # School artifacts are checked independently. A correct sibling can
        # never hide a contaminated, ungrounded or failed file in an aggregate.
        out["checks"].extend(school_artifact_verification_checks(
            envelope, plan_actions, executions))

        # Phase B — scenario-specific checks (per-category sub-rules).
        # Returns a LIST of check dicts (one per applicable sub-rule) so
        # the UI / trace can see which specific scenario rule passed or
        # failed (e.g. office_doc.no_placeholder vs research.has_sources).
        scenario_checks = self._scenario_checks(
            user_intent, plan_actions, executions, task_category)
        out["checks"].extend(scenario_checks)

        # Phase 2 (L4.6) — stricter bar for tasks solved via an adapted
        # skill. Only runs when the runtime flags used_adapted_skill.
        if used_adapted_skill:
            strict_checks = self._skill_adapted_strict_checks(
                user_intent, plan_actions, executions, adapted_target_tool)
            out["checks"].extend(strict_checks)
            out["adapted_skill_strict_mode"] = True

        failed_checks = [c for c in out["checks"] if not c["pass"]]
        out["pass"] = len(failed_checks) == 0
        if out["pass"]:
            ok_names = [c["name"] for c in out["checks"]]
            out["summary"] = (f"all checks passed ({len(ok_names)}: "
                              f"{','.join(ok_names) or 'none_applied'})"
                              if ok_names else "no_applicable_checks")
        else:
            reasons = [f"{c['name']}={c['reason']}" for c in failed_checks]
            out["summary"] = f"failed: {' ; '.join(reasons)[:300]}"
        return out

    # ------------------------------------------------------------------
    # Check 1: length sanity
    # ------------------------------------------------------------------
    def _length_check(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> dict | None:
        cfg = self.rules.get("length_check") or {}
        if not cfg.get("enabled", True):
            return None
        target_words = self._extract_target_words(user_intent)
        if target_words is None:
            # User didn't ask for a specific length — check is N/A.
            return None
        applies = set(cfg.get("applies_to_tools", []))
        min_ratio = float(cfg.get("min_ratio", 0.5))
        max_ratio = float(cfg.get("max_ratio", 2.5))

        body = self._collect_prose_body(plan_actions, executions, applies)
        if not body:
            return {
                "name": "length_check",
                "pass": False,
                "reason": f"requested~{target_words}w_no_prose_body_found",
                "details": {"target_words": target_words,
                            "actual_words": 0},
            }
        actual_words = self._word_count(body)
        lo = int(target_words * min_ratio)
        hi = int(target_words * max_ratio)
        if lo <= actual_words <= hi:
            return {
                "name": "length_check", "pass": True,
                "reason": "ok",
                "details": {"target_words": target_words,
                            "actual_words": actual_words,
                            "lower_bound": lo, "upper_bound": hi},
            }
        if actual_words < lo:
            kind = "too_short"
        else:
            kind = "too_long"
        return {
            "name": "length_check", "pass": False,
            "reason": f"{kind}:target~{target_words}_got_{actual_words}",
            "details": {"target_words": target_words,
                        "actual_words": actual_words,
                        "lower_bound": lo, "upper_bound": hi},
        }

    # ------------------------------------------------------------------
    # Check 2: format / artifact sanity
    # ------------------------------------------------------------------
    def _format_check(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        preferred_ext: str = "",
    ) -> dict | None:
        cfg = self.rules.get("format_check") or {}
        if not cfg.get("enabled", True):
            return None
        expected_exts = self._extensions_from_intent(user_intent)
        if preferred_ext and preferred_ext in expected_exts:
            expected_exts = [preferred_ext]
        if not expected_exts:
            return None  # user didn't ask for a specific file format
        min_bytes = int(cfg.get("min_bytes", 64))
        affected_files: list[Path] = []
        for e in executions:
            if e.status != "success":
                continue
            for path in (e.affected_resources or []):
                try:
                    affected_files.append(Path(path))
                except Exception:
                    continue
        missing: list[str] = []
        too_small: list[str] = []
        ok_exts: list[str] = []
        for ext in expected_exts:
            # Look for any affected file whose suffix matches.
            matches = [p for p in affected_files
                       if p.suffix.lower().lstrip(".") == ext]
            if not matches:
                missing.append(ext)
                continue
            # Check size (best-effort; if the file is gone for any
            # reason we report missing, not too_small).
            sized_ok = False
            for p in matches:
                try:
                    if p.exists() and p.stat().st_size >= min_bytes:
                        sized_ok = True
                        break
                except Exception:
                    continue
            if sized_ok:
                ok_exts.append(ext)
            else:
                too_small.append(ext)
        if not missing and not too_small:
            return {
                "name": "format_check", "pass": True,
                "reason": "ok",
                "details": {"expected_extensions": expected_exts,
                            "verified": ok_exts},
            }
        bits: list[str] = []
        if missing:
            bits.append(f"missing={','.join(missing)}")
        if too_small:
            bits.append(f"too_small={','.join(too_small)}")
        return {
            "name": "format_check", "pass": False,
            "reason": ";".join(bits),
            "details": {"expected_extensions": expected_exts,
                        "missing": missing, "too_small": too_small,
                        "verified": ok_exts,
                        "affected_files":
                            [str(p) for p in affected_files]},
        }

    # ------------------------------------------------------------------
    # Check 3: refusal sniff (chat tool only)
    # ------------------------------------------------------------------
    def _refusal_sniff(
        self,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        final_route: str,
    ) -> dict | None:
        cfg = self.rules.get("refusal_sniff") or {}
        if not cfg.get("enabled", True):
            return None
        exempt = set(r.upper() for r in cfg.get("exempt_routes", []))
        if (final_route or "").upper() in exempt:
            return None  # RED / INFEASIBLE — a refusal here is correct
        applies = set(cfg.get("applies_to_tools", ["chat"]))
        phrases = [p.lower() for p in cfg.get("phrases", [])]
        if not phrases:
            return None
        # Look at successful chat executions
        body = self._collect_prose_body(plan_actions, executions, applies)
        if not body:
            return None
        lowered = body.lower()
        matched = [p for p in phrases if p in lowered]
        if not matched:
            return {
                "name": "refusal_sniff", "pass": True,
                "reason": "no_refusal_phrases",
                "details": {"phrases_checked": len(phrases)},
            }
        # We saw a refusal phrase in approved output → soft failure.
        return {
            "name": "refusal_sniff", "pass": False,
            "reason": f"refusal_in_approved_output:{matched[0][:80]}",
            "details": {"matched_phrases": matched[:5],
                        "final_route": final_route,
                        "body_preview": body[:200]},
        }

    def _artifact_failure_sniff(
        self,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> dict | None:
        """P0.2 — fail if a generated artifact (docx/pptx/xlsx/report) still
        contains internal generation-failure / apology text. A judge sees the
        artifact, not the internal reason, so an apology baked into a .docx
        must register as a verify-FAIL, never a green 'VERIFIED'."""
        cfg = self.rules.get("artifact_failure_sniff") or {}
        if not cfg.get("enabled", True):
            return None
        phrases = [p.lower() for p in cfg.get("phrases", [])]
        if not phrases:
            return None
        applies = set(cfg.get("applies_to_tools",
                              ["docx", "pptx", "xlsx", "report"]))
        body = self._collect_prose_body(plan_actions, executions, applies)
        if not body:
            return None
        lowered = body.lower()
        matched = [p for p in phrases if p in lowered]
        if not matched:
            return {"name": "artifact_failure_sniff", "pass": True,
                    "reason": "no_generation_failure_text"}
        return {
            "name": "artifact_failure_sniff", "pass": False,
            "reason": f"generation_failure_text_in_artifact:{matched[0][:60]}",
            "details": {"matched": matched[:3], "body_preview": body[:200]},
        }

    # ------------------------------------------------------------------
    # Phase B — scenario-specific sub-checks
    #
    # Generic engine driven by `scenario_checks.by_category` in the rules
    # file. Each category may declare any combination of these sub-rules;
    # only declared ones run. Each sub-rule emits its own check entry so
    # the UI / trace shows which specific rule passed or failed
    # (e.g. `scenario.office_doc.no_placeholder`).
    # ------------------------------------------------------------------
    def _scenario_checks(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        task_category: str | None,
    ) -> list[dict]:
        if not task_category:
            return []
        sc_cfg = self.rules.get("scenario_checks") or {}
        if not sc_cfg.get("enabled", True):
            return []
        cat_rules = (sc_cfg.get("by_category") or {}).get(task_category)
        if not cat_rules:
            return []

        applies = set(sc_cfg.get("applies_to_tools",
                                  ["chat", "docx", "pptx", "xlsx",
                                   "report", "fs"]))
        body = self._collect_prose_body(plan_actions, executions, applies)
        action_by_id = {a.action_id: a for a in plan_actions}
        successful_actions = [
            action_by_id[e.action_id] for e in executions
            if e.status == "success" and e.action_id in action_by_id
        ]

        out: list[dict] = []
        prefix = f"scenario.{task_category}"

        # ---- min_body_chars --------------------------------------------
        min_chars = cat_rules.get("min_body_chars")
        if isinstance(min_chars, int) and min_chars > 0:
            n = len(body)
            out.append({
                "name": f"{prefix}.min_body_chars",
                "pass": n >= min_chars,
                "reason": "ok" if n >= min_chars
                          else f"got_{n}_chars_need_{min_chars}",
                "details": {"min": min_chars, "actual": n},
            })

        # ---- forbid_placeholders ---------------------------------------
        forbidden = cat_rules.get("forbid_placeholders") or []
        if forbidden and body:
            low = body.lower()
            hits = [p for p in forbidden if str(p).lower() in low]
            out.append({
                "name": f"{prefix}.no_placeholder",
                "pass": len(hits) == 0,
                "reason": "ok" if not hits
                          else f"placeholder_found:{hits[0][:40]}",
                "details": {"forbidden": list(forbidden),
                            "hits": hits[:5]},
            })

        # ---- must_contain_any (sources / disclaimer / assumptions …) ---
        # Each entry: {"label": "has_sources",
        #              "any_of": ["sources:", "来源:", "参考资料:"]}
        for spec in cat_rules.get("must_contain_any") or []:
            if not isinstance(spec, dict):
                continue
            label = str(spec.get("label", "must_contain"))
            needles = [str(n).lower() for n in spec.get("any_of", []) if n]
            if not needles:
                continue
            low = body.lower()
            matched = [n for n in needles if n in low]
            out.append({
                "name": f"{prefix}.{label}",
                "pass": bool(matched),
                "reason": "ok" if matched
                          else f"none_of:{','.join(needles[:3])[:80]}",
                "details": {"any_of": needles, "matched": matched[:3]},
            })

        # ---- forbid_phrases (e.g. lawyer-opinion language) -------------
        for spec in cat_rules.get("forbid_phrases") or []:
            if not isinstance(spec, dict):
                continue
            label = str(spec.get("label", "no_forbidden"))
            phrases = [str(p).lower() for p in spec.get("phrases", []) if p]
            if not phrases or not body:
                continue
            low = body.lower()
            hits = [p for p in phrases if p in low]
            out.append({
                "name": f"{prefix}.{label}",
                "pass": len(hits) == 0,
                "reason": "ok" if not hits else f"forbidden:{hits[0][:60]}",
                "details": {"phrases_checked": len(phrases),
                            "hits": hits[:3]},
            })

        # ---- pptx_min_slides -------------------------------------------
        pp_min = cat_rules.get("pptx_min_slides")
        if isinstance(pp_min, int) and pp_min > 0:
            for a in successful_actions:
                if (a.tool or "").lower() != "pptx":
                    continue
                slides = (a.metadata or {}).get("slides") or []
                n = len(slides) if isinstance(slides, list) else 0
                out.append({
                    "name": f"{prefix}.pptx_min_slides",
                    "pass": n >= pp_min,
                    "reason": "ok" if n >= pp_min
                              else f"only_{n}_slides_need_{pp_min}",
                    "details": {"min": pp_min, "actual": n,
                                "action_id": a.action_id},
                })
                break  # check the first pptx action only

        # ---- xlsx_min_rows ---------------------------------------------
        xl_min = cat_rules.get("xlsx_min_rows")
        if isinstance(xl_min, int) and xl_min > 0:
            for a in successful_actions:
                if (a.tool or "").lower() != "xlsx":
                    continue
                sheets = (a.metadata or {}).get("sheets") or {}
                rows = (a.metadata or {}).get("rows") or []
                # Count rows in the first sheet (or top-level `rows`),
                # excluding the header row.
                if isinstance(sheets, dict) and sheets:
                    first = next(iter(sheets.values()))
                    rows = first if isinstance(first, list) else []
                n = max(0, len(rows) - 1) if isinstance(rows, list) else 0
                out.append({
                    "name": f"{prefix}.xlsx_min_rows",
                    "pass": n >= xl_min,
                    "reason": "ok" if n >= xl_min
                              else f"only_{n}_data_rows_need_{xl_min}",
                    "details": {"min": xl_min, "actual": n,
                                "action_id": a.action_id},
                })
                break

        # ---- min_word_count (uses _word_count, CJK-aware) --------------
        min_words = cat_rules.get("min_word_count")
        if isinstance(min_words, int) and min_words > 0 and body:
            wc = self._word_count(body)
            out.append({
                "name": f"{prefix}.min_word_count",
                "pass": wc >= min_words,
                "reason": "ok" if wc >= min_words
                          else f"got_{wc}_words_need_{min_words}",
                "details": {"min": min_words, "actual": wc},
            })

        return out

    # ------------------------------------------------------------------
    # Phase 2 (L4.6) — adapted-skill strict mode
    #
    # When a task was solved via a cross-context ADAPTED skill, hold the
    # output to a higher bar. Two extra sub-checks, both config-driven by
    # scenario_checks._skill_adapted_strict_mode:
    #
    #   (a) strict_length — raise the length_check lower bound by
    #       extra_min_word_count_pct. Skipped silently when the user
    #       didn't request a specific word count (nothing to scale).
    #   (b) target_tool_format_match — REQUIRE that a successful action
    #       used the tool the skill was adapted TO (or that a file of the
    #       matching extension was written). This is the proof that the
    #       adaptation actually produced the new medium — without it an
    #       adapted "make a deck" skill could silently fall back to a
    #       paragraph blob and still pass.
    # ------------------------------------------------------------------
    _TOOL_EXTENSION = {
        "docx": "docx", "pptx": "pptx", "xlsx": "xlsx",
        "report": "docx", "pdf": "pdf",
    }

    def _skill_adapted_strict_checks(
        self,
        user_intent: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        adapted_target_tool: str,
    ) -> list[dict]:
        sc_cfg = self.rules.get("scenario_checks") or {}
        strict_cfg = sc_cfg.get("_skill_adapted_strict_mode") or {}
        if not strict_cfg.get("enabled", True):
            return []

        out: list[dict] = []
        prefix = "scenario.skill_adapted"
        target_tool = (adapted_target_tool or "").lower()

        # ---- (a) strict length floor ----------------------------------
        pct = float(strict_cfg.get("extra_min_word_count_pct", 1.0) or 1.0)
        target_words = self._extract_target_words(user_intent)
        if target_words is not None and pct > 1.0:
            len_cfg = self.rules.get("length_check") or {}
            applies = set(len_cfg.get("applies_to_tools",
                                      ["chat", "docx", "report", "fs"]))
            min_ratio = float(len_cfg.get("min_ratio", 0.5))
            strict_floor = int(target_words * min_ratio * pct)
            body = self._collect_prose_body(plan_actions, executions, applies)
            actual = self._word_count(body) if body else 0
            out.append({
                "name": f"{prefix}.strict_length",
                "pass": actual >= strict_floor,
                "reason": "ok" if actual >= strict_floor
                          else f"adapted_too_short:got_{actual}_"
                               f"need_{strict_floor}",
                "details": {"target_words": target_words,
                            "strict_floor": strict_floor,
                            "actual_words": actual,
                            "extra_pct": pct},
            })

        # ---- (b) target-tool format match -----------------------------
        if strict_cfg.get("require_target_tool_format_match", True) \
                and target_tool:
            ok, evidence = self._target_tool_produced(
                target_tool, plan_actions, executions)
            out.append({
                "name": f"{prefix}.target_tool_format_match",
                "pass": ok,
                "reason": "ok" if ok
                          else f"no_{target_tool}_artifact_produced",
                "details": {"target_tool": target_tool,
                            "evidence": evidence},
            })

        return out

    def _target_tool_produced(
        self,
        target_tool: str,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> tuple[bool, str]:
        """True iff a successful execution actually produced an artifact
        in the adapted target tool's medium. Checks (1) a successful
        action whose tool == target_tool with real metadata, then
        (2) an affected file with the matching extension."""
        action_by_id = {a.action_id: a for a in plan_actions}
        ext = self._TOOL_EXTENSION.get(target_tool, target_tool)

        for e in executions:
            if e.status != "success":
                continue
            a = action_by_id.get(e.action_id)
            if a is None:
                continue
            tool = (a.tool or "").lower()
            meta = a.metadata or {}

            # (1) tool match with non-trivial content
            if tool == target_tool:
                if target_tool == "pptx":
                    slides = meta.get("slides") or []
                    if isinstance(slides, list) and slides:
                        return True, f"pptx_action:{len(slides)}_slides"
                elif target_tool == "xlsx":
                    sheets = meta.get("sheets") or {}
                    rows = meta.get("rows") or []
                    if (isinstance(sheets, dict) and sheets) or \
                       (isinstance(rows, list) and rows):
                        return True, "xlsx_action:has_data"
                else:  # docx / report / fs prose
                    body = (meta.get("body") or meta.get("content")
                            or e.output_summary or "")
                    if str(body).strip():
                        return True, f"{target_tool}_action:has_body"

            # (2) affected file with the matching extension
            for path in (e.affected_resources or []):
                try:
                    if Path(path).suffix.lower().lstrip(".") == ext:
                        return True, f"file:{Path(path).name}"
                except Exception:
                    continue

        return False, "none"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_target_words(self, text: str) -> int | None:
        if not text:
            return None
        for rx in self._compiled_word_patterns:
            m = rx.search(text)
            if m:
                try:
                    n = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                # sanity clamp — "1 word" or "100000 words" both off-base
                if 20 <= n <= 20000:
                    return n
        return None

    def _extensions_from_intent(self, text: str) -> list[str]:
        if not text:
            return []
        out: list[str] = []
        clauses = [
            part.strip() for part in re.split(r"[.!?;\n。！？；]+", text)
            if part.strip()
        ]
        output_request = re.compile(
            r"\b(?:create|make|generate|prepare|produce|build|write|export|"
            r"save|edit|update|populate|fill|convert|turn|put|give me|need|want)\b"
            r"|(?:生成|制作|建立|创建|写|做成|做|准备|整理成|输出|另存为|请做)",
            re.IGNORECASE,
        )
        for ext, rx in self._compiled_ext_patterns.items():
            # A format word can describe source evidence rather than the
            # requested output (for example, "a spreadsheet was emailed to the
            # wrong vendor"). Require an output-making cue in the same clause.
            if any(rx.search(clause) and output_request.search(clause)
                   for clause in clauses):
                out.append(ext)
        # De-dup preserving order
        seen: set[str] = set()
        result: list[str] = []
        for e in out:
            if e not in seen:
                seen.add(e)
                result.append(e)
        return result

    @staticmethod
    def _collect_artifact_content(
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
    ) -> str:
        """Build a unified text representation of EVERY artifact the
        agent produced, for the LLM judge to evaluate. Unlike
        `_collect_prose_body` (which only captures chat / docx prose
        bodies), this serialises pptx slide structure and xlsx sheet
        structure too — so the judge sees real content for office tasks
        instead of just the short chat companion.

        Reads from action.metadata (fast; also works in tests where the
        file may not be present on disk).
        """
        action_by_id = {a.action_id: a for a in plan_actions}
        parts: list[str] = []
        for e in executions:
            if e.status != "success":
                continue
            a = action_by_id.get(e.action_id)
            if a is None:
                continue
            tool = (a.tool or "").lower()
            meta = a.metadata or {}

            if tool == "chat":
                text = str(e.output_summary or meta.get("body") or "").strip()
                if text:
                    parts.append(f"[chat reply]\n{text}")

            elif tool in {"docx", "report", "fs"}:
                title = str(meta.get("title") or "").strip()
                body = str(meta.get("body") or meta.get("content")
                           or e.output_summary or "").strip()
                if not (title or body):
                    continue
                head = f"[{tool} document"
                if title:
                    head += f" — title: {title[:120]}"
                head += "]"
                if meta.get("school_output_contract"):
                    head = head[:-1] + (
                        f"; target={Path(a.target).name}; "
                        f"artifact_id={meta.get('artifact_id')}; "
                        f"role={meta.get('artifact_role')}; "
                        f"audience={meta.get('audience')}]"
                    )
                parts.append(f"{head}\n{body[:4000]}")

            elif tool == "pptx":
                title = str(meta.get("title") or "").strip()
                slides = meta.get("slides") or []
                if not isinstance(slides, list) or not slides:
                    continue
                lines = [f"[pptx deck — title: {title[:120]}, "
                         f"{len(slides)} slides]"]
                for i, s in enumerate(slides[:12], 1):
                    if not isinstance(s, dict):
                        continue
                    st = str(s.get("title") or "").strip()
                    bullets = s.get("bullets") or []
                    lines.append(f"  Slide {i}: {st[:120]}")
                    if isinstance(bullets, list):
                        for b in bullets[:6]:
                            lines.append(f"    - {str(b)[:200]}")
                parts.append("\n".join(lines))

            elif tool == "xlsx":
                sheets = meta.get("sheets") or {}
                rows = meta.get("rows")
                if isinstance(sheets, dict) and sheets:
                    lines = ["[xlsx workbook]"]
                    for sheet_name, sheet_rows in list(sheets.items())[:5]:
                        if not isinstance(sheet_rows, list):
                            continue
                        lines.append(
                            f"  Sheet {sheet_name!r} "
                            f"({len(sheet_rows)} rows):"
                        )
                        for r in sheet_rows[:8]:
                            if isinstance(r, list):
                                lines.append(
                                    "    | "
                                    + " | ".join(str(c)[:30] for c in r)
                                    + " |"
                                )
                    parts.append("\n".join(lines))
                elif isinstance(rows, list) and rows:
                    lines = [f"[xlsx — {len(rows)} rows]"]
                    for r in rows[:8]:
                        if isinstance(r, list):
                            lines.append(
                                "    | "
                                + " | ".join(str(c)[:30] for c in r)
                                + " |"
                            )
                    parts.append("\n".join(lines))

            elif tool == "image_gen":
                # Judge doesn't see images; surface that one was produced
                # plus the prompt so it can score "did we generate the
                # requested image" qualitatively.
                prompt = str(meta.get("prompt") or "").strip()
                affected = [str(p) for p in (e.affected_resources or [])]
                if prompt or affected:
                    parts.append(
                        f"[image generated]\n"
                        f"  prompt: {prompt[:300]}\n"
                        f"  files: {', '.join(affected[:3])}"
                    )

        return "\n\n".join(parts)

    @staticmethod
    def _collect_prose_body(
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        applies_to_tools: set[str],
    ) -> str:
        """Concatenate prose output from successful executions of the
        configured tools. For docx we read metadata.body / metadata.content
        from the action (cheaper than opening the file); for chat we use
        execution.output_summary directly."""
        action_by_id = {a.action_id: a for a in plan_actions}
        bodies: list[str] = []
        for e in executions:
            if e.status != "success":
                continue
            a = action_by_id.get(e.action_id)
            if a is None:
                continue
            tool = (a.tool or "").lower()
            if tool not in applies_to_tools:
                continue
            if tool == "chat":
                # chat surfaces the answer text in output_summary
                bodies.append(e.output_summary or "")
            else:
                # docx/report/fs: planner wrote the body in metadata
                meta = a.metadata or {}
                body = (meta.get("body") or meta.get("content")
                        or e.output_summary or "")
                bodies.append(str(body))
        return "\n\n".join(b for b in bodies if b)

    @staticmethod
    def _word_count(text: str) -> int:
        """Count words. Three regimes:
          - dominantly CJK (CJK chars >> tokens): return CJK char count
            ('500字' = 500 Chinese characters)
          - dominantly Latin: return token count
          - MIXED CJK+Latin (S-patch fix): count Latin-only tokens
            PLUS CJK chars separately and sum them. Without this, the
            web-grounded recovery body (Chinese intro + English snippets
            + Latin URLs) under-counted: neither regime won and we fell
            back to tokens — losing all the Chinese content's weight.
        """
        if not text:
            return 0
        tokens = re.findall(r"\S+", text)
        cjk = re.findall(r"[㐀-鿿豈-﫿]", text)
        if cjk and len(cjk) > len(tokens) * 2:
            return len(cjk)
        if cjk:
            latin_tokens = sum(
                1 for t in tokens
                if not re.search(r"[㐀-鿿豈-﫿]", t)
            )
            return latin_tokens + len(cjk)
        return len(tokens)

    # ------------------------------------------------------------------
    # Phase 14 — LLM-as-judge. Catches semantic failures (agent wrote
    # 500 words but it's gibberish) that the three mechanical checks
    # above can't see. Returns a structured opinion the runtime uses
    # to decide whether to fire the self-fix loop.
    # ------------------------------------------------------------------
    def llm_judge(
        self,
        *,
        envelope: TaskEnvelope,
        plan_actions: list[CandidateAction],
        executions: list[ExecutionResult],
        final_route: str,
        task_category: str = "",
    ) -> dict:
        """Ask an LLM to score the output against per-category rubrics.

        Returns:
            {
              "enabled": bool,
              "pass": bool | None,        # None = skipped
              "score": int (0-100),
              "threshold": int,
              "issues":      [str, ...],
              "suggestions": [str, ...],
              "rubric_used": str,         # category key
              "summary": "<short>",
              "skipped_reason": "..."     # only when pass=None
            }

        Never raises — judge errors fall back to skipped.
        """
        out = {
            "enabled": True, "pass": None, "score": 0, "threshold": 0,
            "issues": [], "suggestions": [],
            "rubric_used": "default", "summary": "",
        }

        cfg = self.rules.get("llm_judge") or {}
        if not cfg.get("enabled", True):
            out["enabled"] = False
            out["summary"] = "llm_judge_disabled"
            out["skipped_reason"] = "disabled_in_config"
            return out
        if self.chat_llm is None:
            out["skipped_reason"] = "no_chat_llm"
            out["summary"] = "llm_judge_skipped:no_chat_llm"
            return out

        school_semantics = (envelope.metadata or {}).get(
            "school_semantics") or {}
        if (envelope.metadata or {}).get("response_pack_mode") == "delta":
            # The parent task already owns the original requested pack. A
            # delta child is intentionally scoped to one user-added file, so a
            # general judge comparing it with the full parent request will
            # wrongly demand all parent siblings again. Contract, grounding
            # and role checks still run mechanically for the added artifact.
            out["skipped_reason"] = "school_response_pack_delta"
            out["summary"] = "llm_judge_skipped:school_response_pack_delta"
            return out
        if (
            school_semantics.get("checked") is True
            and school_semantics.get("school_domain") is False
            and str(school_semantics.get("case_relation") or "").lower()
            == "unrelated"
        ):
            # The correct product behaviour is a capability boundary, not a
            # report about the unrelated topic. A generic quality judge would
            # otherwise call the intentional boundary a refusal and trigger
            # two pointless self-fix retries.
            out["skipped_reason"] = "school_domain_boundary"
            out["summary"] = "llm_judge_skipped:school_domain_boundary"
            return out

        # Exempt routes (RED/INFEASIBLE) — a refusal IS the right answer
        exempt = {r.upper() for r in cfg.get("judge_exempt_routes", []) or []}
        if (final_route or "").upper() in exempt:
            out["skipped_reason"] = f"route_exempt:{final_route}"
            out["summary"] = out["skipped_reason"]
            return out

        # Category opt-in: empty list = on for all categories
        enabled_cats = cfg.get("judge_enabled_categories") or []
        if enabled_cats and task_category not in enabled_cats:
            out["skipped_reason"] = f"category_not_enabled:{task_category}"
            out["summary"] = out["skipped_reason"]
            return out

        # Q1 fix — judge needs to see ALL artifacts, not just prose body.
        # Before: `_collect_prose_body` only returned chat/docx/report/fs
        # text. For pptx/xlsx tasks that meant the judge saw only the
        # ~80-char chat companion ("我为你做了一份…") and concluded the
        # answer was "just a download link with no content". Now we also
        # serialize pptx structure (slide titles + bullets) and xlsx
        # structure (sheet name + headers + first rows) into the judge
        # brief — so the judge evaluates the REAL artifact content.
        artifact_content = self._collect_artifact_content(
            plan_actions, executions)
        if not artifact_content:
            out["skipped_reason"] = "no_judgeable_output"
            out["summary"] = out["skipped_reason"]
            return out

        rubric_key = (
            "school_governed_markdown"
            if is_school_output_contract(plan_actions)
            else task_category
        )
        rubric = self._rubric_for(rubric_key)
        out["rubric_used"] = rubric["_key"]
        threshold = int(rubric.get("pass_threshold", 60))
        out["threshold"] = threshold

        # Build prompt for the judge LLM
        call_cfg = self.rubrics.get("judge_call") or {}
        max_tokens = int(call_cfg.get("max_tokens", 600))

        system_prompt = self._judge_system_prompt(rubric, threshold)
        user_prompt = self._judge_user_prompt(
            envelope.normalized_goal or envelope.raw_goal, artifact_content)

        try:
            decision = self.chat_llm.chat_json(
                system=system_prompt, user=user_prompt, max_tokens=max_tokens,
            )
        except Exception as exc:
            out["skipped_reason"] = f"judge_error:{exc}"
            out["summary"] = out["skipped_reason"]
            return out
        if not isinstance(decision, dict) or not decision:
            out["skipped_reason"] = "judge_empty_or_malformed"
            out["summary"] = out["skipped_reason"]
            return out

        # Clamp + extract fields the judge sent
        try:
            score = int(decision.get("score", 0))
        except (ValueError, TypeError):
            score = 0
        score = max(0, min(100, score))
        out["score"] = score
        out["issues"] = [str(i)[:200] for i in (decision.get("issues") or [])][:8]
        out["suggestions"] = [str(s)[:200] for s in (decision.get("suggestions") or [])][:8]
        out["pass"] = score >= threshold
        out["summary"] = (f"judge_pass:score={score}/{threshold}"
                          if out["pass"] else
                          f"judge_fail:score={score}/{threshold}:"
                          + ";".join(out["issues"])[:200])
        return out

    # ------------------------------------------------------------------
    # Judge helpers
    # ------------------------------------------------------------------
    def _rubric_for(self, task_category: str) -> dict:
        """Look up the per-category rubric, falling back to 'default'."""
        if task_category and task_category in self.rubrics:
            entry = self.rubrics.get(task_category) or {}
            if isinstance(entry, dict) and entry.get("criteria"):
                return {**entry, "_key": task_category}
        default = self.rubrics.get("default") or {}
        return ({**default, "_key": "default"} if default else
                {"_key": "default", "criteria": [], "pass_threshold": 60})

    @staticmethod
    def _judge_system_prompt(rubric: dict, threshold: int) -> str:
        criteria = rubric.get("criteria") or []
        bullets = "\n".join(f"- {c}" for c in criteria)
        return (
            "You are Module 110's LLM judge. Score how well an agent's "
            "output satisfies a user's goal.\n\n"
            f"Criteria (score AGAINST these):\n{bullets}\n\n"
            f"Scoring: integer 0-100. Pass threshold: {threshold}.\n"
            "Return ONE JSON object only:\n"
            "{\n"
            '  "score":       <int 0-100>,\n'
            '  "issues":      ["short bullet of what is wrong", ...],\n'
            '  "suggestions": ["short bullet of how to fix it next try", ...],\n'
            '  "reasoning":   "one sentence overall"\n'
            "}\n"
            "Be fair: a reasonable answer for the question deserves a "
            "pass even if not perfect. Be strict: gibberish or refusals "
            "that should have answered get a fail."
        )

    # P7 — explicit-language cues. The judge was observed hallucinating a
    # "should have been in Chinese" failure on an English-only prompt
    # (the requirement leaked from memory/context, not the request). We
    # detect a language requirement DETERMINISTICALLY from the goal text
    # and tell the judge to score language ONLY when one is actually asked
    # for. Pure regex, no LLM.
    _LANG_PATTERNS = {
        "Chinese": re.compile(
            r"in chinese|in mandarin|用中文|用中文回答|中文(?:回答|撰写|输出|版本|作答)"
            r"|译成中文|翻译成中文|以中文", re.IGNORECASE),
        "English": re.compile(
            r"in english|用英(?:文|语)|英文(?:回答|撰写|输出|版本)|译成英文"
            r"|翻译成英文|以英文", re.IGNORECASE),
    }

    @classmethod
    def _detect_requested_language(cls, goal: str) -> str:
        """Return "Chinese" / "English" only when the goal EXPLICITLY
        asks for that output language; "" when no language is specified.
        Order: check each pattern; first explicit cue wins."""
        if not goal:
            return ""
        for lang, pat in cls._LANG_PATTERNS.items():
            if pat.search(goal):
                return lang
        return ""

    @classmethod
    def _judge_user_prompt(cls, goal: str, output: str) -> str:
        requested_lang = cls._detect_requested_language(goal)
        if requested_lang:
            lang_rule = (
                f"Output language: the user explicitly asked for "
                f"{requested_lang}. Penalise if the output is not in "
                f"{requested_lang}."
            )
        else:
            lang_rule = (
                "Output language: the user did NOT specify one. Do NOT "
                "penalise the output for the language it is written in — "
                "judge content and correctness only, never assume a "
                "language requirement from memory or context."
            )
        return (
            f"User's goal:\n{goal}\n\n"
            f"Agent's output (may include section markers like "
            f"[chat reply] / [docx document] / [pptx deck] / "
            f"[xlsx workbook] / [image generated] — judge ALL of them "
            f"together against the goal):\n{output[:8000]}\n\n"
            "Score based on whether the produced ARTIFACTS satisfy the "
            "user's request. A short chat companion paired with a real "
            "document is fine — score the document, not the chat.\n"
            f"{lang_rule}\n"
            "Reply JSON only."
        )
