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
- New workflow tests: `tests/test_workflow_autonomy.py` — 13 total (5 resolver-direct, 3 102W integration, 5 101D / data-use).
- New intentional skips: none.

### Phase log
- **Phase 1** — `configs/workflows/public_school/post_event_reporting.json` (§5, verbatim; tool/op hints verified against `tool_catalog.json`).
- **Phase 2** — `teow_agl/modules/module_102w_workflow_resolver.py` (§6: offline strong-phrase / anchor+cue matching, never on cue alone, never on sub-goals; `build_plan` → CandidatePlan with full workflow metadata). Unit-tested directly.
- **Phase 3** — runtime integration (§E): resolver init in `__init__` (graceful degrade); 102W resolver runs BEFORE the Phase-13 task-tree fork; fork gets the `workflow_plan is None` guard; `plan_from_cache = workflow_plan` + `102 planner_skipped`. Verified: CN/EN goals fire 102W, planner skipped (never called), task tree cannot steal a workflow task.
- **Phase 4** — `teow_agl/modules/module_101d_data_use_guard.py` (§8/§F/§I): deterministic self-governance over the agent's OWN data use. Inert by default (no metadata + no obvious sensitive use → NO_OVERRIDE). RED on socio+differential / public-PII / health-in-public; GREEN on external release or unclear sensitive use; NO_OVERRIDE on internal/draft. `_obvious_sensitive_use` kept narrow so legacy actions are never perturbed.
- **Phase 5** — integrated 101D into `_execute_actions` (§8): runs before 101B; inert default; RED short-circuit builds one decision + `_on_red` (no pre-append → exactly one RED, §H); GREEN elevation after 101B raises BLUE→GREEN only (§G). Verified vs §19 expected: internal/draft steps BLUE, external release GREEN; flagship guardian-income action → RED once with the §I reason + safe alternative, blocked before execution. 103 honors `risk.recommended_route` (line 89) so the GREEN elevation works on this build.

## Hard rules being followed (from the brief)
- Additive only; never rewrite. Existing runtime/modules/contracts preserved.
- Every workflow action still flows 101B → 103 → 105/107 (never bypass `_execute_actions`).
- Data Use Guard inert (`NO_OVERRIDE`) for any action without workflow/data-use metadata (§F).
- Task-tree fork gets the `workflow_plan is None` guard so it cannot steal a workflow task (§E).
- RED short-circuit does NOT pre-append before `_on_red` (exactly one RED decision) (§H).
- Only tools/operations present in `configs/tool_catalog.json`.
- `smart_mock` stays the default; pytest/CI never depend on a real API key (§K).
