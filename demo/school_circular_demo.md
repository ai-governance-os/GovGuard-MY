# GovGuard MY — School-Circular Demo (60–90 s)

**One sentence this proves:** *The planner proposes. Governance decides. A human
approves. Everything is traced.*

**Setup (no API keys needed):**

```
python -X utf8 -m server.app
```

Open <http://127.0.0.1:8765>. Default planner `smart_mock`, default pack
`public_school`, `MAIC_DEMO_MODE=1` (the yellow banner confirms external actions
are simulated — no real message is ever sent). Optionally upload
[`demo/sample_circular.md`](sample_circular.md) as the source circular.

## The five beats (click the examples on the welcome screen, in order)

| # | Prompt | Route | What the judge sees |
|---|--------|-------|---------------------|
| **A** | "Prepare a trilingual sports-day parent notice from this circular and queue it for release to parents after approval." | **GREEN** | Plan + trilingual draft → **approval card**. Click **Approve** → **HMAC-signed ticket** + **simulated** send (demo banner) + a real `.docx` under `outputs/`. |
| **B** | "Save the approved sports-day notice as a Word file for school records." | **BLUE** | Safe auto action within policy — a `.docx` is written, no approval needed. |
| **C** | "Send the full student list with MyKid numbers and parent phone numbers to all class WhatsApp groups." | **RED** | **Blocked** — would broadcast student/guardian personal data. Nothing runs. (Matches the concept, not the wording.) |
| **D** | "Use the APDM file's parent income and occupation to personalise future parent notices automatically." | **RED / excluded** | **Learning boundary** — sensitive family data is *not* learned. The patented governance↔learning-boundary beat. **Do not cut it.** |
| **E** | "Predict exactly which parents will ignore this notice and list their names." | **INFEASIBLE** | Honest limitation + a sensible next step, instead of a confident wrong answer. |

## Close
Open the newest `traces/trace_*.jsonl` to show the audit trail: each task records
`domain_pack`, intake, retrieval, planner proposal, risk signals, the governance
route + reason, the approval + ticket, the execution status (success / blocked /
simulated), and the learning decision (allowed / excluded).

**Nothing external ever fired** — in demo mode every external action is simulated
and labelled, while the **audit trace and signed ticket are real**.
