# GovGuard V3 — dev branch notes

**V3 is the development line. V2 remains the competition submission.** V3 = V2
plus (a) a shared learning-boundary hardening and (b) a second, generalisation
demo route. It is built on branch `10.7.5-v3-route-b` in a separate pair of
folders so V2 is never touched.

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

## Still pending (owner)

- **Live validation requires a rotated API key.** Everything above is built and
  unit-tested keyless (stub / curated); the live-model faithfulness pass
  (Brief 3 §B–D contracts under `TEOW_AGL_PLANNER=openai`) is validated only
  after the leaked key is rotated.
- Route A (charity bazaar) is scoped but not built — Route B (higher-value
  generalisation) was prioritised.
