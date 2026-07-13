# GovGuard MY — Governance Runtime for High-Risk Public-Service AI Workflows

### The planner proposes. Governance decides (BLUE / GREEN / RED / INFEASIBLE). A human approves. Everything is traced.

**GovGuard MY** · Powered by the TEOW-AGL Governance Runtime · MAIC Nexus Challenge 2026 · Track T5 (Public Services).

## What this is

GovGuard MY is a governed, auditable runtime for high-risk public-service AI
workflows. This submission demonstrates it through a Malaysian public-school
administration domain pack, where the stakes are concrete: children, parents,
records, public messages, approvals, and community trust. It produces genuinely
useful administrative outputs — reports, parent notices, public posts, donor
outreach — while enforcing data-use limits, privacy boundaries, human approval
for consequential actions, and a full audit trail. The governance runtime is
domain-agnostic (swap the domain pack by config alone, `TEOW_AGL_DOMAIN_PACK`);
school administration is the first shipped pack, not the product limit.

## Why it matters

GovGuard is **not** a generic chatbot. It implements a *model proposes,
governance decides* architecture: the language model may draft wording, but
deterministic governance layers decide whether a data use is allowed, whether an
external action needs human approval, whether a request must be refused, and
whether a missing fact must be marked unknown instead of invented. None of these
decisions is left to prompt-only guardrails.

## Demo overview — four parts, three tiers

The local demo home page groups four demo parts into a three-tier narrative —
**core governance demo** (dominant) → **generalisation** → **real-case
evidence** (collapsed case study):

1. **① National athletics workflow autonomy** — the agent runs a multi-step
   school follow-up, self-blocks its own unsafe status/income proposal (RED),
   and pauses a protected student-record write for human verification (GREEN).
2. **② User-input governance probes** — the same governance over the operator's
   later free-text requests (BLUE edit, RED status-pressure, GREEN release,
   INFEASIBLE reward guess, RED learning-boundary).
3. **③ Open school input (generalisation)** — an unfamiliar school situation
   becomes a selectable, governed Markdown Response Pack. Route B remains the
   reproducible speech-competition example; typed cases can range across safety,
   welfare, events, transport, food, cyber/data, finance, learning support, and
   general administration.
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

## School-ready open input

Mixed Live adds a closed-schema **Situation Compiler** in front of the same
governance runtime. The LLM labels meaning — family, phase, severity, affected
people, stakeholders, known facts and unknowns — but it cannot authorise an
action. Config-driven policy compiles those labels into a role-scoped Response
Pack. Every selected draft, official-source lookup and external action is then
governed independently.

- All open-input text artifacts are `.md` and isolated by task.
- Required outputs run automatically; recommended and conditional outputs stay
  visible but unselected unless the operator chooses them.
- One critical question is asked only when its answer changes life safety,
  coverage, recipient or governance boundary; otherwise missing facts remain TBC.
- A failed or malformed content-generation call falls back to complete,
  role-specific, fact-conservative Markdown instead of writing a partial or
  unsupported file.
- The operator can add a missing output after the first pack. The delta task
  inherits the case and risk level, but still passes through governance.
- Public drafts exclude person-level case details. Real send/publish/contact
  requests remain separate GREEN actions and are simulated in demo mode.
- Open case facts are task-local and never update `USER.md` or `MEMORY.md`.
  Reusable workflow SOP learning remains non-personal and owner-gated.

## Governance model

| Colour | Meaning |
|---|---|
| **BLUE** | Safe internal or draft task — auto-run |
| **GREEN** | External / consequential action — human approval required |
| **RED** | Prohibited or unsafe request — self-blocked, with a safe alternative |
| **INFEASIBLE** | Cannot be done reliably (missing data) — marked, not guessed |

## How to run

```powershell
python -m pip install -e ".[dev]"     # once, in a clean Python environment
python -X utf8 -m server.app          # → http://127.0.0.1:8765
python -X utf8 -m pytest -q           # tests
python -X utf8 scripts/run_evals.py   # evaluation suite
python -X utf8 scripts/verify_competition_enhancements.py
python -X utf8 scripts/verify_no_secrets.py
```

Runs offline with **no API keys** (default planner `smart_mock`,
`MAIC_DEMO_MODE=1`). Keep `-X utf8` on Windows (the demo mixes 中文 / Malay /
English).

**Optional competition Mixed Live mode** (with a valid key): set both
`TEOW_AGL_LIVE_WORKFLOWS=ad_hoc_school_event_reporting,school_charity_bazaar`
and `TEOW_AGL_LIVE_SCHOOL_INPUTS=1` before starting. The core main demo remains
deterministic; Route A/B, their free-form follow-ups, and new school-domain
administration cases use the API in the same browser session. The active case
is carried into follow-up turns, including while a GREEN approval is pending.
The LLM labels meaning and may draft content; deterministic coverage,
data-use, governance, artifact and verification modules still own the package
and decide BLUE/GREEN/RED/INFEASIBLE. If a live bundle is incomplete or
ungrounded, role-specific safe Markdown replaces it as one governed unit. An
unrelated request does not silently inherit the school case. Instead it receives
a stable capability-boundary answer that states no student, parent, or
school-case data was carried over; the generic planner is skipped. The UI mode
badges state which tier is actually running.

With a private key present, the optional live smoke test exercises semantic
intake plus one complete OpenAI planner → governance → execution → verification
run (the key is never printed):

```powershell
python -X utf8 scripts/verify_openai_school_inputs.py --full
```

## Test evidence

- **1121** tests collected — **1120 passed**, **1 skipped**, **0 failed**
- Evaluation suite: **37 / 37** evaluated cases passed, **3** skipped, pass rate **1.0**
- Secret scan: **PASS** (no secrets, no blocked files in the public surface)

## Privacy and data boundary

Real-case-derived, privacy-preserving: real school **event structure** may be
used, but **all** person-level records (donors, parents, students, stakeholders)
are **synthetic or redacted**. No real donor list, phone number, address,
payment record, WhatsApp record, or student-sensitive record appears in the
public demo. *(Route A's event structure is real-case-derived and used **with
the school's signed acknowledgement** (provided as a separate evidence pack);
all person-level stakeholder/donor records are synthetic — see
`EVIDENCE_PACK_NOTE.md`.)*

## Repository notes

This is a submission build: it excludes secrets, local runtime state, the venv,
git internals, traces, and private uploads. See `scripts/verify_no_secrets.py`.
