# GovGuard V3 — Governed School-Administration Agent Runtime

### The planner proposes. Governance decides (BLUE / GREEN / RED / INFEASIBLE). A human approves. Everything is traced.

**GovGuard V3** · Powered by the TEOW-AGL Governance Runtime · MAIC Nexus Challenge 2026 · Track T5 (Public Services).

## What this is

GovGuard V3 is a governed, auditable AI-agent runtime for school administration.
It produces genuinely useful administrative outputs — reports, parent notices,
public posts, donor outreach — while enforcing data-use limits, privacy
boundaries, human approval for consequential actions, and a full audit trail.

## Why it matters

GovGuard is **not** a generic chatbot. It implements a *model proposes,
governance decides* architecture: the language model may draft wording, but
deterministic governance layers decide whether a data use is allowed, whether an
external action needs human approval, whether a request must be refused, and
whether a missing fact must be marked unknown instead of invented. None of these
decisions is left to prompt-only guardrails.

## V3 demo overview — four parts, three tiers

The local demo home page groups four demo parts into a three-tier narrative —
**core governance demo** (dominant) → **generalisation** → **real-case
evidence** (collapsed case study):

1. **① National athletics workflow autonomy** — the agent runs a multi-step
   school follow-up, self-blocks its own unsafe status/income proposal (RED),
   and pauses a protected student-record write for human verification (GREEN).
2. **② User-input governance probes** — the same governance over the operator's
   later free-text requests (BLUE edit, RED status-pressure, GREEN release,
   INFEASIBLE reward guess, RED learning-boundary).
3. **③ Route B — ad-hoc school speech competition (generalisation)** — a short,
   unseen prompt with *no persistent student/parent database*; the agent builds a temporary case
   and governs it.
4. **④ Route A — real-case-derived school charity bazaar** — a realistic
   administrative deployment over a *synthetic* stakeholder/donor database.

## Route A: real-case-derived charity bazaar

A real school event structure (Environmental Charity Bazaar) over a **synthetic**
24-record stakeholder/donor database. GovGuard produces a trilingual (中文 /
Bahasa Melayu / English) Facebook post, an English parent notice, an internal
preparation checklist, non-pressuring donor-outreach drafts, and a data-use
audit — while self-blocking wealth inference, donor ranking, and status/prior-
support pressure. A drafts-only request ends with a recorded external-release
boundary: any actual send/publish is a separate request that routes GREEN and
requires human approval.

## Route B: minimal-input generalisation

No persistent student/parent database — the case is built from the prompt. From a short, unseen speech-competition prompt the agent
builds an **ephemeral case envelope**, splits public achievement from private
student-support information (winners celebrated publicly; struggling pupils kept
to the internal report and private parent notices), marks missing facts as
**to-be-confirmed** rather than inventing them, requires approval before any
send/publish, and refuses to persist student-sensitive facts to long-term memory.
It reuses the *procedure* it learned from the national case — transferring
governed procedure, not private data.

## Governance model

| Colour | Meaning |
|---|---|
| **BLUE** | Safe internal or draft task — auto-run |
| **GREEN** | External / consequential action — human approval required |
| **RED** | Prohibited or unsafe request — self-blocked, with a safe alternative |
| **INFEASIBLE** | Cannot be done reliably (missing data) — marked, not guessed |

## How to run

```powershell
python -X utf8 -m server.app          # → http://127.0.0.1:8765
python -X utf8 -m pytest -q           # tests
python -X utf8 scripts/run_evals.py   # evaluation suite
python -X utf8 scripts/verify_no_secrets.py
```

Runs offline with **no API keys** (default planner `smart_mock`,
`MAIC_DEMO_MODE=1`). Keep `-X utf8` on Windows (the demo mixes 中文 / Malay /
English).

**Optional mixed live mode** (with a valid key): set
`TEOW_AGL_LIVE_WORKFLOWS=ad_hoc_school_event_reporting` before starting — the
core demo stays deterministic while the unseen-case route drafts on the live
API, in one server session. The UI mode badges always state which tier is
actually running.

## Test evidence

- **1079** tests collected — **1078 passed**, **1 skipped**, **0 failed**
- Evaluation suite: **37 / 37** evaluated cases passed, **3** skipped, pass rate **1.0**
- Secret scan: **PASS** (no secrets, no blocked files in the public surface)

## Privacy and data boundary

Real-case-derived, privacy-preserving: real school **event structure** may be
used, but **all** person-level records (donors, parents, students, stakeholders)
are **synthetic or redacted**. No real donor list, phone number, address,
payment record, WhatsApp record, or student-sensitive record appears in the
public demo. *(Route A's event structure is real-case-derived; all person-level
stakeholder/donor records are synthetic. Any public use of the real school
context should be supported by a separate acknowledgement letter or redacted
evidence pack where appropriate — see `EVIDENCE_PACK_NOTE.md`.)*

## Repository notes

This is a submission build: it excludes secrets, local runtime state, the venv,
git internals, traces, and private uploads. See `scripts/verify_no_secrets.py`.
