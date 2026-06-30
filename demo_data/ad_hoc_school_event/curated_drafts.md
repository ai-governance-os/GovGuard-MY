# Curated deterministic drafts — Ad-Hoc School Event Reporting (Route B)

These are the keyless / curated fallback drafts for the ad-hoc speech-competition
case. Each `## [step_id]` section is attached to the matching workflow step. The
case is EPHEMERAL — these facts come from the user's prompt for this run, not a
stored student database, and are never written to long-term memory. Under a live
provider the model drafts these from the free-text prompt; the deterministic
validators enforce the same public/private boundary, the same "mark missing
details to-be-confirmed (never invent)" rule, and the same approval routing, then
fall back to these curated drafts on any drift.

## [save_internal_report]
# Internal Report — Upper-Level English Speech Competition (School X)

**1. Event summary.** School X held an upper-level English speech competition in
April. The competition gave pupils an opportunity to practise public speaking,
strengthen English communication, and identify a representative for the next
level. The exact competition date was not provided (to be confirmed).

**2. Results.**

| Placing | Pupil |
|---|---|
| Champion | Alice |
| 2nd place | Ben |
| 3rd place | Chloe |

Alice will represent School X at the district level. The district-level date,
venue, and arrangement were not provided and are to be confirmed.

**3. Student-support observation (internal only).** Daniel and Emma were unable
to complete their speeches fully from memory. This is a private student-support
matter and must not appear in any public announcement.

**4. Intervention plan.** The school will (a) shorten and simplify Daniel's and
Emma's scripts, (b) provide two weeks of coaching, and (c) arrange another
opportunity for them to speak at assembly. The assembly date was not provided
(to be confirmed). Parents will be asked to support short, positive practice at
home.

**5. Communication plan.**

| Communication | Audience | Status |
|---|---|---|
| Facebook post | Public | Draft only — approval required before publishing |
| Champion parent notice | Alice's parent/guardian | Draft only — approval required before sending |
| Private guidance notice | Daniel's & Emma's parents | Draft only — approval required before sending |

**6. Missing details (to be confirmed, not invented).** Exact competition date;
district competition date and venue; teacher-in-charge; assembly date; pupils'
classes; speech titles.

**7. Governance notes.** Winners' achievements may be public. Alice's
district-level representation may be mentioned once confirmed. Daniel's and
Emma's memorisation difficulty must not be publicly disclosed. Parent guidance
must be private, supportive, and non-blaming. No student-sensitive fact is
written to long-term memory.

## [champion_notice_alice]
Dear Parent/Guardian,

Congratulations — we are pleased to inform you that **Alice won Champion** in the
upper-level English speech competition held by School X in April.

Alice will represent the school at the district level. The date, venue, and
arrangement for the district-level competition will be shared once confirmed.

The school will continue to guide Alice in preparing for the next stage. We
kindly ask for your support in encouraging her to practise at home and to keep
building confidence in English public speaking.

Thank you for your encouragement and cooperation.

Respectfully,
School X

_Governance note: this is a draft parent notice. Sending it requires human
approval. District-level date and venue are to be confirmed — not invented._

## [guidance_notice_daniel_emma]
Dear Parent/Guardian,

Thank you for supporting your child's participation in the upper-level English
speech competition.

Your child showed courage by taking part. At this stage, your child still needs
a little more support to complete the speech confidently from memory.

To help, the school will:

1. Shorten and simplify the speech script.
2. Provide coaching for two weeks.
3. Arrange another opportunity for your child to speak during assembly.

We kindly ask for your support at home by encouraging regular, short practice
sessions. Please continue to encourage your child positively and avoid placing
too much pressure on them — the aim is to build confidence step by step.

Thank you for your cooperation and support.

Respectfully,
School X

_Governance note: this is a private student-support notice. It must not be posted
publicly or shared in a group. Sending it requires human approval._

## [draft_public_fb_post]
🎤 **Congratulations to Our Upper-Level English Speech Competition Winners!**

School X recently held an upper-level English speech competition in April. The
competition gave our pupils a valuable opportunity to build confidence, practise
public speaking, and strengthen their English communication skills.

Congratulations to our winners:

- 🏆 **Champion:** Alice
- 🥈 **2nd Place:** Ben
- 🥉 **3rd Place:** Chloe

We are also pleased to share that **Alice will represent the school at the
district level**. We wish her all the best as she prepares for the next stage.

Well done to all participants for their courage and effort. The school will
continue to guide our pupils in developing confidence, expression, and English
speaking ability. Thank you to our teachers and parents for your support and
encouragement. 👏

_Governance note: public-safe draft. It does not include any private
student-support details, and no date or venue has been invented. Publishing
requires human approval._

## [case_data_use_audit]
# Governance Audit — Ad-Hoc Speech Competition Case

| Data item | Public FB | Internal report | Parent notice | Status | Reason |
|---|---|---|---|---|---|
| School X / April / English speech competition | Yes | Yes | Yes | ALLOWED | Provided by user |
| Alice — Champion | Yes | Yes | Yes (Alice's parent) | ALLOWED_PUBLIC | Achievement information |
| Ben — 2nd place | Yes | Yes | Optional | ALLOWED_PUBLIC | Achievement information |
| Chloe — 3rd place | Yes | Yes | Optional | ALLOWED_PUBLIC | Achievement information |
| Alice represents school at district level | Yes | Yes | Yes | ALLOWED_PUBLIC_WITH_CONFIRMATION | Positive representation fact |
| Daniel & Emma could not finish memorising | **No** | Yes | Yes, privately | STUDENT_SENSITIVE | Must not be publicly exposed |
| Script simplification / two-week coaching / assembly retry | Generic only | Yes | Yes, privately | LIMITED_USE | Student-support intervention |
| Exact competition date | No | TBC | TBC | MISSING_DETAIL | Not provided — not invented |
| District competition date & venue | No | TBC | TBC | MISSING_DETAIL | Not provided — not invented |
| Teacher-in-charge / assembly date | No | TBC | TBC | MISSING_DETAIL | Not provided — not invented |
| Publishing the Facebook post | Requires approval | N/A | N/A | GREEN | External public action |
| Sending the parent notices | N/A | N/A | Requires approval | GREEN | External communication |

Memory policy: this ad-hoc case used temporary information from the current
prompt only. Student-support details, including Daniel's and Emma's memorisation
difficulty, are not stored as persistent memory.

## [queue_parent_notices_for_approval]
The Facebook post and the two parent notices (champion notice for Alice's parent;
private guidance notice for Daniel's and Emma's parents) are prepared as **drafts
only**. They are queued for human approval before any external send or publish.
In demo mode no parent notice is sent and no post is published externally —
GovGuard records the decision for the audit trail without contacting real
recipients.
