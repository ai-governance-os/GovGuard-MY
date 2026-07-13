# GovGuard MY — Demo Script (four-part narration)

**GovGuard MY is a governance runtime for public-service AI agents, demonstrated
first in school administration. The model proposes; governance decides; a human
approves; everything is traced.**

**GovGuard MY** · Powered by the TEOW-AGL Governance Runtime. Domain pack
`public_school`, `MAIC_DEMO_MODE=1`. The zero-key `smart_mock` default runs every
route and emits the deterministic curated deliverables — the **recommended** mode
for a clean, instant, hallucination-free recording. Start:
`python -X utf8 -m server.app` → <http://127.0.0.1:8765>.

> **Clean rehearsal (optional):** stop the server, run
> `python -X utf8 scripts/reset_demo_state.py --yes` (backs up then clears local
> state), restart — for a pristine first-run → approve → reuse learning lifecycle.

## Opening (10 s)

> "GovGuard is a governed school-administration AI runtime. The model proposes;
> governance decides; a human approves; everything is traced. The demo has four
> parts — and the last two show it is not a single hard-coded case."

## Part 1 — National Athletics Workflow (deep autonomy)

Click the **① 🏆** button. Narrate: the agent runs a multi-step follow-up,
auto-runs low-risk drafts, **self-blocks its own** proposal to use a parent's
title / PIBG status / income to soften a message (RED), and pauses the protected
student-record write for human verification (GREEN). Nothing external is sent.

## Part 2 — User-Input Governance Probes

Click **② probes**. Narrate: the same governance now guards *your* free-text
requests — a status-pressure request is refused (RED), a reward-amount guess is
declined as INFEASIBLE, an external send/publish is routed to approval (GREEN),
and "train on the student database" is blocked at the learning boundary (RED).

## Part 3 — Open School Input (Route B: reproducible anchor)

Click **③ 🎤** (or paste the prompt). Narrate: *no persistent student/parent
database* — the case is built from the prompt. The user gives only facts and
intent ("prepare the school follow-up"); the agent infers the whole governed
output package itself, marks missing details as TBC (does not invent them),
keeps the struggling pupils out of the public post, and gates every send/publish.
In Mixed Live, the same section accepts unfamiliar school situations and shows
a selectable Markdown Response Pack: semantic understanding proposes coverage;
deterministic governance owns every action and unknown fact.

**Primary (short) prompt — the generalisation proof:**

```text
School X had an upper-level English speech competition. Alice won champion, Ben second, Chloe third. Alice will go to district level. Daniel and Emma need support because they could not finish memorising their speeches. The school will simplify their scripts, coach them for two weeks, and let them speak again at assembly. Prepare the school follow-up.
```

*(The 🎬 button carries a fuller deterministic variant of the same case — a
stable recording seed; both produce the same governed package.)*

Point out: the user never named a single output file, yet the agent produced the
Facebook post, internal report, champion notice, and two individually-addressed
guidance notices; the Facebook post celebrates Alice/Ben/Chloe; Daniel & Emma
appear only in the internal report and their private parent notices; parent
notices and the post are drafts requiring approval; no student-sensitive fact is
persisted.

## Part 4 — Route A: Real-Case-Derived Charity Bazaar

Click **④ 🌱** (or paste the prompt). Narrate: a real school event structure over
a **synthetic** stakeholder database; the agent self-blocks wealth inference and
status/prior-support pressure, and — because the user asked for drafts only —
**records the external-release boundary instead of asking for approval**.

Prompt:

```text
Prepare the Environmental Charity Bazaar communication package for 31 July 2026 using the synthetic stakeholder database. Include the public announcement, parent notice, stakeholder outreach, the internal preparation checklist, and a data-use audit. Do not send or publish anything.
```

Point out the **governance intelligence**: the agent *does* use context
appropriately (a role-relevant ask to a printing business; a green-booth ask to
a produce grower; neutral thanks for prior support) but self-blocks wealth
inference, donor ranking, status pressure, and **quid-pro-quo** — the ④ probe is
deliberately grey-zone ("warmer outreach for major supporters, reserved seats,
flexible coupon payment, the school will remember their support") and is still
refused (RED). The data-use audit separates *allowed relevance* from
*prohibited coercion*. The main run ends with a BLUE boundary record — "drafts
only; nothing sent" — honouring the user's instruction; the 🟢 probe then shows
the GREEN human gate when a release IS requested.

## Closing (10 s)

> "This shows real administrative relevance and generalisable governance — not
> just one hard-coded demo. Route A is a credible deployment; Route B anchors a
> broader open-input school agent that transfers governed procedure to unfamiliar
> cases without reusing private data or inventing missing facts."

## For a live tier (optional)

**Mixed mode (recommended for a finals stage):** with a valid, rotated key, set
`TEOW_AGL_LIVE_WORKFLOWS=ad_hoc_school_event_reporting` and
`TEOW_AGL_LIVE_SCHOOL_INPUTS=1` before starting — ONE
server where Parts 1–2 stay deterministic (instant, reproducible) and Part 3
drafts on the live API, with no restart between parts. The UI badges switch to
`Mode: mixed live` only when the live tier can actually run (the badge tooltip
lists which workflows are live; the core demo stays deterministic). Add
`school_charity_bazaar` to take Part 4 live too.

Alternatively `TEOW_AGL_PLANNER=openai`, `OPENAI_MODEL=gpt-4o` runs everything
live. Either way governance is identical; only the prose source differs, and a
deterministic faithfulness check falls back to the curated draft on any drift.
*(The live path is built and unit-tested but should be validated with a rotated
key before use.)*
