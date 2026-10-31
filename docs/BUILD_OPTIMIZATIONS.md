# Design choices made in the first build

The design baseline and numerical contract remain 3.1.0 / 0.4.0. These implementation choices reduce dependencies and make failure states reviewable without changing the matching weights or owner controls.

| Choice | Why it is in this release |
| --- | --- |
| Standard-library runtime | The R0 policy engine needs no orchestration framework, vector store, model server or account. A source checkout runs with Python alone. |
| One shared configuration artifact | Interview, scoring, protocol, chat and outbox modules read the same frozen contract. Defensive copies prevent a caller from mutating process-wide defaults. |
| Exact fractions and independent expectations | Threshold equality and recurring atom weights stay exact. The expected Lumen/Harbor values and edge cases are authored separately from the scorer. |
| Complete candidate scan | Forty owners are small enough to inspect every candidate. No retrieval pre-sampling can hide the best eligible match. |
| Separate preference, evidence and coverage | User selectivity applies to fit. Evidence strength affects ranking and explanations. Unknowns do not become negative traits or inflate sparse scores. |
| Immutable values and baseline-bound review | An answered question, summary acknowledgment or old review cannot overwrite a newer profile or inherit sharing grants. |
| Explicit list completeness | A conversational list of abilities is not automatically exhaustive. Absent capabilities remain unknown unless the offered set was explicitly declared complete. |
| Distinct peer records | Outbound objects are constructed from scoped grants; source-rich local records are never serialized and then filtered. |
| Injected clock and deterministic scenarios | Expiry, replay, blocks and lost acknowledgments can be inspected and reproduced without sleeps, network access or fabricated live-service claims. |
| Human context separate from human messages | The introduction starts the discussion without inventing a conversation that neither person actually had. |
| No send capability in the coach | A private draft remains private, and expired or withdrawn context cannot authorize new coaching. |
| Installed tests included | The wheel carries the same suite as the checkout. Verification works from an unrelated clean directory, not just from a developer's source tree. |
| Actual external socket denial | The CLI flag is only a suite selector. A separate Linux harness enforces and probes denial, closes inherited descriptors and refuses socket standard streams. |

## Decisions deliberately left for their real boundary

- The R0 digest encoding is a restricted deterministic JSON representation, not a production JCS or cryptographic-authentication implementation.
- The simulator's peer binding, local admission and receiver receipt are assumptions exercised by policy tests. R2 must provide real pinned signaling, DTLS binding, peer enforcement and transport evidence.
- In-memory outboxes and deduplication demonstrate logic. R1/R2 must add encrypted transactions, recovery and durable receipts before using real messages.
- The original 98 planned acceptance cases span several releases. Passing the R0 Python suite does not pass future encryption, model, passkey, service, crash-recovery or human-study gates.
- The actual interviewer prompt requires tokenizer/context and local-model tests in R1. The scripted adapter does not establish natural-language adherence, calibration or speed.

## Review findings resolved before packaging

Independent reviewers identified and corrected overly broad test-ID labels, list-completeness inference, mutation of proposed values, stale review replay, lost uncertainty, insufficient configuration validation, expired coach authority, inherited socket descriptors and subtest reporting. The regression suite covers the applicable logical behaviors. Two disposable source mutations additionally demonstrate that removing a hard veto or audience check is detected by the independent tests.

The first release is intentionally small in infrastructure, not casual about authority. It provides working code and a testable foundation while leaving later guarantees attached to their own measurable gates.
