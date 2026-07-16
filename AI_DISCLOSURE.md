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
engine. The internal package/version identifiers (`teow_agl`, `10.8.x`) reflect
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

**Workflow content vs governance — who does what.** The Workflow Autonomy layer
(modules 102W workflow resolver, 101D data-use guard) is **deterministic and
offline**: routing (BLUE/GREEN/RED/INFEASIBLE), the human gate, privacy floors,
and the guardian-income self-block are decided by the governance layer, not by
any LLM. In Mixed Live, a provider may propose closed-schema semantic labels,
Response Pack coverage, and document prose. It never changes **who routes,
approves, or blocks**. The public reproducible build runs on `smart_mock` with
no key (more conservative content, identical governance). If the live provider
is unavailable or its output breaks the contract, the system returns a complete
role-scoped Markdown fallback. Even with a real key, `MAIC_DEMO_MODE=1` keeps
every external publish or send **simulated** — no real Facebook post, email, or
message is ever sent.

**Understanding free-form input vs deciding the route.** For open-ended requests,
*understanding* and *deciding* are separated. Understanding may use an LLM to
propose closed-vocabulary data-use concepts (socioeconomic data, differential
treatment, public PII, health/discipline); with no key, a deterministic compiler
produces a conservative interpretation. **Deciding** the route is always the
deterministic governance core (101D + 103), which maps the concepts and requested
actions to fixed rules. The LLM cannot authorise a forbidden data use.
Uncertainty can result in one critical clarification, TBC fields, INFEASIBLE,
GREEN approval, or a domain-boundary answer; it never becomes a silent external
action.

**Data — person-level samples synthetic; student data excluded from learning.**
All person-level sample records in this repository are **synthetic or redacted**.
Route A may use a real-case-derived event/process structure under acknowledgement,
but it contains no real student, parent, donor, or staff personal data. By design, the
governance↔learning boundary keeps student/guardian identifiers, IC/MyKid
numbers, addresses, phone numbers, attendance, discipline, and health records
out of any reusable learning store (demonstrated by demo Flow D and the
`public_school` learning-exclusion pack).

**Demo mode — no real external actions.** When `MAIC_DEMO_MODE=1` (the default),
no real email, message, API send, file deletion, or external modification can
fire. External actions are simulated and clearly labelled; the audit trace and
signed ticket remain real.
