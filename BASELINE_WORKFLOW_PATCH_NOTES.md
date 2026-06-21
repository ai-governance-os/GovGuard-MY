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
- pytest after **Phase 3** (102W integrated): **957 passed, 1 skipped, 0 failed** (= 949 + 8 new).
- New workflow tests added: `tests/test_workflow_autonomy.py` — 8 so far (5 resolver-direct + 3 runtime integration). 101D routing tests added in Phase 5.
- New intentional skips: none.

### Phase log
- **Phase 1** — `configs/workflows/public_school/post_event_reporting.json` (§5, verbatim; tool/op hints verified against `tool_catalog.json`).
- **Phase 2** — `teow_agl/modules/module_102w_workflow_resolver.py` (§6: offline strong-phrase / anchor+cue matching, never on cue alone, never on sub-goals; `build_plan` → CandidatePlan with full workflow metadata). Unit-tested directly.
- **Phase 3** — runtime integration (§E): resolver init in `__init__` (graceful degrade); 102W resolver runs BEFORE the Phase-13 task-tree fork; fork gets the `workflow_plan is None` guard; `plan_from_cache = workflow_plan` + `102 planner_skipped`. Verified: CN/EN goals fire 102W, planner skipped (never called), task tree cannot steal a workflow task.

## Hard rules being followed (from the brief)
- Additive only; never rewrite. Existing runtime/modules/contracts preserved.
- Every workflow action still flows 101B → 103 → 105/107 (never bypass `_execute_actions`).
- Data Use Guard inert (`NO_OVERRIDE`) for any action without workflow/data-use metadata (§F).
- Task-tree fork gets the `workflow_plan is None` guard so it cannot steal a workflow task (§E).
- RED short-circuit does NOT pre-append before `_on_red` (exactly one RED decision) (§H).
- Only tools/operations present in `configs/tool_catalog.json`.
- `smart_mock` stays the default; pytest/CI never depend on a real API key (§K).
