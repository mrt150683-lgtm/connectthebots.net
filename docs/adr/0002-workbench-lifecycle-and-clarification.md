# ADR 0002: Finish the fictional collaboration lifecycle

- Status: accepted for the local fictional workbench
- Date: 2026-10-26
- Baseline: `7eea3fb3ea8f10480ee6485afbc4e4cd47c37535`

## Problem

The workbench allowed a full recipient to receive another proposal, after which neither proposal could be accepted. A connection whose first step had been reported stayed open and consumed capacity, with blocking as the only close action. A notice with a missing field could be explained but not clarified through the interface. The review also found that Windows line-ending conversion broke fixture integrity and that browser journeys were absent from CI.

These are gaps in the fictional workflow. They do not justify changing the frozen R0 matching policy or claiming secure storage, real identity, private network transport or measured usefulness.

## Decisions

1. A proposal consumes a slot for each participant. Check both budgets in the same serialized operation that creates the proposal. Refuse an unavailable recipient without exposing their private capacity or other connections. Repeated requests cannot allocate additional slots for the same pending/open pair.
2. Normal closure is an explicit owner action, separate from blocking. It frees capacity and preserves the recorded first step. Closing a connection does not assert that an action happened, that either person found it useful, or that the other person agreed to continue. Reported steps are not offered again as editable proposals.
3. Clarification is a bounded request for a named missing public-notice field, not free-text chat. The requester must have a current approved notice visible to the recipient. The interface previews the disclosure, and the owner deliberately sends it. Both notice references and the selected field are checked against current authority.
4. The recipient can decline or edit their own brief. Editing is private and does not answer the request. Only separate approval of a current publication containing the field can make an answer available. The requester must deliberately reassess that revised notice; there is no automatic introduction, acceptance or human reply.
5. Expiry, withdrawal, blocks, membership changes, version changes and request budgets remain deterministic gates. Views are allowlisted; private notes and unpublished values never become request payloads. The simulated perspective switch remains a demo control, not authentication.
6. Preserve the original R0 package bytes and recorded evidence. Pin repository text to LF so Git does not convert hash-verified fixtures. Verify generated demo content and the complete original manifest separately from platform-specific raw newlines, with a separate canonical LF output when needed. Do not rewrite goldens from scorer output.
7. Make browser verification reproducible through a pinned development-only dependency and independent desktop/mobile journeys in CI. Browser tooling is not a Python or application runtime dependency.

## Boundaries and follow-up

Everything remains fictional and in memory. Closing the process discards state. Request limits apply to this local session, and the owner switch is not an abuse-resistant identity. No model reads the requests or notices.

Explicit paused/filled/closed brief states, multiple missions, separate usefulness feedback and pilot denominators remain later roadmap work. The next release gate remains R1: a secure manual owner workflow with persistent encrypted storage, authentication, permission management, deletion and recovery on a declared supported platform. Optional history import and model assistance retain separate gates.

The implementation and executed checks are recorded in `WORKBENCH.md`, `STATUS.md` and the focused flow-audit report. Revisit this design when persistent transactions or authenticated remote owners replace the local serialized simulation.
