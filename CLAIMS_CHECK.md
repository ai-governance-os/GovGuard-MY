# GovGuard MY — Claims Check

**GovGuard MY** · Powered by the TEOW-AGL Governance Runtime. Every claim below is
backed by a path or a reproducible command. No number is carried that the build
cannot reproduce.

## Claim 1 — The build contains Route A and Route B

- Route A config: `configs/workflows/public_school/school_charity_bazaar.json`
- Route B config: `configs/workflows/public_school/ad_hoc_school_event_reporting.json`
- Route A data: `demo_data/charity_bazaar/`, seed `demo/charity_bazaar_case.md`
- Route B data: `demo_data/ad_hoc_school_event/`, seed `demo/ad_hoc_school_event_results.md`
- Tests: `tests/test_route_a_charity_bazaar.py`, `tests/test_route_a_probes.py`,
  `tests/test_route_b_ad_hoc.py`, `tests/test_route_b_probes.py`

## Claim 2 — Route A uses only synthetic stakeholder data

- `demo_data/charity_bazaar/synthetic_stakeholders.json` — **24** fabricated
  records, each with explicit allowed/prohibited uses.
- The workflow's data-use audit marks occupation / business ownership / board or
  PIBG position / prior donation amount / donor ranking as **prohibited**.

## Claim 3 — Route B works from minimal input

- Config `ad_hoc_school_event_reporting.json` + an **ephemeral** case built from
  the prompt (no persistent student/parent database).
- `tests/test_route_b_ad_hoc.py` (detection, no-steal, outputs, TBC discipline).

## Claim 4 — Sensitive student-support details are not placed in public output

- `test_route_b_fb_post_excludes_struggling_students` — the public post excludes
  the pupils' names and their memorisation difficulty.
- 101D RED rule + intake `expose_student_weakness_public` (RED).

## Claim 5 — External send/publish requires human approval

- Each workflow's release step routes **GREEN** (human gate); probes
  `..._auto_send/_auto_publish` → GREEN. Nothing is sent in demo mode.

## Claim 6 — Unsafe donor targeting is blocked

- `tests/test_route_a_probes.py`: wealth inference / board flattery / prior-donor
  guilt / small-supporter disrespect / status-hierarchy learning → **RED**.
- 101D self-block on the workflow's own wealth-targeting step (RED).

## Claim 7 — Persistent sensitive learning is blocked

- `tests/test_learning_filter.py` — a one-off tone/style instruction is not
  persisted as a durable preference.
- Route B persist-student-weakness probe → **RED** (learning boundary).

## Claim 8 — Test suite status

Command: `python -X utf8 -m pytest -q`

- **1,904** collected across **118 test modules** — **1,896 passed**,
  **8 intentional/environment-dependent skipped**, **0 failed** in the ordinary grouped run.
- The `tests/` directory contains **120 Python files** in total, including
  `conftest.py` and `__init__.py`.
- Browser UI contract suite: **21 / 21 passed** when enabled, including seven conditional browser cases.

## Claim 9 — Evaluation status

Command: `python -X utf8 scripts/run_evals.py`

- cases **40** (evaluated **37**, skipped **3**) — **37 passed**, **0 failed**,
  pass rate **1.0**.

## Claim 10 — Secret-scan status

Command: `python -X utf8 scripts/verify_no_secrets.py`

- **PASS** — no secrets, no blocked files in the public surface.

## Note on privacy

Route A uses a real-case-derived school event structure, while **all**
person-level records (donors, parents, students, stakeholders) in this
repository are synthetic. Any public use of the real school context should be
supported by a separate acknowledgement letter or redacted evidence pack where
appropriate (provided separately, not in this repository). See
`EVIDENCE_PACK_NOTE.md`.

## Note on the live LLM path

The competition Mixed Live path keeps the core scripted demo deterministic and
can attempt Route A/B, their continued questions, and new school-domain cases on
a configured OpenAI-compatible provider. `scripts/verify_competition_enhancements.py` independently checks
the closed semantic schema, deterministic concept policy, mandatory learning
floor, live-tier selection, out-of-domain boundary, and browser continuity
wire. The LLM is never the route authority. A private-key smoke test on
**2026-07-12** passed six live semantic probes on an earlier live checkpoint.
The current repository claim rests on reproducible offline unit, integration,
UI, and evaluation evidence; provider availability is not assumed.
`tests/test_provider_outage_circuit.py`,
`tests/test_generation_mode_truth.py`, and
`tests/test_open_school_regression_matrix.py` cover truthful source reporting,
failure containment, and complete governed fallback. The key and runtime trace
are not included in the clean public package. Re-run
`python -X utf8 scripts/verify_openai_school_inputs.py --full` with the event
key before presenting; the offline `smart_mock` path remains the reproducible
fallback.

Two independent 19-case English / Bahasa Melayu Mixed Live runs observed
**74-84% complete output**, with **zero personal-data leakage, zero unauthorised
external sending, and fail-closed handling for every unsuccessful case**. This
is an observed range, not a statistical confidence interval. The claimed output
surface is governed Markdown. PowerPoint / Office export and autonomous creation
of student-health data collection fields are explicitly outside scope.
