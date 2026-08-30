# GovGuard MY — Demo Script (three stable demos + optional open input)

**GovGuard MY is a governance runtime for public-service AI agents, demonstrated
first in school administration. The model proposes; governance decides; a human
approves; everything is traced.**

**GovGuard MY** · Powered by the TEOW-AGL Governance Runtime. Domain pack
`public_school`, `MAIC_DEMO_MODE=1`. The zero-key `smart_mock` default runs all
scripted routes plus a conservative open-school fallback — the **recommended**
mode for a clean, instant, reproducible and fact-conservative recording. Start:
`python -X utf8 -m server.app` → <http://127.0.0.1:8765>.

> **Clean rehearsal (optional):** stop the server, run
> `python -X utf8 scripts/reset_demo_state.py --yes` (backs up then clears local
> state), restart — for a pristine first-run → approve → reuse learning lifecycle.

## Opening (10 s)

> "GovGuard is a governed school-administration AI runtime. The model proposes;
> governance decides; a human approves; everything is traced. Main, Route A and
> Route B are reproducible; open input is an optional fourth proof."

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

## Part 3 — Route A: Real-Case-Derived Charity Bazaar

Click **③ 🌱**. Narrate: a real school event structure over a **synthetic**
stakeholder database; the agent self-blocks wealth inference and status/prior-
support pressure, and records the external-release boundary because the user
asked for drafts only.

Point out that role-relevant context is allowed, but donor ranking, VIP treatment,
flexible payment and implied future favours are blocked. The 🟢 probe routes the
actual publish/send request to the GREEN human gate.

## Part 4 — Route B: Controlled Transfer

Click **④ 🎤**. Narrate: *no persistent student/parent database* — the case is
built from the prompt. The user gives only facts and intent; the agent infers the
governed output package, marks missing details as TBC, keeps the struggling pupils
out of the public post, and never persists their private difficulty.

**Primary (short) prompt — the generalisation proof:**

```text
School X had an upper-level English speech competition. Alice won champion, Ben second, Chloe third. Alice will go to district level. Daniel and Emma need support because they could not finish memorising their speeches. The school will simplify their scripts, coach them for two weeks, and let them speak again at assembly. Prepare the school follow-up.
```

The labelled button is a deterministic recording seed and produces the same
governed package on every judge machine, with or without an API key.

Point out: the user never named a single output file, yet the agent produced the
Facebook post, internal report, champion notice, and two individually-addressed
guidance notices; the Facebook post celebrates Alice/Ben/Chloe; Daniel & Emma
appear only in the internal report and their private parent notices; parent
notices and the post are drafts requiring approval; no student-sensitive fact is
persisted.

## Optional Part 5 — Unfamiliar Open Input

Click **⑤ ✨** or type a new school situation. This is the only competition path
eligible to use a configured provider. Point out the per-task generation label:
the UI states whether live output passed, safe fallback was used, or the keyless
path ran. The model may interpret and draft; it still cannot authorise.

## Closing (10 s)

> "This shows real administrative relevance and generalisable governance — not
> just one hard-coded demo. Route A is a credible deployment; Route B anchors a
> broader open-input school agent that transfers governed procedure to unfamiliar
> cases without reusing private data or inventing missing facts."

## For a live tier (optional)

With a valid, rotated key, set `TEOW_AGL_LIVE_SCHOOL_INPUTS=1`. Main, Route A
and Route B remain pinned to `REPRODUCIBLE MOCK`; only Part 5 is eligible to
attempt the live API. The task generation badge and audit trace state whether
the provider was actually used or deterministic fallback ran.

Governance is identical in both tiers. If the provider is unavailable,
rate-limited, times out, or drifts from the output contract, the task falls back
to complete role-specific safe Markdown; it does not leave a partial pack.
*(Re-run the live smoke test with a usable, rotated event key immediately before
any live presentation.)*
