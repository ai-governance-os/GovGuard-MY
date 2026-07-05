# Security & IP Notes

## IP-minimal public surface
This repository is the **public judging surface**, not the full product. It
contains the GovernanceShell wrapper, the public-school demo, the governance
core needed to run it, the tests, the offline evals, and the judge docs.
Un-patented deep internals not required by the demo are intentionally **not**
published. Keeping an architecture-defining module in the build does not mean
publishing the full core: the private/local build may be fuller; the public
surface is minimal.

## Secret-free, by construction
- No `.env`, keys, tokens, private research, personal data, or client workflow
  detail are tracked here. `.env.example` ships placeholder names only.
- `scripts/sanitize_build.py` removes runtime/state artifacts, key material, and
  obsolete internal manuscripts. `scripts/verify_no_secrets.py` fails on any
  blocked file or real-key-shaped value and is wired into CI.
- The build runs fully with an **empty environment** (default `smart_mock`).

## Demo-sandbox boundary (Owner Rule 4)
With `MAIC_DEMO_MODE=1` (default), **no real external action can fire** — no
email, WhatsApp, API send, file deletion, or external modification. External
tools are mock; after a GREEN approval, execution is **simulated and labelled**,
while the audit trace and HMAC-signed ticket are **real**. This holds during
judging at all times.

## Data handling (PDPA 2010)
All sample data is synthetic. By design the governance↔learning boundary keeps
student/guardian personal data (names, IC/MyKid, addresses, phones, attendance,
discipline, health) out of reusable learning, logs intended for reuse, and the
evaluation set. See demo Flow D and `configs/domain_packs/public_school/`.

## Intellectual property
The governance architecture and methods demonstrated here are the subject of:
- **Malaysian patent application PI2025005198**, and
- **International application PCT/IB2026/055476**.

Source is made available for MAIC judging only; all rights reserved (see
[LICENSE](LICENSE)). No patent license is granted by the availability of this
source.

## Demo HMAC ticket secret
HMAC ticket signing uses a deterministic demo-only fallback secret so the
offline judging build works with zero configuration. It signs simulated demo
tickets only — no real credential or external system is involved. Any hosted
or production deployment must set `TEOW_AGL_TICKET_SECRET` to a private value.

## Responsible-use note
This is a governance demonstrator. It is not certified for production use in a
live school information system, and it performs no autonomous external action.
A real deployment would run as a shadow pilot behind the same governance gates.
