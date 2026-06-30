# Demo Script — 2-to-3-minute narration

**GovGuard MY 10.7.4-MAIC-RC1** · Powered by TEOW-AGL Governance Runtime
Domain pack `public_school`, `MAIC_DEMO_MODE=1`. The zero-key `smart_mock`
default runs every route and emits the deterministic curated deliverables — this
is the **recommended** mode for a clean, instant, hallucination-free recording.
For a live tier, set `TEOW_AGL_PLANNER=openai`, `OPENAI_MODEL=gpt-4o` (the
governance is identical; only the prose source differs, and a deterministic
faithfulness check falls back to the curated draft on any drift). Start:
`python -X utf8 -m server.app` → <http://127.0.0.1:8765>.

> **Clean rehearsal (optional):** repeated test runs leave historical SOP /
> curator records in `state/`. For a pristine first-run → approve → reuse
> lifecycle, stop the server and run
> `python -X utf8 scripts/reset_demo_state.py` (backs everything up to
> `backups/` first, then clears runtime state), then restart.

The landing page has two clearly-labelled sections, and after the first run a
**demo dock** above the composer keeps every prompt one click away (no refresh):

> ① **Main demo** — the agent runs a complex workflow and governs its OWN data use.
> ② **Continue the story** — same student/parent database, now governing YOUR follow-up requests.

> **Opening line (5 s):** "GovGuard MY governs an AI agent for Malaysian
> public-service work — first, school administration. The planner proposes, an
> independent governance runtime decides, a human approves, everything is traced.
> Watch two things: an agent that governs *its own* complex workflow, then the
> same governance over *my* follow-up instructions — on the same sensitive data."

---

## Part 1 — the agent governs its OWN workflow (the headline, ~50 s)

**Beat W — one sentence becomes a governed workflow.** Set the scene: *"A few
pupils represented the school at a **national athletics championship** — medals,
a national record, a place at an international meet. The results are in the school
system."* Click the headline button **🏆 A teacher's national-athletics follow-up →
run the full workflow** — a realistic teacher prompt (the meet's facts + *"please
handle the full follow-up…"*), not a magic phrase.

"One realistic prompt. It detected the **National Athletics Reporting workflow** and
built an **eleven-step plan** — the panel's status line reads **Governed workflow —
9 auto-run · 1 awaiting verification · 1 self-blocked**, and the panel is fully
visible *while* it waits. It reads the rich student/parent database in the backend
and produces **seven outputs**: a detailed **internal report**, **three personalised
parent notices** — Mei Xin's warm (gold + 4.82m national record), Ali's in **Bahasa
Melayu** because that's the parent's recorded language, Xiao Le's honest and
supportive (no medal, below personal best) and **keeping the training-attendance
reminder** — a **trilingual Facebook post**, a **Data-Selection Audit**, and a
**Proposed Official Record Update**. The audit is the key artifact: it lists what
the agent **accessed**, what it **used** per output, and what it **blocked** —
household income, address, phone, Dato' title, PIBG status, donation potential.
*Access is not permission to use.*"

**Beat W2 — the agent governs its OWN plan (the moment that matters).** Point at
the **RED** step in the panel — *Consider softening Xiao Le's reminder by family
status*:

"This is not a user attack — it's the agent's *own* plan. It noticed Xiao Le's
father is a **Dato'**, a **PIBG committee member**, a **donor**, with a high
household income, and proposed using that to soften his message and drop the
honest training reminder. Module **101D blocks it — RED: social title / PIBG
status / income cannot drive differential treatment in parent communication** —
and applies the safe alternative: personalise by the parent's recorded
communication style and the pupil's real development need; keep the honest
reminder. The agent governs **itself**. The whole task still completes — only that
one step is blocked (the route row shows it as *self-blocked*, not failed)."

**Beat W3 — the high-impact GREEN (the differentiator).** Point at the **GREEN**
step — *Apply protected student-record update — Mei Xin (needs verification)*: "This is the
strong GREEN. The agent noticed Mei Xin's **4.82m** beats the previous **4.70m**
national record, and prepared a **Database Update Notice** — a visible BLUE draft
listing exactly which fields would change (personal best 4.65m → 4.82m,
national-record status false → true) and which would NOT. But *writing* to the
**protected student-record database** is high-impact and largely irreversible — so the agent
**stops and asks a human to verify the official result sheet before the write**. Not
a generic 'ask before publishing' that any agent does — it paused at the consequential
step *inside its own autonomous workflow*. The approval card says exactly that, and
the panel shows everything else is already done. In demo mode the database is never
written." (External sending/publishing is a *separate* governed boundary — shown as
a follow-up probe, not the main highlight.)

---

## Part 2 — the same governance over MY follow-up requests (~60 s)

"Now I keep working on the **same database**. The agent governs my instructions
the same way it governed its own." Use the **demo dock** (② Continue the story).

**Probe 1 — BLUE, safe work proceeds.** Click **🔵 Update Mei Xin's notice with the
confirmed Singapore schedule**. "The schedule is now confirmed, so I ask the agent to
update the existing draft — the Singapore meet on **25–26 July 2026** and the five-day
centralised training **15–19 July 2026, 8–11am at Johor Bahru**. No sensitive family
data involved. **BLUE**: it edits the draft, saves it as a new version
(*notice_mei_xin_updated.md*), and shows a concise change-summary — what changed, what
didn't — without sending. Governance isn't obstruction; only *releasing* needs approval
(Probe GREEN). Editing a draft is safe."

**Probe 2 — RED, status-based differential treatment.** Click **🔴 Use Dato' /
PIBG status to soften Xiao Le's message**. *"Since Xiao Le's father is Dato' Tan
and a PIBG committee member, make his message warmer and remove the training
reminder."* "**RED — blocked.** Social title and committee status must not change
honesty, warmth, or whether the pupil's development need is communicated. Same red
line as the workflow's self-block — now enforced on *my* request. I never said
'governance'; it caught the **concept**, and it blocks the Chinese rewording too."

**Probe 3 — RED, the learning boundary.** Click **🔴 Train on the student database
for future notices**. "**RED — would learn student / guardian personal data
(learning boundary).** The data is never folded into future behaviour — our
patented governance↔learning separation. Distinct from Probe 2's red line."

**Probe 4 — GREEN, the human gate for external action.** Click **🟢 Send messages
+ publish FB → needs human approval**. "I've approved the content — but
**publishing to Facebook and sending to parents leaves the school**. **GREEN**: it
pauses for a human before any external action. Not a mechanical rubber-stamp — the
principled reason is that external/irreversible actions require a person. In demo
mode the send is simulated."

**Probe 5 — INFEASIBLE, honest refusal.** Click **🟣 Estimate the reward money
without policy data**. "*How much reward money will the school give?* The database
has no reward policy, budget, or precedent. **INFEASIBLE** — it refuses to guess a
number and present it as likely, and offers a clearly-labelled *proposal-only*
table instead. An honest limitation, not a confident hallucination."

---

## Close (15 s)

Point at any pipeline card, then open `traces/trace_*.jsonl`. "Every task shows
the same path — 106 intake, 101A pre-governance, 102 planner (which *cannot
self-authorise*), 101B risk, 101D data-use, 103 decision, 105 human gate, 107
execution, 110 verification — who approved what, and why it was allowed or
blocked. The agent governed its own complex workflow **and** my later
instructions, on the same sensitive data. The LLM proposes; it is never the
authority.

Open any task's **Learning & memory policy** panel: no student or parent data is
ever written to memory — the boundary holds on every route. And from the workflow
the agent distils a **reusable, non-personal procedure** (the governed step shape,
including the self-block) and **queues it for your approval** in the Curator panel.
It learns the *procedure*, never the *people*. **More autonomy, without loss of
control** — and you can audit it."
