# GovGuard V3 — build notes

**GovGuard V3 is the current MAIC submission build. V2 is retained only as a
frozen baseline for regression comparison and historical reference.** V3 = V2
plus (a) a shared learning-boundary hardening and (b) two additional demo routes
(generalisation + a second real-case domain). It is built on branch
`10.7.5-v3-route-b`; V2 is kept untouched as the fallback baseline.

## What V3 adds

### Shared foundation (Brief 3 §E) — task-local tone is not persisted
A one-off styling instruction ("keep this notice warm", "make it concise")
governs the current output only. The reflector no longer distils it into a
durable USER.md preference unless the user explicitly asks to remember it.
Deterministic backstop `_drop_task_local_style` (scope-cue gated) + a tightened
reflector prompt. See `teow_agl/modules/module_109_reflector.py`,
`tests/test_learning_filter.py`.

### Route B — ad-hoc school-event reporting (the generalisation demo)
A short, unseen English-speech-competition prompt (no prepared database) builds
a temporary governed case and produces a public Facebook post, an internal
report, a champion parent notice, and a private guidance notice. The claim:
**GovGuard transfers governed PROCEDURE, not private data.**

- `configs/workflows/public_school/ad_hoc_school_event_reporting.json` — 9 steps,
  BLUE drafts + a RED public-disclosure self-block + a GREEN send/publish gate.
  Triggers are speech-competition-specific so it never steals the national or
  post-event goals.
- `demo_data/ad_hoc_school_event/curated_drafts.md` + `demo/ad_hoc_school_event_results.md`
  — the keyless/curated path (Daniel & Emma kept out of public output; missing
  details marked to-be-confirmed, never invented).
- Governance: 101D RED 4 (a pupil's difficulty in public output → RED, keyed on
  the step's OWN data use); intake risk_rules `expose_student_weakness_public`
  (RED), `invent_missing_detail` (INFEASIBLE), and an extended
  `sensitive_data_learning` (persist a pupil's weakness → RED).
- Tests: `tests/test_route_b_ad_hoc.py`, `tests/test_route_b_probes.py`.
- UI: welcome section ③ (main workflow button + two labelled governance probes).

## Demo (keyless, recommended)

`python -X utf8 -m server.app` → click section ③'s "🎤 An ad-hoc
speech-competition report". The workflow runs on the new case: winners are
celebrated publicly, the struggling pupils are kept to the internal report and
a private parent notice, missing facts are marked TBC, and any send/publish is
routed to human approval. Try the two ③ probes for the RED / INFEASIBLE
governance.

### Route A — school environmental charity bazaar (second real-case domain)
A real-case-derived charity-bazaar workflow over a **synthetic** 24-record
donor database: a trilingual (中/BM/EN) public Facebook post, an English parent
notice, an internal preparation checklist, non-pressuring donor outreach, and a
data-use audit. The self-governance moment is a RED self-block on **wealth
inference / status pressure** (occupation / board position / prior amount →
stronger asks).

- `configs/workflows/public_school/school_charity_bazaar.json`,
  `demo_data/charity_bazaar/synthetic_stakeholders.json` (24) + `curated_drafts.md`,
  `demo/charity_bazaar_case.md`.
- Governance: 101D RED 1 (socio + differential) for the self-block; intake
  `wealth_inference_targeting` (RED) + extended `status_based_differential`
  (board flattery, prior-donor guilt, small-supporter disrespect,
  status-hierarchy learning). Tests: `tests/test_route_a_charity_bazaar.py`,
  `tests/test_route_a_probes.py`. UI: welcome section ④.
- **Privacy:** the brief names a real school; the synthetic data is fabricated,
  but obtain the school's acknowledgement before any PUBLIC use of the name
  (or pseudonymise it).

### Mixed-mode planner + three-tier console UI (final polish)
- **Mixed mode:** `TEOW_AGL_LIVE_WORKFLOWS=<workflow_id,...>` at startup runs the
  listed workflows on the live API while everything else stays deterministic —
  one server, no restart between demo parts. A task goes live only when its goal
  pre-resolves to a listed workflow AND a key is present; env override happens
  under a construction lock and is always restored. `/api/config` exposes
  `live_workflows` + `live_ready`; each task records `planner_mode`. See
  `tests/test_mixed_mode.py`.
- **UI:** the landing page is a three-tier governance console (core demo →
  generalisation → collapsed real-case evidence) with a product top bar, honest
  status pills (Mode badge never claims live without a key), navy primary
  actions, and version-busted static assets (no more stale-cache hard refresh).

## Still pending (owner)

- **Live validation requires a rotated API key.** Everything above is built and
  unit-tested keyless (stub / curated); the live-model faithfulness pass
  (Brief 3 §B–D contracts under `TEOW_AGL_PLANNER=openai`) is validated only
  after the leaked key is rotated.
