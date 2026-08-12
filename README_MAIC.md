# GovGuard MY — MAIC Submission Brief

[![CI](https://github.com/ai-governance-os/GovGuard-MY/actions/workflows/ci.yml/badge.svg)](https://github.com/ai-governance-os/GovGuard-MY/actions/workflows/ci.yml)

**GovGuard MY** · Powered by the TEOW-AGL Governance Runtime · MAIC Nexus Challenge 2026 · Track T5 (Public Services).

## One-line summary

GovGuard MY is a governance runtime for high-risk public-service AI workflows,
demonstrated through a school-administration domain pack. It produces useful
administrative outputs while enforcing privacy, human-approval routing,
data-use limits, auditability, and bounded memory — across deterministic,
generalised, and real-case-derived scenarios, not limited to one curated case.

## Demo narrative (Route A and Route B front and centre)

- **Route A — deployment credibility.** The workflow is derived from a *real*
  school charity-bazaar preparation process. The event structure is real, but
  person-level stakeholder and donor records are **synthetic** to protect
  privacy. GovGuard generates a trilingual Facebook post, a parent notice, an
  internal checklist, donor-outreach drafts, and a data-use audit while blocking
  wealth inference, status pressure, and prior-donor pressure.
- **Open-input generalisation.** Route B remains the reproducible short-prompt
  example, while Mixed Live accepts unfamiliar school situations across safety,
  welfare, transport, food, events, cyber/data, finance, learning support, and
  general administration. A closed-schema Situation Compiler proposes a selectable
  Markdown Response Pack; deterministic policy owns coverage, TBC, privacy,
  approval and memory boundaries. It has a conservative zero-key compiler; an
  optional provider adds richer interpretation and prose but no authority. A
  malformed or unavailable live draft cannot remove a required file: the
  governed role receives a fact-conservative safe fallback.

## What judges should notice

1. The model **proposes**, but **governance decides**.
2. External publication / sending is **not automatic** — it requires human approval.
3. Sensitive **student** facts are **not publicly exposed**.
4. **Donor / stakeholder** data is synthetic and governed — no wealth inference, no ranking, no pressure.
5. Missing facts are marked **TBC** instead of invented.
6. Learning is **bounded**: open case facts remain task-local; only a
   non-personal workflow SOP can enter the separate owner-gated learning path.
7. Free-form Route A/B follow-ups and new school-admin cases work in conservative
   zero-key mode; when a provider is usable, Mixed Live can enrich their semantic
   interpretation and prose while deterministic governance still owns the route.
8. Approved learning may reduce repeated friction, but it cannot downgrade a
   mandatory GREEN human gate imposed by policy or the data-use guard.

## Demo — four parts, three tiers

The landing console groups the four demo parts into a three-tier narrative
(core demo dominant → generalisation → collapsed evidence):

1. **① National athletics workflow autonomy** — deep workflow + internal self-governance (RED self-block; GREEN protected-record verification). *(Tier 1)*
2. **② User-input governance probes** — governance over the operator's later free-text requests. *(Tier 1)*
3. **③ Open school input** — school-ready generalisation with Route B as the reproducible example and arbitrary typed cases in Mixed Live. *(Tier 2)*
   The submission's measured open-input scope is English and Bahasa Melayu
   school-administration cases. Chinese free-form input is not part of this
   open-input claim; scripted routes may still produce deterministic
   trilingual content.
4. **④ Route A — real-case-derived charity bazaar** — realistic deployment over synthetic donor data. *(Tier 3, case study)*

## Measured Mixed Live boundaries

- Two independent 19-case English / Bahasa Melayu runs observed **74-84%
  complete output**. Both runs had **zero personal-data leakage, zero
  unauthorised external sending, and fail-closed handling for every unsuccessful
  case**. The range is an observed result, not a statistical confidence interval.
- The School Administration Pack produces governed Markdown drafts. PowerPoint
  and other Office-format export are outside this submission's claimed scope.
- GovGuard will not autonomously create student-health data collection fields.
  It may prepare a non-medical consent draft or use a human-approved school
  template; collection of protected health information remains a human decision.

## Evidence

- pytest: **1,868** collected across **113 test modules** — **1,860 passed /
  8 intentional/environment-dependent skipped / 0 failed** in the ordinary grouped run.
- The `tests/` directory contains **115 Python files** in total, including
  `conftest.py` and `__init__.py`.
- Browser UI contract suite: **21 / 21 passed** when enabled, including seven conditional browser cases.
- Evaluation suite: **37 / 37** evaluated cases passed, **3** skipped, pass rate **1.0**.
- Secret scan: **PASS**.
- Published regression evidence is offline / keyless
  (`smart_mock`, `MAIC_DEMO_MODE=1`); live generation is optional.

## Privacy note

Real administrative workflows are used responsibly: the real **event structure**
is allowed, but sensitive **person-level** records are synthetic or redacted.
No real donor, parent, or student-sensitive record is exposed in the demo.
Route A's event structure is real-case-derived and used **with the school's
signed acknowledgement** (provided as a separate evidence pack, not in this
repository). All person-level records in this repository are synthetic. See
`EVIDENCE_PACK_NOTE.md`.

## Originality claim

Even connected to a real LLM, GovGuard does not rely on prompt-only guardrails.
The model can draft language, but governance routing, protected writes, release
approval, verification, and learning boundaries are enforced **outside** the
model. Route A shows this on a credible real-case deployment; Route B shows the
same governed procedure transferring to an unseen case without reusing private
data or inventing missing facts.
