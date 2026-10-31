# AgentMatch lifecycle and clarification flow audit

Date: 26 October 2026. Scope: focused verification of the fictional workbench milestone on `codex/workbench-lifecycle`, based on `7eea3fb3ea8f10480ee6485afbc4e4cd47c37535`. Audit and fixes were authorized by the owner's build request after the status review.

**Verdict: PASS for the implemented fictional flows.** Both capacity and normal-closure defects were repaired. The bounded information-request journey was observed from request through private editing, exact publication approval and reassessment on desktop and mobile Chromium. This is not an R1/R2 security or real-usefulness verdict.

## Environment and scope

- Windows 11, Python 3.14.3, pinned TypeScript 5.9.3 and Playwright 1.64.1.
- Disposable fictional seed state; no accounts, real histories, paid providers or models.
- Python domain/service/HTTP contracts; TypeScript interface; memory-only store and lexical index.
- Browser tests launch their own random-port loopback process and stop it afterward. Desktop is 1440×1000; mobile is a 390×844 Chromium viewport with touch emulation.
- Python temporary files use a writable directory under `artifacts/` because this execution environment restricts its default temporary directory.
- Original R0 source, configuration, fixture data, recorded outputs, provenance manifest and frozen plans were checked unchanged.

## Requirements and observations

| Goal and source | Actual path | Evidence | Verdict |
| --- | --- | --- | --- |
| Full capacity prevents new offers (roadmap section 9; documented) | Proposal → current visibility/fit → both-owner capacity checks → reservation in serialized service transaction | Distinct competing and concurrent proposal tests; first permitted pair still accepts | PASS |
| Continue or close (roadmap section 4; documented) | Explicit close → context-version check → end live channel authority → preserve reports → capacity released for both | Domain/service tests; desktop and mobile browser closure, subsequent discoverability | PASS for closure; a second step within the same closed connection is not provided |
| Missing information remains unknown until approved (status next step; documented) | Explanation → disclosure confirmation → exact source/target references → recipient inbox → private draft → exact approval → explicit fresh reassessment | End-to-end browser journey and information-request tests | PASS |
| Stale requests cannot disclose private/unapproved material (trust/architecture; documented) | Read/use reauthorization → publication scope/digest/expiry/membership/block checks → allowlisted view | Private canary, suspended-payload tamper, stale reference, withdrawal, block and revocation tests | PASS |
| UI responses stay with their owner perspective (inferred from demo identity boundary) | Busy request → perspective disabled → response and workspace refresh → perspective enabled | Delayed workspace-response browser assertion | PASS |
| Windows preserves integrity and reproducibility (review finding; user-authorized fix) | LF Git policy → fixture byte hashes; separate output comparator → exact manifest/content/canonical bytes | Checkout-filter checks, fixture verification, reproducibility helper | PASS for tested bytes; raw Windows output still differs in newlines and says so |
| Browser evidence is actual visible content (build prompt; documented) | Fresh complete journey per viewport → explicit response/panel/focus readiness → overflow checks and screenshots | Browser log, screenshots and geometry metadata; visual inspection | PASS for Chromium viewports |

## As-built flow and boundaries

`application/service.py` is the command boundary. Its reentrant transaction lock encloses public service operations, including refreshes that can expire state. `propose_introduction` rechecks the current target notice and both capacity budgets before storing a pending introduction. Pending/open records consume capacity for both people. This protects one process/service instance; it is not a persistent transaction across servers.

`domain/introductions.py:close_connection` accepts only a current context version and open channel, with idempotent retry after normal closure. It ends live membership/context authority and pending delivery state without fabricating reports or marking a person blocked. `application/views.py` preserves first-step history for ordinary closure. The interface labels the channel closed and no longer offers an impossible new proposal after a report.

`domain/information.py` defines the three typed fields and bounded records. `request_information` binds the requester's approved notice and the target notice, requires mutual permitted visibility and creates no free-text message or introduction. The source reference is part of the command so a stale preview cannot silently disclose a revised requester context. Local sent/received pending limits and session totals bound repeated requests; exact retries are idempotent.

The recipient may edit privately while the request is pending. The special handling for this edit suspension rechecks the full approved-payload digest and scope while ignoring only the draft-version mismatch. Publication approval remains a separate existing command. A known public field on a newer permitted notice changes the request to answered; reassessment is still an explicit requester action. An answer can reveal a mismatch. It does not automatically qualify or connect a pair.

The browser receives allowlisted request views. When authority is lost, counterpart identity, field and notice references are suppressed. Values already delivered cannot be recalled. The owner switch remains fictional and is not authentication.

## Findings fixed and regression evidence

| ID | Pre-fix result and impact | Repair and proof |
| --- | --- | --- |
| AM-CAP-01 / P1 | Two distinct proposals could fill a capacity-one recipient to 2/1 and prevent either acceptance | Both-owner checks and serialized reservation; `test_distinct_proposers_cannot_overbook_recipient_and_first_can_accept`, `test_concurrent_distinct_proposals_reserve_one_recipient_slot` |
| AM-CLOSE-02 / P2 | Both reported steps still occupied capacity; blocking was the only close action | Explicit normal close; `test_normal_close_preserves_reports_releases_capacity_and_is_not_blocking`, `test_normal_close_does_not_invent_an_action_or_require_both_to_leave` |
| AM-INFO-03 / P2 | Needs-information cards had no clarification path | Request/edit/approval/reassessment journey; 15 request tests in `test_workbench_lifecycle.py` |
| AM-WIN-04 / P2 | Windows CRLF checkout broke byte-hashed fixtures | `.gitattributes` pins LF; original fixture hashes retained; canonical demo comparison leaves raw-newline differences visible |
| AM-UI-05 / P2 | Perspective could be switched while a workspace refresh was still pending | Busy state remains until the response completes; delayed-response browser check |
| AM-VIS-06 / P3 | An early mobile screenshot was blank because a repeated text assertion passed before the explanation response and deferred focus | Synchronous result focus and scoped panel readiness; final image visually inspected and nonblank, with geometry metadata |

## Executed checks

- Baseline: 179 Python tests passed before edits.
- Integrated final suite: **200 passed, zero failures/errors/skips**, including 21 new lifecycle/request regressions.
- Fresh Windows checkout of implementation commit `3757194` with `core.autocrlf=true`: original fixture/source bytes remained LF, all 200 tests passed again, and cached clean dependency installation and interface compilation passed. The canonical demo and original manifest also matched from that checkout.
- Corpus validation: 40 fictional owners, 200 conversations, original integrity/source checks passed.
- Both selected deliberate core policy mutations were detected.
- Three comparison-helper tests passed. Generated demo: exact semantic and full-manifest equality, exact canonical LF equality; raw Windows byte equality false solely because of native output newlines.
- TypeScript type-check and build passed.
- Fresh independent desktop and mobile browser journeys passed. Each covers validation, both entry routes, publication, unknown and no-match states, instruction-shaped text rendering, clarification, revised approval, reassessment, bilateral consent, acceptance invalidation, separate reports, closure and released capacity. Console/network/overflow checks and keyboard tab navigation also passed.
- Final screenshots of requests, the mobile explanation, and normal closure were visually inspected. Earlier failed-attempt images are outside the final evidence directory.

Evidence: `artifacts/build-final/verification/`, `fixtures.json`, `mutations.json`, `reproducibility/verification.json`, `clean-check/`, `browser.log`, and `screenshots/`. Selected screenshots are retained under `examples/workbench/lifecycle/`.

## Limits and next steps

No failing case remains within these tested flows. This focused audit is not exhaustive proof of all possible state combinations or malicious inputs. The tests do not establish encrypted persistence, passkeys, recovery, live network security, human usefulness or production abuse resistance.

The newly configured hosted CI jobs, Linux socket denial for the expanded suite, an installed-wheel run, macOS, other browsers, physical mobile devices and screen readers were not executed in this local verification. Run the configured jobs after publishing the branch; declare platform support only from observed evidence.

R1 secure manual owner storage/authentication/permissions/recovery remains the next release gate. Explicit brief lifecycle states, multiple missions, usefulness feedback and pilot opportunity denominators remain separately tracked product work. Nothing in this milestone authorizes real-user recruitment or private-data ingestion.
