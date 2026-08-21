# GovGuard MY — Quick Start Guide

Offline, no API keys required (default planner `smart_mock`, `MAIC_DEMO_MODE=1`).
On Windows keep the `-X utf8` flag — the demo mixes 中文, Bahasa Melayu, and English.
The zero-key path runs all scripted demos and conservatively compiles unfamiliar
school-admin input into complete governed Markdown packs.

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

Choose **one** provider block. OpenAI:

```powershell
$env:TEOW_AGL_PLANNER = "smart_mock"
$env:MAIC_DEMO_MODE = "1"
$env:TEOW_AGL_DOMAIN_PACK = "public_school"
$env:OPENAI_API_KEY = "<private OpenAI key; never commit it>"
$env:OPENAI_MODEL = "gpt-4o"
$env:TEOW_AGL_LIVE_WORKFLOWS = "ad_hoc_school_event_reporting,school_charity_bazaar"
$env:TEOW_AGL_LIVE_SCHOOL_INPUTS = "1"
python -X utf8 -m server.app
```

DeepSeek (the same short OpenAI-compatible startup you already tested):

```powershell
$env:TEOW_AGL_PLANNER = "smart_mock"
$env:MAIC_DEMO_MODE = "1"
$env:TEOW_AGL_DOMAIN_PACK = "public_school"
$env:OPENAI_API_KEY = "<private DeepSeek key; never commit it>"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-v4-flash"
$env:TEOW_AGL_LIVE_WORKFLOWS = "ad_hoc_school_event_reporting,school_charity_bazaar"
$env:TEOW_AGL_LIVE_SCHOOL_INPUTS = "1"
python -X utf8 -m server.app
```

The core demo stays deterministic. Route A/B, their typed follow-ups, and new
school-admin cases attempt the live API; deterministic governance still owns
every route, artifact and approval decision. Open-input files are Markdown,
unknowns remain TBC, and a failed or unavailable live bundle falls back to safe
role-specific drafts. The top badge reports configuration; each task reports
whether live generation or deterministic fallback actually ran, including the
actual provider and model. A
confirmed unrelated request receives a stable capability-boundary response and
does not inherit any school-case data.

Optional real-key smoke test (uses the configured OpenAI or DeepSeek endpoint):

```powershell
python -X utf8 scripts/verify_openai_school_inputs.py --full
```

There is no hidden OpenAI fallback. If DeepSeek is selected and unavailable,
GovGuard returns its governed deterministic Markdown fallback. DeepSeek
thinking is disabled by default; set `DEEPSEEK_THINKING=enabled` only when you
intentionally want the extra latency/cost.

Measured Mixed Live scope is English and Bahasa Melayu school-administration
input. Two independent 19-case runs observed **74-84% complete output**, with
zero personal-data leakage, zero unauthorised external sending, and fail-closed
handling for every unsuccessful case. Open-input artifacts are Markdown-only.
GovGuard does not autonomously create student-health data collection fields; use
a non-medical consent draft or a human-approved school template instead.

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

Validated baseline: **1,876 collected across 115 test modules; 1,868 passed /
8 intentional/environment-dependent skipped / 0 failed** in the ordinary grouped run.
The `tests/` directory contains **117 Python files** in total, including
`conftest.py` and `__init__.py`.
The browser UI contract suite passes **21 / 21** when enabled, including seven conditional browser cases.

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
