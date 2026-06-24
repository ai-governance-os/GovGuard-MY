# Baseline & Workflow-Patch Notes

## Branch / build
- **Branch:** `10.7.4-workflow-autonomy`
- **Baseline build:** GovGuard MY 10.7.4-MAIC-RC1 (copied from `GovGuard_提交版_GitHub`, the final pre-workflow MAIC RC)
- **Working folder:** `GovGuard_Workflow_测试版_本机` (own venv + git; the original `GovGuard_提交版_GitHub` stays untouched as the 949-green submittable fallback, per §M3)
- **Test default:** `smart_mock`, no API key (Tier 1; §K). All numbers below measured with provider keys UNSET.

## Baseline measurement (BEFORE patch)
```
pytest --collect-only : 950 collected   (= 949 passed + 1 skipped)
pytest                : 949 passed, 1 skipped, 0 failed   ← GREEN
```
- Matches the brief's M0/A1 expected baseline exactly.
- Intentional skip (1): `tests/test_qpatch.py:256` — documented intentional-precision case.

## After patch
- pytest after **Phase 3** (102W integrated): **957 passed, 1 skipped, 0 failed** (= 949 + 8).
- pytest after **Phase 5** (101D integrated): **962 passed, 1 skipped, 0 failed** (= 949 + 13).
- pytest after **Phase 9** (workflow build): **964 passed, 1 skipped, 0 failed** (965 collected; = 949 + 15).
- pytest after **V2 critique fixes** (this folder): **969 passed, 1 skipped, 0 failed** (970 collected; = 949 + 20).
- pytest after **V2 live-key fixes**: **972 passed, 1 skipped, 0 failed** (973 collected; = 949 + 23).
- pytest after **readability hotfix**: **976 passed, 1 skipped, 0 failed** (977 collected; = 949 + 27).
- pytest after **National Athletics workflow** (curated/live two-tier + demo coherence): **989 passed, 1 skipped, 0 failed** (990 collected; = 949 + 40).
- pytest after **post-main-demo governance probes** (user-input governance on the same dataset + persistent demo dock): **1001 passed, 1 skipped, 0 failed** (1002 collected; = 949 + 52).
- Offline governance eval: **pass rate 1.0** (37 evaluated, 3 documented L2 skips) — +2 national + 6 probe cases.
- New workflow tests: `tests/test_workflow_autonomy.py` — 39 (National Athletics suite). Probe tests: `tests/test_post_main_demo_governance_probes.py` — 12 (BLUE/RED×2/GREEN/INFEASIBLE routes, no-side-effect for blocked probes, not-a-workflow, CJK reword-proof RED, over-fire guards). Two new reword-proof `risk_rules` (status_based_differential → RED; unsupported_amount_estimate → INFEASIBLE).
- New intentional skips: none.

> Doc-number correction: the shipped docs previously claimed **932/933** — stale
> from before the FIX_PLAN added ~17 tests without updating them. All judge docs
> are now corrected to the measured **972/973** (pre-workflow baseline 949 noted).

## V2 live-key fixes (after a real GPT-4o run drifted: slow / web-polluted / generic)
A live-key run of the headline workflow ran ~1 min, showed DuckDuckGo web sources +
generic sports-day advice, and produced ungrounded drafts; and the income self-block
was logically floating (the workflow had no parent-communication action). Fixed:
- **Workflow ownership (P0-a):** web search is gated on `workflow_plan is None`
  (`runtime.py`) — once 102W matches, no unsolicited search / web-context injection.
  Generic tasks keep web search. (Test: `test_workflow_skips_web_search`.)
- **Latency (P0-b):** 102W flags non-deliverable steps `workflow_template_only`
  (report-stub, RED self-block, GREEN release); the synthesizer short-circuits them
  with deterministic templates (no LLM). Content drafts capped at 1200 tokens →
  3 live calls (was ~5×4000) + no web. (Test: `test_workflow_status_steps_use_template_not_llm`.)
- **Narrowed synthesis (P0-c):** `_synth_workflow_text` — local context only, no web,
  no inventing, sensitive-data rules, "do not touch the route".
- **Grounding (P1):** `Runtime._attach_workflow_context` reads `workspace/results.md`
  once; internal steps get the full data, public-facing steps only the delimited
  `## Public Summary`. Both tiers (template + live model) cite real facts. (Test:
  `test_workflow_outputs_grounded_in_results`.) Source shown in the panel (P1-b).
- **Logical coherence (P2):** workflow redesigned to 7 steps — added a REAL
  `draft_parent_congrats_notice` (BLUE) so the income self-block governs an action
  the workflow actually performs. Routes BLUE×5 + RED + GREEN. Richer
  `demo/sports_day_results.md` (date/venue/programme/full events/standings/attendance
  + a public-safe summary block). The live GPT-4o output quality remains owner-verified
  (the offline stub proves wiring + grounding + the call cap).

## V2 critique fixes (after a mentor review of the natural-input behaviour)
A reviewer found the flagship self-governance claim was real in unit tests but
did NOT fire on natural UI input (101D term lists were underscore/metadata-shaped;
the natural-language path never tagged data-use metadata, so 101D stayed inert and
the route came from the P2.2 GREEN-failsafe, not a 101D RED). Fixed in two segments:
- **Seg 1 (deterministic, A-tier):** 101D now normalizes text (underscore/space,
  CJK) + a natural-language socio/PII/health lexicon, and inspects the threaded
  `user_intent` — so guardian-income free text (EN + 中文) routes RED end-to-end.
  AND-socio+differential gate kept tight (no new false REDs). Added an in-workflow
  self-block step (`consider_income_personalisation`) so the headline workflow
  itself shows the agent blocking its own income-based personalisation (RED).
  Seeded `demo/sports_day_results.md` (+ server seeds `workspace/results.md`) so
  step 1 no longer shows `not_found`.
- **Seg 2 (C-tier + UI + docs):** added a gated **LLM understanding layer**
  (`DataUseGuard.understand` + `Runtime._understand_data_use`) — when a live model
  is present and the lexicon is uncertain, GPT-4o labels the request with
  closed-vocabulary data-use concepts that feed 101D's *deterministic* rules (the
  model never decides the route; no key → lexicon + fail-safe). Option-2 UI: a
  workflow-aware route chip + panel status line ("4 auto · 1 approval · 1
  self-blocked") so a self-blocked step reads as governed, not failed. Docs aligned.

### Phase log
- **Phase 1** — `configs/workflows/public_school/post_event_reporting.json` (§5, verbatim; tool/op hints verified against `tool_catalog.json`).
- **Phase 2** — `teow_agl/modules/module_102w_workflow_resolver.py` (§6: offline strong-phrase / anchor+cue matching, never on cue alone, never on sub-goals; `build_plan` → CandidatePlan with full workflow metadata). Unit-tested directly.
- **Phase 3** — runtime integration (§E): resolver init in `__init__` (graceful degrade); 102W resolver runs BEFORE the Phase-13 task-tree fork; fork gets the `workflow_plan is None` guard; `plan_from_cache = workflow_plan` + `102 planner_skipped`. Verified: CN/EN goals fire 102W, planner skipped (never called), task tree cannot steal a workflow task.
- **Phase 4** — `teow_agl/modules/module_101d_data_use_guard.py` (§8/§F/§I): deterministic self-governance over the agent's OWN data use. Inert by default (no metadata + no obvious sensitive use → NO_OVERRIDE). RED on socio+differential / public-PII / health-in-public; GREEN on external release or unclear sensitive use; NO_OVERRIDE on internal/draft. `_obvious_sensitive_use` kept narrow so legacy actions are never perturbed.
- **Phase 5** — integrated 101D into `_execute_actions` (§8): runs before 101B; inert default; RED short-circuit builds one decision + `_on_red` (no pre-append → exactly one RED, §H); GREEN elevation after 101B raises BLUE→GREEN only (§G). Verified vs §19 expected: internal/draft steps BLUE, external release GREEN; flagship guardian-income action → RED once with the §I reason + safe alternative, blocked before execution. 103 honors `risk.recommended_route` (line 89) so the GREEN elevation works on this build.
- **Phase 6** — trace/UI data: server builds a `workflow` view (`server/app.py _workflow_view`) from the result — per-step route + status + data-use decision, plus any 101D self-blocked action — round-tripped through `_state_to_dict` + persistence like `task_tree`.
- **Phase 7** — UI: `renderWorkflowPanel` + a teal **Workflow panel** beside the governance card (each step + route + status + note, and the RED self-block with reason + safe alternative) + a `WORKFLOW n` chip (`static/app.js`, `static/style.css`). Additive; ordinary tasks show nothing new.
- **Phase 8** — docs (§M-docs): repositioned README_MAIC + DEMO_SCRIPT + JUDGE_GUIDE to lead with the workflow (the §I flagship RED appears in all three); CLAIMS_CHECK + AI_DISCLOSURE updated (one-workflow claim discipline, real-LLM vs smart_mock); all test-count claims corrected 932→964.
- **Phase 9** — output quality (§C): the 102B synthesizer now has a workflow-aware fallback (`_workflow_fallback_body`) so zero-key steps produce real bilingual drafts, never an apology; `build_plan` emits absolute outputs targets (a bare filename resolved to CWD and was denied by the FilesystemTool). Eyeballed: 4 BLUE + 1 GREEN, two substantive deliverables written (internal report ~600 chars, FB post ~500 chars). Full suite green; evals 1.0.

## Hard rules being followed (from the brief)
- Additive only; never rewrite. Existing runtime/modules/contracts preserved.
- Every workflow action still flows 101B → 103 → 105/107 (never bypass `_execute_actions`).
- Data Use Guard inert (`NO_OVERRIDE`) for any action without workflow/data-use metadata (§F).
- Task-tree fork gets the `workflow_plan is None` guard so it cannot steal a workflow task (§E).
- RED short-circuit does NOT pre-append before `_on_red` (exactly one RED decision) (§H).
- Only tools/operations present in `configs/tool_catalog.json`.
- `smart_mock` stays the default; pytest/CI never depend on a real API key (§K).
