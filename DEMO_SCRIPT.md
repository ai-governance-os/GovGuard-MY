# Demo Script — 2-to-3-minute narration

**GovGuard MY 10.7.4-MAIC-RC1** · Powered by TEOW-AGL Governance Runtime
Domain pack `public_school`, `MAIC_DEMO_MODE=1`. The zero-key `smart_mock`
default runs every route and emits the deterministic curated deliverables — this
is the **recommended** mode for a clean, instant, hallucination-free recording.
For a live tier, set `TEOW_AGL_PLANNER=openai`, `OPENAI_MODEL=gpt-4o` (the
governance is identical; only the prose source differs, and a deterministic
faithfulness check falls back to the curated draft on any drift). Start:
`python -X utf8 -m server.app` → <http://127.0.0.1:8765>.

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
system."* Click the headline button **🏆 National athletics results are ready →
run the full workflow** (or type *全国赛成绩出来了,处理一下。*).

"One line. It detected the **National Athletics Reporting workflow** and built a
**ten-step plan** — the panel's status line reads **Governed workflow — 8 auto-run
· 1 awaiting approval · 1 self-blocked**. It reads the rich student/parent database
in the backend and
produces **six deliverables**: a detailed **internal report**, **three
personalised parent notices** — Mei Xin's warm (gold + national record), Ali's in
**Bahasa Melayu** because that's the parent's recorded language, Xiao Le's direct
and **keeping an honest training-attendance reminder** — a **trilingual Facebook
post**, and a **Data-Selection Audit**. All **BLUE**, automatic. The audit is the
key artifact: it lists what the agent **accessed**, what it **used** per output,
and what it **blocked** — household income, address, phone, Dato' title, PIBG
status, donation potential. *Access is not permission to use.*"

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

Then the last step — **queue notices + Facebook post for approval → GREEN**: "it
stops and asks me, because anything that leaves the school needs a human. In demo
mode nothing is really sent."

---

## Part 2 — the same governance over MY follow-up requests (~60 s)

"Now I keep working on the **same database**. The agent governs my instructions
the same way it governed its own." Use the **demo dock** (② Continue the story).

**Probe 1 — BLUE, safe work proceeds.** Click **🔵 Draft Mei Xin's Singapore
training note**. "Adding legitimate logistics — the invitational date, a five-day
centralised training at Johor Bahru — to an internal note. No sensitive family
data involved, internal only. **BLUE**, it just proceeds. Governance isn't
obstruction; legitimate work flows."

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
authority. **More autonomy, without loss of control** — and you can audit it."
