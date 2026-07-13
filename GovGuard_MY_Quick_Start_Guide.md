# GovGuard MY — Quick Start Guide

Offline, no API keys required (default planner `smart_mock`, `MAIC_DEMO_MODE=1`).
On Windows keep the `-X utf8` flag — the demo mixes 中文, Bahasa Melayu, and English.

## 1. Install once

```powershell
python -m pip install -e ".[dev]"
```

## 2. Start the server

```powershell
python -X utf8 -m server.app
```

## 3. Open the local demo

<http://127.0.0.1:8765>

## 4. Demo parts (four, in three tiers)

- **Tier 1 · Core governance demo** — ① national athletics workflow autonomy +
  ② user-input governance probes (deterministic · reproducible)
- **Tier 2 · Generalisation** — ③ open school input with a governed Markdown
  Response Pack (Route B remains the reproducible example)
- **Tier 3 · Real-case evidence** — ④ Route A: charity bazaar case study
  (collapsed — click to open; synthetic donor data)

Each part has one main workflow button and one or two governance probes. The
top-bar pills show the honest session state (Governance / External / Mode).

## 5. Optional — competition Mixed Live

```powershell
$env:TEOW_AGL_LIVE_WORKFLOWS = "ad_hoc_school_event_reporting,school_charity_bazaar"
$env:TEOW_AGL_LIVE_SCHOOL_INPUTS = "1"
$env:TEOW_AGL_CHAT_LLM = "openai"
$env:OPENAI_API_KEY = "<private key; never commit it>"
$env:OPENAI_MODEL = "gpt-4o"
python -X utf8 -m server.app
```

The core demo stays deterministic. Route A/B, their typed follow-ups, and new
school-admin cases use the live API; deterministic governance still owns every
route, artifact and approval decision. Open-input files are Markdown, unknowns
remain TBC, and a failed live bundle falls back to safe role-specific drafts. A
confirmed unrelated request receives a stable capability-boundary response and
does not inherit any school-case data.

Optional real-key smoke test:

```powershell
python -X utf8 scripts/verify_openai_school_inputs.py --full
```

## 6. Optional — clean rehearsal state

```powershell
python -X utf8 scripts/reset_demo_state.py --yes
```

Backs up then clears `state/ outputs/ traces/` for a pristine
first-run → approve → reuse lifecycle. Restart the server afterwards.

## 7. Run the tests

```powershell
python -X utf8 -m pytest -q
```

Expected: **1120 passed / 1 skipped / 0 failed** (1121 collected).

## 8. Run the enhancement checks

```powershell
python -X utf8 scripts/verify_competition_enhancements.py
```

Expected: `PASS  all 6 competition enhancement checks`.

## 9. Run the evaluations

```powershell
python -X utf8 scripts/run_evals.py
```

Expected: **37 / 37** evaluated passed, **3** skipped, pass rate **1.0**.

## 10. Verify no secrets

```powershell
python -X utf8 scripts/verify_no_secrets.py
```

Expected: `PASS: no secrets, no blocked files in the public surface.`
