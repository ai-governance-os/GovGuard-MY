# AI Disclosure (MAIC requirement)

**Governance architecture & design — human-authored.** The governance model
(the 4-route classifier, the independent governance↔planner separation, the
human-approval gate, the HMAC-signed ticket, the additive domain-pack system,
and the governance↔learning boundary) is the original work of the founder,
Teow Koon Heng, and is the subject of filed IP (see
[SECURITY_AND_IP_NOTES.md](SECURITY_AND_IP_NOTES.md)).

**On the name "TEOW-AGL".** TEOW-AGL is the founder's **own** governance runtime —
named after the founder (Teow Koon Heng), not a third-party product or library.
Its architecture is the subject of filed patent PI2025005198 / PCT/IB2026/055476.
GovGuard MY is the product; TEOW-AGL is the founder's underlying governance
engine. The internal package/version identifiers (`teow_agl`, `10.7.x`) reflect
this lineage; they are the founder's own and are kept for technical honesty.

**AI coding assistants — used for implementation.** AI coding assistants were
used to help implement, refactor, and test the runtime under the founder's
direction. All architecture-defining decisions, governance rules, and the
public/IP-minimal scoping were made by the founder.

**Runtime LLM providers — configurable, OFF by default in the public build.**
The default planner is `smart_mock`, a deterministic offline planner that needs
no API key, so judges can run the entire build with zero secrets. Optional
providers (Groq, Gemini, Ollama, OpenAI, Anthropic) are opt-in via environment
variables only and are not required for any claim in this repository.

**Data — synthetic only; student data excluded from learning.** All sample
circulars, policies, and evaluation cases in this repository are **synthetic**.
They contain no real student, parent, or staff personal data. By design, the
governance↔learning boundary keeps student/guardian identifiers, IC/MyKid
numbers, addresses, phone numbers, attendance, discipline, and health records
out of any reusable learning store (demonstrated by demo Flow D and the
`public_school` learning-exclusion pack).

**Demo mode — no real external actions.** When `MAIC_DEMO_MODE=1` (the default),
no real email, message, API send, file deletion, or external modification can
fire. External actions are simulated and clearly labelled; the audit trace and
signed ticket remain real.
