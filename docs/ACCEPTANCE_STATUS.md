# Executed evidence and remaining gates

This file maps the implementation to the release intent. The **129 Python test methods** and **22 independent fixture oracle records** are different counting units from the original **98 cross-release acceptance cases**. Neither test names nor total counts make later guarantees pass.

| Requirement group | R0 evidence | Explicit remaining boundary |
| --- | --- | --- |
| Exact matching and selectivity | `test_matching.py`, the 22 authored fixture oracles, `test_integration.py`; exact F/S/C/U, hard gates, reciprocal policies, missingness, monotonicity, full scan, stable ties and capacity. | Extraction accuracy and actual usefulness are unmeasured. |
| Provenance and fixtures | `test_fixtures.py`; counts/hashes, owner-role linkage, duplicate and invalid references, strict schema, fictional origin. | A future real importer must verify selected source/role semantics separately. |
| Review, consent and disclosure | `test_consent.py`, `test_interview.py`; exact audience/purpose/value/version, stale revision/replay, source invalidation, sensitive restrictions, separate retention and approval. | This is in-memory policy behavior; no encrypted vault, backup deletion, remote erasure or isolated local model is established. |
| Interviews | Bounded template scheduler, six/twenty limits, neutral single follow-up, no refusal trait, immutable proposals and exact review; integrated score change only after approval. | Actual language-model adherence, full tokenizer budget, natural-language extraction and performance remain R1. |
| Topics and human channels | `test_social.py`; exact bilateral context/capabilities, zero-topic invitation, honest human authorship, no unapproved name/source fields. | UI persistence and live authenticated peers remain later gates. |
| Protocol | `test_protocol.py`; representative states, coordinator and handshake assumptions, typed question scopes, quotas, wrong actor, replay, withdrawal, generic stop and separate contact acceptance. | No full exhaustive state-product proof, live endpoint race, authenticated signaling or packet security evidence. |
| Outbox and receipt | In-memory limits/default and shorter TTL, stable IDs, lost ACK, receiver dedup, block/reconnect membership and asymmetric contact delivery. | No encrypted disk transaction, process-crash recovery or durable dedup across restarts. |
| Private assistance | Explicit local invocation, scoped introduction/chat input, context expiry/withdrawal and no send capability. | No running model, actual isolation or generated-prompt quality measured. |
| Offline portability | The source and installed wheel run under Linux syscall denial with positive controls; source and installed demo JSON are identical. | The run is not a Mac Studio/Ryzen benchmark or a certified Windows/macOS/private-inference sandbox. |
| Abuse and references | Owner-wide scripted quotas, blocks, exact active scopes, synthetic-only participant loader. | Real admission, Sybil/collusion resistance, moderation and legal readiness need live-release work. |

## Backlog delivery map

| Tickets | Delivered implementation |
| --- | --- |
| AM-001–002 | Strict typed models, immutable records, bounded numeric/schema inputs, packaged configuration, CLI and zero-runtime-dependency wheel. |
| AM-003–004 | Fictional corpus, independent expected outcomes, lifecycle/approval/expiry/correction and invalidation. |
| AM-005–008 | Eligibility, fixed atom fit, exact disclosure projection, directional/reciprocal gates, full-scan ranking and capacity. |
| AM-009–010 | Transport-free protocol state machine, redacted effects, deterministic explanations and run/data fingerprints. |
| AM-015–019 | Scripted interview/revision, selectivity, topic cards, bilateral channel/outbox simulation and private coach routing. |
| AM-011 | Complete CLI journey, inspectable HTML/JSON/JSONL output and explicit unsupported real-data paths. |
| AM-012 | Packaged adversarial/regression suite, independently reviewed oracles and two detected deliberate source mutations. |
| AM-013 | Actual clean wheel install, installed-suite run, source/installed reproduction and external isolation controls. |
| AM-014 | Release report, command logs, exact output/hash evidence and this boundary map. |

The release is an **R0 synthetic reference alpha**. This map does not promote production security clauses to passed status. The original catalogue remains the roadmap for the actual release boundary that can test each clause.
