# GovGuard V3 — MAIC Submission Brief

<!-- Replace OWNER/REPO with the real GitHub path once the repo is created. -->
[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

**GovGuard V3** · Powered by the TEOW-AGL Governance Runtime · MAIC Nexus Challenge 2026 · Track T5 (Public Services).

## One-line summary

GovGuard V3 is a governed school-administration AI runtime that produces useful
administrative outputs while enforcing privacy, human-approval routing, and
data-use limits — and it now demonstrates this across **two additional
real-world-shaped scenarios**, not just one hard-coded demo.

## Demo narrative (Route A and Route B front and centre)

- **Route A — deployment credibility.** The workflow is derived from a *real*
  school charity-bazaar preparation process. The event structure is real, but
  person-level stakeholder and donor records are **synthetic** to protect
  privacy. GovGuard generates a trilingual Facebook post, a parent notice, an
  internal checklist, donor-outreach drafts, and a data-use audit while blocking
  wealth inference, status pressure, and prior-donor pressure.
- **Route B — generalisation.** The user gives only a short, unseen school
  competition prompt. GovGuard builds a temporary case envelope and generates a
  Facebook post, internal report, champion parent notice, and private guidance
  notice. It celebrates winners publicly but keeps student-support issues
  private, marks missing facts as TBC, requires approval before publishing or
  sending, and refuses to persist student-sensitive facts.

## What judges should notice

1. The model **proposes**, but **governance decides**.
2. External publication / sending is **not automatic** — it requires human approval.
3. Sensitive **student** facts are **not publicly exposed**.
4. **Donor / stakeholder** data is synthetic and governed — no wealth inference, no ranking, no pressure.
5. Missing facts are marked **TBC** instead of invented.
6. Learning is **bounded**: a one-off styling instruction does not become persistent sensitive memory.

## V3 demo — four parts, three tiers

The landing console groups the four demo parts into a three-tier narrative
(core demo dominant → generalisation → collapsed evidence):

1. **① National athletics workflow autonomy** — deep workflow + internal self-governance (RED self-block; GREEN protected-record verification). *(Tier 1)*
2. **② User-input governance probes** — governance over the operator's later free-text requests. *(Tier 1)*
3. **③ Route B — ad-hoc school speech competition** — minimal-input generalisation; optionally runs on the live API in mixed mode. *(Tier 2)*
4. **④ Route A — real-case-derived charity bazaar** — realistic deployment over synthetic donor data. *(Tier 3, case study)*

## Evidence

- pytest: **1080** collected — **1079 passed / 1 skipped / 0 failed**.
- Evaluation suite: **37 / 37** evaluated cases passed, **3** skipped, pass rate **1.0**.
- Secret scan: **PASS**.
- All offline / keyless (`smart_mock`, `MAIC_DEMO_MODE=1`).

## Privacy note

Real administrative workflows are used responsibly: the real **event structure**
is allowed, but sensitive **person-level** records are synthetic or redacted.
No real donor, parent, or student-sensitive record is exposed in the demo.
Route A's event structure is real-case-derived; any public use of the real
school context should be supported by a separate acknowledgement letter or
redacted evidence pack where appropriate (provided separately, not in this
repository). All person-level records in this repository are synthetic. See
`EVIDENCE_PACK_NOTE.md`.

## Originality claim

Even connected to a real LLM, GovGuard does not rely on prompt-only guardrails.
The model can draft language, but governance routing, protected writes, release
approval, verification, and learning boundaries are enforced **outside** the
model. Route A shows this on a credible real-case deployment; Route B shows the
same governed procedure transferring to an unseen case without reusing private
data or inventing missing facts.
