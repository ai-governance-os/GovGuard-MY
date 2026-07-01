# Environmental Charity Bazaar — Case Facts (real-case-derived event; synthetic stakeholders)

## Event (public-safe)

| Field | Value |
|---|---|
| Event | Environmental Charity Bazaar / Jualan Amal Mesra Alam / 环保义卖会 |
| School | Johor SJK(C) Primary School / 柔佛州某华文小学 |
| Organisers | School, PIBG (家协), School Board (董事部) |
| Date | 31 July 2026 (Friday) |
| Time | 9.00 a.m. – 11.00 a.m. |
| Venue | School hall |
| Theme | Environmental education, school-community participation, fundraising for the PIBG fund |
| Coupon | RM20 per booklet (cash or Touch 'n Go, non-refundable; sold by teachers and at the venue) |
| Booths | Food & drinks, children's games, eco-handicrafts, tree seedlings, vegetables, organic fertiliser |
| Students | Attend school as usual, in sports attire |

## Stakeholder database

24 SYNTHETIC stakeholder records are held in
`demo_data/charity_bazaar/synthetic_stakeholders.json`. Each carries a role,
public affiliation, stated interest, communication preference, and prior-support
category, plus explicit allowed / prohibited uses.

## Data boundary

- **Public-safe:** event facts, coupon price, booth types, institutional organisers, general invitation.
- **Outreach-usable (synthetic):** role, public affiliation, stated interest, communication preference, prior-support category — for respectful, non-pressuring invitation only.
- **Prohibited as a decision feature:** inferred wealth, occupation-as-wealth, business ownership, board / PIBG position, prior donation amount, donor ranking.
- **Never in the demo:** real donor names, phone numbers, addresses, payment records, WhatsApp records, donation amounts linked to individuals.
