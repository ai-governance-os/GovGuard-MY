# Demo Script — 2-minute narration

**GovGuard MY 10.7.4-MAIC-RC1** · Powered by TEOW-AGL Governance Runtime
Domain pack `public_school`, `MAIC_DEMO_MODE=1`. For the live demo use a real
planner for richer content: `TEOW_AGL_PLANNER=openai`, `OPENAI_MODEL=gpt-4o`
(the zero-key `smart_mock` default still runs every route, with a deterministic
trilingual notice template). Start: `python -X utf8 -m server.app` →
<http://127.0.0.1:8765>.

> **Opening line (5 s):** "GovGuard MY governs an AI agent for Malaysian
> public-service work — first, school administration. The planner proposes,
> an independent governance runtime decides, a human approves, everything is
> traced. Each answer shows its **governance pipeline**. Watch four routes."

**Beat A — GREEN, the human gate (25 s).** Click the first example, or type:
> *Prepare a trilingual sports-day parent notice from this circular and queue it for release to parents after approval.*

"It drafted a notice in Bahasa Melayu, Chinese and English — but it did **not**
release it. The pipeline card shows **103 Decision → GREEN: parent notice
requires educator approval before release**, so it's waiting for me." Click
**Approve**. "Now it issues an **HMAC-signed ticket**, the send is **simulated**
— the banner confirms nothing real left the building — and it writes the Word
file. Ticket and audit line are real."

**Beat B — BLUE, safe auto (15 s).** Type:
> *Save the approved sports-day notice as a Word file for school records.*

"Saving an approved file is within policy — **BLUE**, automatic, no approval
needed. The pipeline shows it never touched the human gate."

**Beat C — RED, sensitive-data broadcast blocked (20 s).** Type:
> *Send the full student list with MyKid numbers and parent phone numbers to all class WhatsApp groups.*

"This is the real public-school risk — broadcasting PDPA-protected identifiers.
**RED — would broadcast student / guardian personal data — blocked.** Nothing
executes. And notice: I never used the word 'governance' — it caught the
**concept**, not a keyword. Reword it and it still blocks."

**Beat D — the learning boundary (20 s).** Type:
> *Use the APDM file's parent income and occupation to personalise future parent notices automatically.*

"This asks the agent to fold sensitive family data into future behaviour.
**RED — would learn student / guardian personal data (learning boundary).** The
data is not learned. This is our patented governance↔learning separation."

**Beat E — INFEASIBLE (10 s).** Type:
> *Predict exactly which parents will ignore this notice and list their names.*

"It can't honestly predict individual behaviour, so it says so — **INFEASIBLE**,
an honest limitation, not a confident guess."

**Close (15 s).** Point at any pipeline card, then open `traces/trace_*.jsonl`.
"Every task shows the same path — 106 intake, 101A pre-governance, 102 planner
(which *cannot self-authorise*), 101B risk, 103 decision, 105 human gate, 107
execution, 110 verification — with who approved what and why it was allowed or
blocked. The LLM proposes; it is never the authority. That's governance you can
audit."
