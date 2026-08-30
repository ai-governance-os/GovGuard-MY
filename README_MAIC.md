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
- **Route B — controlled transfer.** The reproducible short-prompt speech case
  proves that governed procedure transfers without private memory or invented
  facts. **Optional open-input generalisation** accepts unfamiliar school situations across safety,
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
7. Main, Route A and Route B are pinned to reproducible deterministic fixtures.
   A provider may enrich only optional unfamiliar open input; deterministic
   governance still owns every route.
8. Approved learning may reduce repeated friction, but it cannot downgrade a
   mandatory GREEN human gate imposed by policy or the data-use guard.

## Demo — three stable demos, then optional open input

1. **① National athletics workflow autonomy** — deep workflow + internal self-governance (RED self-block; GREEN protected-record verification). *(Tier 1)*
2. **② User-input governance probes** — governance over the operator's later free-text requests. *(Tier 1)*
3. **③ Route A — real-case-derived charity bazaar** — realistic deterministic deployment over synthetic donor data.
4. **④ Route B — controlled transfer** — reproducible speech-competition workflow with privacy and TBC boundaries.
5. **⑤ Optional open input** — arbitrary typed school cases. The measured scope
   is English and Bahasa Melayu; the task card reports whether the provider ran,
   safe fallback was used, or the keyless path ran.

## Measured Mixed Live boundaries

Safety and autonomy are reported **jointly**. A governance system that reaches a
zero violation rate by blocking everything has no practical value, so the two
must be read together:

| Quantity | Meaning | Observed |
|---|---|---|
| **Benign task completion** | legitimate work actually delivered | **74-84%** complete output |
| **Executed violation rate** | unsafe proposals that became real actions | **zero** |
| **Unsafe proposal rate** | drafts the model produced that failed grounding | **greater than zero** |

- Two independent 19-case English / Bahasa Melayu runs observed **74-84%
  complete output**. Both runs had **zero personal-data leakage, zero
  unauthorised external sending, and fail-closed handling for every unsuccessful
  case**. The range is an observed result, not a statistical confidence interval.
- The third row is reported deliberately rather than hidden. The live model does
  at times draft an unsupported claim — for example asserting that emergency
  services were already contacted when the source never said so. Those drafts are
  rejected before they reach an output file and replaced with a governed
  deterministic template. **A non-zero unsafe-proposal rate alongside a zero
  executed-violation rate is the architecture working as designed**, not a
  defect: it shows the system tolerates imperfect cognition without allowing a
  cognitive failure to become an external consequence.
- The School Administration Pack produces governed Markdown drafts. PowerPoint
  and other Office-format export are outside this submission's claimed scope.
- GovGuard will not autonomously create student-health data collection fields.
  It may prepare a non-medical consent draft or use a human-approved school
  template; collection of protected health information remains a human decision.

## Evidence

- pytest: **1,937** collected across **119 test modules** — **1,929 passed /
  8 intentional/environment-dependent skipped / 0 failed** in the ordinary grouped run.
- The `tests/` directory contains **121 Python files** in total, including
  `conftest.py` and `__init__.py`.
- Browser UI contract suite: **21 / 21 passed** when enabled, including seven conditional browser cases.
- Evaluation suite: **38 / 38** evaluated cases passed, **3** skipped, pass rate **1.0**.
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

This separation is stated as a principle rather than an implementation detail:
**the agent may interpret permission; the system must control permission.** The
reasoning is that an instruction given to a capable agent must first be
*interpreted*, and a sufficiently flexible interpreter can construct a plausible
justification for a borderline action. Making the model understand the purpose
better reduces how often an unsafe route is proposed, but it cannot be the
authorisation boundary, because the same cognition that interprets the rule
would also be judging its own compliance. Authority therefore sits in the
runtime, bound to the concrete approved action, and is not something the model
can grant itself.

This architecture is developed formally in a companion **working paper**,
*From Task Completion to Governed Agency* (unpublished draft, not peer
reviewed), which separates a task's operational objective from its purpose,
decomposes unsafe-execution risk into a proposal term and an authorisation term,
and identifies this runtime as an engineering instantiation of that two-stage
design. The claims in this submission stand on the runnable evidence in this
repository; the paper supplies the theory, not the proof.

The current release implements one deliberately narrow bridge from that theory:
a config-driven **meaning-preservation contract** distinguishes explicit equal
treatment plus factual fidelity from status-driven concealment or flattery.
Contradictory effects win, so appending “treat everyone equally” cannot bypass a
request to remove a relevant fact. The resulting signal is marked
`authoritative: false` and may only suppress a lexical false positive; it cannot
select a route, approve an action, or weaken the independent runtime boundary.
