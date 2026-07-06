# GovGuard V3 — Quick Start Guide

Offline, no API keys required (default planner `smart_mock`, `MAIC_DEMO_MODE=1`).
On Windows keep the `-X utf8` flag — the demo mixes 中文, Bahasa Melayu, and English.

## 1. Start the server

```powershell
python -X utf8 -m server.app
```

## 2. Open the local demo

<http://127.0.0.1:8765>

## 3. Demo parts (four, in three tiers)

- **Tier 1 · Core governance demo** — ① national athletics workflow autonomy +
  ② user-input governance probes (deterministic · reproducible)
- **Tier 2 · Generalisation** — ③ Route B: ad-hoc school speech competition
- **Tier 3 · Real-case evidence** — ④ Route A: charity bazaar case study
  (collapsed — click to open; synthetic donor data)

Each part has one main workflow button and one or two governance probes. The
top-bar pills show the honest session state (Governance / External / Mode).

## 4. Optional — clean rehearsal state

```powershell
python -X utf8 scripts/reset_demo_state.py --yes
```

Backs up then clears `state/ outputs/ traces/` for a pristine
first-run → approve → reuse lifecycle. Restart the server afterwards.

## 5. Run the tests

```powershell
python -X utf8 -m pytest -q
```

Expected: **1079 passed / 1 skipped / 0 failed** (1080 collected).

## 6. Run the evaluations

```powershell
python -X utf8 scripts/run_evals.py
```

Expected: **37 / 37** evaluated passed, **3** skipped, pass rate **1.0**.

## 7. Verify no secrets

```powershell
python -X utf8 scripts/verify_no_secrets.py
```

Expected: `PASS: no secrets, no blocked files in the public surface.`
