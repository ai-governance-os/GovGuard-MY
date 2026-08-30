# GovGuard MY — Governance Runtime for High-Risk Public-Service AI Workflows

### The planner proposes. Governance decides (BLUE / GREEN / RED / INFEASIBLE). A human approves. Everything is traced.

**GovGuard MY** · Powered by the TEOW-AGL Governance Runtime · MAIC Nexus Challenge 2026 · Track T5 (Public Services).

> **Quick start — no API key:** `python -X utf8 -m server.app` → open
> <http://127.0.0.1:8765>. The default is offline `smart_mock`,
> `MAIC_DEMO_MODE=1`, with every external action simulated.

## What this is

GovGuard MY is a governed, auditable runtime for high-risk public-service AI
workflows. This submission demonstrates it through a Malaysian public-school
administration domain pack, where the stakes are concrete: children, parents,
records, public messages, approvals, and community trust. It produces genuinely
useful administrative outputs — reports, parent notices, public posts, donor
outreach — while enforcing data-use limits, privacy boundaries, human approval
for consequential actions, and a full audit trail. The governance core is
designed for additive, configured domain packs (`TEOW_AGL_DOMAIN_PACK`);
`public_school` is the domain pack shipped and verified in this submission.

## Why it matters

GovGuard is **not** a generic chatbot. It implements a *model proposes,
governance decides* architecture: the language model may draft wording, but
deterministic governance layers decide whether a data use is allowed, whether an
external action needs human approval, whether a request must be refused, and
whether a missing fact must be marked unknown instead of invented. None of these
decisions is left to prompt-only guardrails.

## Demo overview — three stable demos, then optional open input

1. **① National athletics workflow autonomy** — the agent runs a multi-step
   school follow-up, self-blocks its own unsafe status/income proposal (RED),
   and pauses a protected student-record write for human verification (GREEN).
2. **② User-input governance probes** — the same governance over the operator's
   later free-text requests (BLUE edit, RED status-pressure, GREEN release,
   INFEASIBLE reward guess, RED learning-boundary).
3. **③ Route A — real-case-derived school charity bazaar** — a realistic,
   deterministic administrative deployment over a *synthetic* stakeholder/donor
   database.
4. **④ Route B — controlled transfer** — the reproducible speech-competition
   workflow separates public achievement from private student support and never
   invents missing facts.
5. **⑤ Optional open input** — an unfamiliar typed school situation becomes a
   selectable governed Markdown Response Pack. A configured provider may enrich
   interpretation and prose without gaining authority.

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

Open school input uses a closed-schema **Situation Compiler** in front of the same
governance runtime. A provider, when available, may label meaning — family,
phase, severity, affected people, stakeholders, known facts and unknowns — but
it cannot authorise an action. With no key or during a provider outage, a
bounded deterministic compiler produces a conservative pack instead.
Config-driven policy compiles the labels into a role-scoped Response Pack.
Every selected draft, official-source lookup and external action is then
governed independently.

- All open-input text artifacts are `.md` and isolated by task.
- Required outputs run automatically; recommended and conditional outputs stay
  visible but unselected unless the operator chooses them.
- One critical question is asked only when its answer changes life safety,
  coverage, recipient or governance boundary; otherwise missing facts remain TBC.
- A failed, timed-out, rate-limited, or malformed content-generation call opens
  a task-local outage circuit and falls back to complete, role-specific,
  fact-conservative Markdown instead of writing a partial or unsupported file.
- The operator can add a missing output after the first pack. The delta task
  inherits the case and risk level, but still passes through governance.
- Public drafts exclude person-level case details. Real send/publish/contact
  requests remain separate GREEN actions and are simulated in demo mode.
- Open case facts are task-local and never update `USER.md` or `MEMORY.md`.
  Reusable workflow SOP learning remains non-personal and owner-gated.
- The School Administration Pack produces governed Markdown drafts. PowerPoint
  and other Office-format export are outside this submission's claimed scope.
- GovGuard will not autonomously create student-health data collection fields.
  It may prepare a non-medical consent draft or use a human-approved school
  template; deciding what protected health information to collect remains a
  human responsibility.

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

**Optional competition Open Input Live mode** (with a valid OpenAI or DeepSeek
key): set `TEOW_AGL_LIVE_SCHOOL_INPUTS=1` before starting. Main, Route A and
Route B remain deterministic and display `REPRODUCIBLE MOCK`; only unfamiliar
open input attempts the API in the same browser session.
The LLM labels meaning and may draft content; deterministic coverage,
data-use, governance, artifact and verification modules still own the package
and decide BLUE/GREEN/RED/INFEASIBLE. If a live bundle is incomplete or
ungrounded — or the provider is unavailable — role-specific safe Markdown
replaces it as one governed unit. An
unrelated request does not silently inherit the school case. Instead it receives
a stable capability-boundary answer that states no student, parent, or
school-case data was carried over; the generic planner is skipped. The UI mode
badge states what is configured; each open-input task's generation badge and audit trace
state whether the provider was actually used or a deterministic fallback ran.

Two independent 19-case English / Bahasa Melayu Mixed Live runs observed
**74-84% complete output**. Across both runs there was **zero personal-data
leakage, zero unauthorised external sending, and every unsuccessful case failed
closed**. This is an observed competition test range, not a statistical
confidence interval or a claim that every arbitrary prompt will be completed.

For OpenAI, set `OPENAI_API_KEY` and `OPENAI_MODEL`. For DeepSeek's
OpenAI-compatible API, keep the same variable names and additionally set
`OPENAI_BASE_URL=https://api.deepseek.com/v1` and a `deepseek-*` model (for
example `deepseek-v4-flash`). The runtime exposes the actual provider/model in
`/api/config`, the Mode tooltip, and each live task's generation badge. There is
no automatic cross-provider fallback: DeepSeek failure returns governed
deterministic Markdown and never calls an OpenAI model. DeepSeek credentials
are not sent to OpenAI's embedding endpoint.

With a private key present, the optional live smoke test exercises semantic
intake plus one complete configured-provider planner → governance → execution → verification
run (the key is never printed):

```powershell
python -X utf8 scripts/verify_openai_school_inputs.py --full
```

## Test evidence

- **1,937** tests collected across **119** test modules — **1,929 passed**,
  **8 intentional/environment-dependent skipped**, **0 failed** in the ordinary grouped run
- The `tests/` directory contains **121 Python files** in total, including
  `conftest.py` and `__init__.py`.
- Browser UI contract suite: **21 / 21 passed** when enabled, including the
  seven browser-dependent cases skipped in the ordinary run
- Evaluation suite: **38 / 38** evaluated cases passed, **3** skipped, pass rate **1.0**
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
