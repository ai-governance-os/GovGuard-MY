# Claims Check — tiered, reproducible evidence

**GovGuard MY 10.7.4-MAIC-RC1** · Powered by TEOW-AGL Governance Runtime

Every numeric claim is presented in **three separate tiers**. Tiers are never
merged, and no number is carried that the build it names cannot reproduce.

> **Measurement environment matters.** All pytest numbers below are measured
> with provider keys **unset** (`OPENAI_API_KEY`, etc.) — the same zero-key
> environment a judge or CI runner has. With a real `OPENAI_API_KEY` present,
> the skill-ranking test additionally exercises the live embeddings lane (real
> network calls); the clean-env result is the one we claim.

## Tier 1 — Internal full sandbox build (private, fuller TEOW-AGL)
The public artifact is carved from a fuller internal TEOW-AGL build that
contains additional un-published modules and tests. That build is the owner's
and is **measured separately on the private tree; it is not reproducible from
this public repository**, so no specific count is claimed here for it (Owner
Rule 6). The reproducible lineage anchor is Tier 2 below: this public build is
not test-trimmed relative to the 10.7.4 spine it derives from.

## Tier 2 — Public MAIC evaluation build (this repository)
The exact, reproducible result of the public surface, in a clean zero-key env:
- **pytest: 989 passed / 1 skipped / 0 failed** (990 collected).
  - This includes the Workflow Autonomy layer (102W/101D) and its 39 tests in
    `tests/test_workflow_autonomy.py` (the post-event reporting workflow plus the
    National Athletics reporting workflow with its rich-DB field-selection,
    self-block, and two-tier curated/live drafting). The pre-workflow baseline on
    this same tree was 949 passed / 1 skipped / 0 failed (950 collected).
  - The 1 skip is documented (`tests/test_qpatch.py:256` — an intentional
    precision case).
- **Task 5 fix is real and verified.** Before it, a clean-env run *failed* on
  `test_find_relevant_active_outranks_equal_stale`: the BM25 retrieval lane
  computed the lifecycle status weight but never re-sorted by it, so an active
  skill did not outrank an equal-but-stale one. The fix sorts the BM25 results
  by weighted score (active=1.0 > stale=0.5), matching the cosine lane. After
  the fix: 0 failed. The assertion was **fixed, not deleted**.
- **Secret scan: PASS** (`python -X utf8 scripts/verify_no_secrets.py`).
- Reproduce (zero keys):
  ```bash
  pip install -e ".[dev]"
  python -X utf8 -m pytest -q
  python -X utf8 scripts/verify_no_secrets.py
  ```

## Tier 3 — Offline governance eval
Behaviour eval over the seed + public-school cases (offline, `smart_mock`):
- **pass rate 1.0** — 34 cases: 31 evaluated (0 failed), 3 skipped (L2 cases that
  require a live semantic classifier; skipped offline by design, never failed).
- Reproduce: `python -X utf8 scripts/run_evals.py`.

## Verified (true of this build)
- Independent governance runtime; planner cannot self-authorise.
- 4-route classifier (BLUE / GREEN / RED / INFEASIBLE) demonstrated by demo
  Flows A–E.
- HMAC-signed ticket; no GREEN execution without a ticket
  (`tests/test_signed_ticket_contract.py`).
- Per-task audit-trace contract for every route
  (`tests/test_audit_trace_contract.py`).
- Additive-only domain packs; a pack cannot remove base governance
  (`tests/test_domain_pack_activation.py`).
- Governance↔learning boundary: student personal data excluded from learning
  (demo Flow D; `public_school` learning-exclusion pack).
- Demo-mode lockout: zero real external actions when `MAIC_DEMO_MODE=1`
  (external tools are mock; execution simulated and labelled).
- **Workflow Autonomy (one configured workflow demonstrated).** Implemented and
  demoed: one configured `post_event_reporting` workflow. Mechanism: a
  config-driven workflow resolver (102W) + workflow metadata + the *normal*
  governance pipeline (101B → 103 → 105/107) + a data-use guard (101D). The 7-step
  flow: low-risk drafting auto-runs (BLUE) — internal report, public Facebook post,
  and a real parent-congratulation notice; the agent then self-blocks personalising
  that parent outreach by family income (RED); and queuing the parent notice + post
  for release needs human approval (GREEN). The same RED also fires on free-text
  input (EN + 中文). Outputs are grounded in a local `workspace/results.md` (cited
  in the panel); a workflow-owned task runs **no web search**. Extensible by adding
  more templates under `configs/workflows/`.
- **Understanding vs deciding (separated layers).** Free-form requests are
  *understood* by a deterministic concept lexicon offline, or by GPT-4o labelling
  closed-vocabulary data-use concepts when a key is present (gated: at most one
  call per task, only when the lexicon is uncertain). The route is *decided* only
  by the deterministic governance core — the LLM never authorises an action, and
  uncertain cases fail safe to human approval. Verified with no key (lexicon path)
  and with a stub model (the LLM-concept path); the live GPT-4o path is the
  owner's to run.

## Not claimed
- **No** live government deployment.
- **No** live pilot impact metric (e.g. "X hours saved" in a real school).
- **No** autonomous external action of any kind.
- **No** universal workflow autonomy — one configured workflow is demonstrated,
  not a general autonomous agent for all public-service tasks, and no real
  external publication occurs in the judging build.
- No accuracy/quality claim about LLM-generated content beyond route, shape, and
  governance behaviour (content generation with a live provider is out of scope
  for the offline judging build).
