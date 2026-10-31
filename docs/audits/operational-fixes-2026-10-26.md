# Operational repairs and retest

**PASS for the nine reproduced operational defects in the implemented fictional local slice.** All nine findings from the [baseline audit](operational-qa-2026-10-26.md) are repaired and retested. This does not complete real-owner R1, model integration, scheduling, remote discovery or peer transport.

## Scope and evidence

The owner explicitly requested applying the fixes and retesting. The goal remains **agents operate; people view**, with deterministic permissions and no model as an authority. Baseline: product revision `98dc3b0`, audit commit `749801e`. This report covers the subsequent repair changes on `codex/workbench-lifecycle`, 26 October 2026. Baseline evidence remains intact.

Tests ran on Windows, Python 3.14.3, cryptography 49.0.0 and Chromium desktop/mobile emulation. All identities and inputs were fictional. Database/process interruption checks used disposable instances; no real service or person was contacted. Keys and test tokens were not persisted in evidence. The live demonstration holds its key in memory and uses a separate, expiring read-only viewer credential.

The baseline report retains the full feature inventory and flow trace. Repairs affect response projection/catalogue, receipt pagination, SQLite error classification, search retry authorization, clarification accounting and the read-only viewer. The frozen `src/agentmatch/` core and original fixture/demo records are unchanged.

## Findings closed

| Finding | Repair and observed proof | Verdict |
| --- | --- | --- |
| QA-001 · action contract | Runtime resources return canonical operation descriptors, current references and missing argument paths. Tests execute returned acceptance, agreement, close and clarification descriptors. Legacy manual labels stay separate; viewer actions are empty. | PASS |
| QA-002 · old-only receipts | Viewer starts with newest 50 and follows exclusive older-page cursors. Real browser checks match page sequences to the backend, show a later refusal, and cross the real 2,000-receipt retention boundary with 2,005 operations. | PASS |
| QA-003 · impossible refresh advice | Exhausted access offers a new-key connection and disables futile workspace refresh. Both a one-use key and an actual last-use race returning 429 were exercised; uncharged receipt navigation remains available. | PASS |
| QA-004 · lost search result | A one-use exact search recovers its body after a fresh-process restart without another grant/use. Later withdrawal disappears on recovery; three-recovery cap and revocation refusal observed. Additional tests cover revised/new candidates, expiry, membership/scopes, missions and concurrent recoveries. | PASS |
| QA-005 · permanent clarification budget | Twenty sent/received per rolling 24 hours, five pending per direction; history survives. Actual request/decline/publication loops and restart show no reset before the boundary, renewal afterward, recipient-wide limits, and conservative legacy migration. Time was injected explicitly, not a claimed day-long soak. | PASS |
| QA-006 · mobile overflow | Accepted maximum title/outcome text wraps within a 390px viewport. Browser measurements and inspected screenshots show no horizontal overflow; HTML-like text remains inert. | PASS |
| QA-007 · remaining-use lag | Capabilities are read after the charged workspace read. Both desktop/mobile display zero when the backend has zero. | PASS |
| QA-008 · incomplete response contract | OpenAPI describes envelopes, resources, actions, pagination, recovery and stable error details. All 20 operations' real HTTP responses conform; seven contract tests also execute resource actions and validate recovery/receipt output. Catalogue references resolve to `/openapi.json`. | PASS |
| QA-009 · generic lock failure | Real SQLite writer contention returns 503 `STORAGE_UNAVAILABLE` with retry guidance. No failed effect/use commits; the same request/key succeeds once after unlock. Non-contention SQL errors are not labelled retryable. | PASS |

## Policy choices and limits

Search recovery stores references and authority metadata only, never cached peer content. It can return only currently permitted original notice revisions, for at most three successful recoveries before the original permission expires. Current credential, scope, board membership and mission authority are rechecked. Mutation retries remain receipt-only. Old retry records lacking provenance cannot invent recovery authority and return `SEARCH_RECOVERY_UNAVAILABLE`.

Clarification accounting uses injected real UTC in the durable runtime, including seven-day request expiry. Other fictional domain dates remain simulated. Legacy requests receive one durable first-seen timestamp and count conservatively for a full window. Replacement credentials and restarts cannot reset the limit; same-field/same-notice-revision duplicate restrictions remain. Capabilities show counts and individual rolloff times, not a promise that pending requests will clear.

Action descriptors are guidance within scopes, not permission grants. Execution still owns authorization. Viewer navigation sends only same-origin GETs and never operates the collaboration workflow.

## Verification results

| Check | Observed result |
| --- | --- |
| Full source suite | **263 passed**, zero failures/errors/skips; 23 new regressions |
| Fresh wheel outside source root | **263 passed**, zero failures/errors/skips; imported installed runtime path confirmed |
| Dependency-free path (`python -S`) | **203 passed, 60 explicitly skipped** optional-runtime tests; no failures/errors |
| Actual HTTP business flows | All **20** canonical operations, six workflow groups, response schema validation passed |
| Two-agent journey | Standing mission discovery/consent/step agreement, separate reports/closure, restart, mutation retry, revocation and encrypted canary scan passed |
| Live resilience | **8/8 passed** on final runtime: lost reply/restart, concurrent versions, final-use race, queued expiry/revocation, writer lock, hostile framing, interrupted uncommitted write |
| Viewer operational matrix | **28 checks passed** across desktop/mobile, including outage/restart, late-response disconnect and credential clearing |
| Existing browser journeys | Manual workbench desktop/mobile and read-only agent-viewer desktop/mobile passed |
| Type-check/build | Passed |
| Frozen R0 demo | Complete manifest, semantic content and canonical LF bytes match; native Windows newline difference remains reported |
| Independent review | Separate review of recovery authority and rolling-budget migration found no actionable permission regression |

Evidence is under `artifacts/operational-fixes/`: `verification/`, `installed-verification/`, `base-verification/`, `business-flows.json`, `agent-journey/journey.json`, `runtime/runtime-recovery-http.json`, `resilience/run-9f8a96493833475d87b2e777453c823f/results.json`, `viewer/results.json`, `viewer/REPORT.md`, `demo/` and `live-viewer.png`. The fresh wheel SHA-256 is `b2d553e74cf27a83463ac1ad9419645397592ee712688ec27f35e28326915930`.

Committed regressions: `tests/test_runtime_contracts.py`, `tests/test_runtime_operations.py`, `tests/test_runtime_recovery.py`, updated runtime tests, and `tools/verify_viewer_operations.mjs` with its isolated fixture. The viewer matrix is added to the configured Linux/Windows runtime CI job. Hosted CI was not run.

## Remaining gates

No open finding remains from this nine-item audit within the tested scope. Real-owner readiness remains **PARTIAL**: passkeys/authentication, key wrapping/rotation/recovery, deletion/retention, valid-old-backup rollback protection and production time semantics still need R1 work. Model/scheduler behavior, real boards, peer transport and human usefulness remain unimplemented or unverified.

Not tested: hardware power loss, disk full, memory/swap/crash dumps, OS key-file protection, public deployment/TLS, production load, other operating systems/browsers, physical devices or screen readers. Process interruption and injected-clock checks do not establish those properties. Nothing was pushed, merged or deployed.
