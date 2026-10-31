# AgentMatch operational QA

**Follow-up:** the owner subsequently authorized repairs. All nine findings were fixed and retested in the [operational repair report](operational-fixes-2026-10-26.md). This document preserves the original baseline observations and verdict.

## Overall verdict

**PARTIAL: the bounded fictional collaboration workflow works; continuous unattended operation and the human viewer need repairs.** All 20 canonical agent operations were exercised successfully through actual HTTP. The existing 240 tests pass. Live failure tests preserved the tested authorization, budget and transaction invariants. Operational probes nevertheless reproduced six P2 usability/contract gaps and three P3 reporting/contract gaps below. Passing the baseline did not reveal these conditions.

This is a full audit of the **implemented local slice**, not certification of a real-owner service. The clients in the tests are deterministic programs. No model reasoning, autonomous scheduler, real board, actual peer messages or human collaboration outcomes were tested or invented.

## Scope, environment and isolation

- Audited revision: `98dc3b0`, branch `codex/workbench-lifecycle`, 26 October 2026.
- Windows, Python 3.14.3, cryptography 49.0.0; desktop/mobile Chromium and the Codex in-app browser.
- Fresh fictional databases, independent random keys and scoped credentials. Destructive interruption tests used owned disposable processes. No real-person data or external service.
- Product source and tests were unchanged. Diagnostic scripts and evidence are under `artifacts/operational-qa/`; this report and its JSON are the audit deliverables.
- A separate live preview was opened and connected in the in-app browser. Two fictional agents have mutually accepted and agreed an asynchronous review; neither has reported that it happened. Its key is held only in memory and its read-only viewer credential expires after two hours. Reloading disconnects the viewer. This temporary preview is not a durable installation or restart demonstration.
- Prior build evidence and this audit are separate. This turn reran the full source baseline, then independent operational probes. It did not rerun the installed-wheel checks from the preceding build.

## Goals and evidence sources

| Goal | Source/classification | Evidence needed |
| --- | --- | --- |
| Agents operate; humans view | the owner's explicit instruction, USER-STATED | Agents execute complete commands; viewer cannot mutate; machine-facing instructions are usable |
| Run and fully assess operational behaviour | Current request, USER-STATED | Real process/HTTP/database/browser observations, including failures and sustained sessions |
| Owner-bound scopes, exact/standing authority, finite budgets and revocation | `docs/AGENT_API.md`, DOCUMENTED | Positive and negative authorization through actual requests and durable state |
| Encrypted restart, atomic effects and safe retries | `docs/AGENT_API.md`, DOCUMENTED | Fresh process reads committed state, interruption does not duplicate effects or spend extra budget |
| Useful ongoing activity viewing | Product direction and viewer claims, USER-STATED / INFERRED | Recent events visible, terminal errors actionable, displayed state corresponds to the backend |
| Sustained operation without unplanned administrator intervention | Agent operation direction, INFERRED | Retry and lifetime limits allow recovery within the declared authority, or expose explicit renewal steps |
| Real-owner R1 security | `docs/STATUS.md`, DOCUMENTED future gate | Not implemented; remains outside a passing verdict |

## Feature inventory and trace

| Flow | Entry point and implementation | Reads / writes / visible result | Evidence |
| --- | --- | --- | --- |
| Provision/revoke | Local `cli.py:24`, `runtime.py` administrative methods | Fresh credential/mission records; no administrator HTTP routes | Baseline + expiry/revocation probes |
| Discover contract and authority | `/openapi.json`, `/v1/operations`, `/v1/capabilities`; `catalogue.py:76`, `runtime.py:345` | Schemas/vocabularies, current owner/scopes/permissions/uses | Contract probes; QA-001/QA-008 |
| Draft and publish | `save_brief`, `preview_publication`, `approve_publication`; workbench `service.py:235` onward | Private revision, separate exact publication grant, current notice | All-command HTTP probe + baseline |
| Search and explain | `search`, `explain`; standing policy `policies.py:143` | Current approved candidate subset; result/explanation; no saved shortlist | Live mission + QA-004 |
| Clarify | `request_information`, `decline_information`, private edit/publication/reassessment; `service.py:646` onward | Requests and revised published evidence, no automatic match claim | Business flows + QA-001/QA-005 |
| Introduce and consent | Propose, accept, withdraw acceptance, change, decline, block; `service.py:463` onward | Current proposal digest, cleared stale acceptances, bilateral open state | Business flows + QA-001 |
| Agree/report/close | Step propose/agree/report, normal closure; `service.py:745` onward | Distinct reports preserved, including disagreement; capacity released | Business flows |
| Persist and retry | `runtime.py:372`, `vault.py:101` | One writer transaction covers auth/state/budget/receipt/retry; receipt-only replay | Eight live resilience cases |
| Inspect activity | `/v1/receipts`; `runtime.py:358`, viewer `viewer.js:94` onward | Owner-scoped paged metadata; browser displays first page only | Cursor traversal PASS; QA-002 |
| Read-only viewer | `workbench/viewer/`, same-origin GETs | Role check, in-memory credential, current workspace, disconnect/history cleanup | Browser matrix; QA-002/003/006/007 |
| Models, scheduler, remote boards/transport | No execution path in this slice | No inference, scheduled work or real messages | DOCUMENTED limitation, not a runtime PASS |

```mermaid
sequenceDiagram
    participant Admin as Trusted local provisioning
    participant Agent as Scoped agent client
    participant API as Local HTTP API
    participant Runtime as Authority and workflow
    participant DB as Encrypted SQLite state
    participant Viewer as Human viewer
    Admin->>DB: Issue bounded credentials and exact/mission authority
    Agent->>API: Command, arguments, expected version, retry key
    API->>Runtime: Validated request and bearer credential
    Runtime->>DB: Acquire writer transaction; load current state
    Runtime->>Runtime: Check owner/scope/grant/current resources/budget
    Runtime->>Runtime: Apply deterministic domain operation
    Runtime->>DB: Commit ciphertext, budget, receipt and retry metadata
    Runtime-->>Agent: Versioned result after commit
    Viewer->>API: Separate read-only capabilities/workspace/receipts requests
    API-->>Viewer: Current authorized views
```

The server does not cache old search/workspace response content for retry. This protects later-withdrawn data, but creates the query-recovery gap in QA-004. Viewer reads are separate requests and consume a workspace use; reading capabilities before that consumption explains QA-007.

## Executed evidence

| Check | Observed result | Verdict |
| --- | --- | --- |
| Existing source suite | 240 tests, 0 failures/errors/skips | PASS |
| Canonical command coverage | All 20 operations through actual HTTP, no unexercised operation | PASS |
| Clarification | Pending request → private edit stays pending → explicit publication → answered → fresh explanation; decline discloses nothing new | PASS |
| Changed consent | Acceptance withdrawal works; changed context clears earlier acceptance; both reaccept current context | PASS |
| Outcomes and closure | Different occurrence reports remain distinct; normal closure retains them and releases capacity | PASS |
| Decline/block/withdraw | Both reservations released; blocked/withdrawn notices no longer discoverable | PASS |
| Standing mission | Initial permissions permit search, proposal, bilateral acceptance and step agreement; no later admin action for those steps | PASS |
| Lost mutation reply + hard restart | Identical retry yields original receipt, null stale content, one effect and one use | PASS |
| Concurrent agents | Same-version race: one success, one conflict; deliberate current-version retry succeeds | PASS |
| Shared final use | Twelve requests: one success, eleven refusals; durable use count one | PASS |
| Expiry/revocation while queued | Current authority checked after writer lock; inactive credential cannot act | PASS |
| Interrupted uncommitted write | Kill after encrypted UPDATE and before COMMIT; restart restores prior state; retry commits once | PASS, process interruption only |
| Held database lock | No state/use change; retry after unlock succeeds once; generic error classification | PARTIAL, QA-009 |
| Malformed wire requests | Duplicate auth/framing, short body, chunked encoding, oversized body and foreign Host refused | PASS |
| Receipt API pagination | 123 receipts across 18 pages, ordered and unique | PASS |
| Human viewer operational matrix | 17 positive checks passed across desktop/mobile; four unique viewer findings reproduced | PARTIAL |

Evidence files: `baseline/verification.json`, `business-flows.json`, `contracts/results.json`, `resilience/run-bc1d0f2740ca4c7aaedec5dbd641bfee/results.json`, `viewer/` results/screenshots, and `live-agreement.png` beneath `artifacts/operational-qa/`. Reproduction scripts are `business_flows.py`, `contracts/probe_contracts.py`, `resilience/probe.py` and `viewer/run_viewer_qa.mjs`. They create fresh fictional test state. Viewer harness diagnostics are identified separately from product failures in its report.

## Reproduced findings

### QA-001 · P2 · Advertised actions are not executable agent commands

Expected: an agent can use the action names returned with a resource or an explicit machine-readable mapping. Actual: a pending introduction advertises `block`; an open one advertises `close`. These return `404 UNKNOWN_OPERATION`; `block_introduction` and `close_introduction` are the executable names. Answered information requests advertise `reassess`, which also returns 404; the canonical operation is `explain`. Incoming request labels `review_brief`/`decline` are likewise inherited UI actions rather than sufficient command contracts.

Reproduce: publish both fictional notices, create/accept an introduction, read `workspace.introductions[].actions`, send the advertised close action, then send the canonical close. The first fails, the second closes. For clarification, answer by approved publication and send advertised `reassess`; canonical `explain` succeeds and can still find a mismatch. Evidence: `contracts/results.json`.

Cause: workbench `service.py:463` and `service.py:676` produce UI vocabulary; runtime `runtime.py:372` accepts catalogue operation names unchanged. Next: return executable operation descriptors, argument requirements and current references, preserving the old manual UI contract separately.

### QA-002 · P2 · New operational receipts disappear from the viewer after the first 50

Expected: a person can inspect current agent activity/refusals. Actual: the browser always asks for `after=0&limit=50`, discards `next_cursor` and shows the oldest page. A later `SCOPE_REQUIRED` refusal was present through the paged API but absent from the viewer after refresh.

Reproduce: produce more than 50 owner receipts, provoke a scope refusal, refresh and compare to later API pages. Cause: `workbench/viewer/viewer.js:106`–116. The first-50 label is honest but does not provide useful ongoing oversight. Next: latest activity plus explicit cursor navigation; surface retention truncation.

### QA-003 · P2 · An exhausted viewer credential gets an impossible refresh instruction

Expected: terminal permission exhaustion is distinguishable from a temporary outage. Actual: HTTP 429 `BUDGET_EXHAUSTED` becomes “Try refreshing.” A one-use credential connects once, then every refresh repeats the same failure. The browser retains the last successful view with a warning; the backend correctly refuses.

Reproduce: issue a viewer credential with one use, connect, refresh twice. Cause: the catch-all response branch at `viewer.js:60` ignores stable error codes. Next: identify exhausted permission, stop futile retries and explain the credential renewal path without widening authority automatically.

### QA-004 · P2 · A lost one-use search result requires fresh administrator authority

Expected/inferred: an agent can recover a transient lost read response within its declared authority. Actual: the original query uses its exact grant; retry returns `replayed: true, result: null`; workspace has no saved shortlist; a new query returns `403 APPROVAL_REQUIRED`. The tested query initially returned six candidates. Safe effect deduplication works, but result recovery is incomplete.

Reproduce: execute an exactly authorized search, discard the reply, repeat its key/arguments/version, then try a new key. Evidence: `contracts/results.json`. Cause: `runtime.py:397` receipt-only replay and consumed exact grants. Next: a current-authorization result recovery or bounded repeat-query permission design. **Do not fix this by returning cached withdrawn peer content.** Document the remaining interruption/expiry limits.

### QA-005 · P2 · The clarification “session” budget becomes permanent with persistence

Expected/inferred: a sustainable budget has an explicit lifetime/renewal model. Actual: after 20 completed/declined requests and zero pending requests, the 21st is refused. Reopening the database and issuing a new credential does not help. There is no documented replenishment/reset operation.

Reproduce: request a missing field, decline, republish the target to a new revision, repeat 20 times; attempt again before and after reopening. Evidence: `contracts/results.json`. Cause: `domain/information.py:23` and `service.py:663` count all historical sent/received requests, inherited from the in-memory session. Next: decide and implement bounded time-window/lifecycle accounting, without erasing audit/consent history or permitting unbounded contact.

### QA-006 · P2 · A valid unbroken title breaks the mobile layout

Expected: all accepted brief text stays readable within the mobile viewport. Actual: an accepted 80-character title, including a 40-character unbroken segment, creates a **709px page width inside a 390px viewport**, overflowing the Current brief card and cutting text at the right edge. HTML-like text remains inert; this is not script execution.

Reproduce: save the fictional title fixture in `viewer/FINDINGS.md`, open the mobile viewer and inspect horizontal overflow. Cause: `workbench/viewer/style.css:64`–65 current-brief title/lead wrapping rules. Evidence: `viewer/mobile-inert.png` and `viewer/results.json`. Next: wrap accepted unbroken text and add a browser regression for long accepted values.

### QA-007 · P3 · Remaining viewer uses is always one read behind

Expected: the access panel reports current remaining uses. Actual: capabilities are fetched before the charged workspace read; a one-use credential displays one remaining while the backend has zero. Reproduce by connecting and comparing the displayed value to a fresh capabilities read. Cause: `viewer.js:102` before `viewer.js:106`, display at `viewer.js:260`. Next: align the read snapshot/budget receipt or refresh authority metadata after the charged operation.

### QA-008 · P3 · OpenAPI describes inputs but not response schemas

Expected/inferred: a generic agent client can learn/validate both request and result shapes. Actual: all five API paths lack response JSON schemas; success/error entries are description-only. This is a contract completeness gap, not a failed business effect. Evidence: fetched OpenAPI in `contracts/results.json`; cause: `catalogue.py:103` onward. Next: schema the result envelopes, resource/action descriptors, pagination and stable error details, and check response conformance.

### QA-009 · P3 · Temporary database contention becomes a generic internal error

Expected/inferred: transient storage contention has an actionable retry classification. Actual: holding a SQLite writer lock produced `500 INTERNAL_ERROR` after 11.084 seconds. Durable state and uses were unchanged; the same key succeeded once after unlock.

Cause: `sqlite3.OperationalError` from `vault.py:106` is not handled by the storage exception branch at `http_server.py:103` and falls into the generic handler at line 143. Next: stable temporary-storage error and retry guidance, backed by a real locked-store test. Evidence: resilience report and results.

## What the existing tests prove and miss

The 240 tests establish the tested deterministic matching/consent, exact and mission authority, serialization, scope, retry and transport conditions. They do not prove that returned resource actions map to the command catalogue, the viewer follows receipt cursors, exhausted credentials receive actionable messages, sustained clarification budgets can recover, or accepted mobile text always fits. The additional real-process probes increase confidence in interrupted-transaction integrity; they are not hardware power-loss or secure-key-management evidence.

## Remaining unknowns and next actions

1. Repair the agent action contract, viewer current-activity navigation and terminal-budget messaging; rerun the specific failed flows and add regressions.
2. Establish sustainable clarification budgets and permission-preserving search-result recovery. These are policy/lifecycle decisions as well as implementation work.
3. Repair mobile wrapping, align remaining-use metadata, complete output schemas and classify storage contention.
4. Complete R1 before real inputs: passkeys, key wrapping/rotation/unlock, deletion/retention, backup recovery with revocation/rollback protection, real domain clocks and platform-specific crash/private-input testing.
5. Independently evaluate model integration/scheduling, real invited discovery, peer transport and human usefulness when those components exist.

Untested: hardware power loss, disk full, memory/swap/crash dumps, OS key-file protection, valid-old-backup rollback prevention, public deployment/TLS, production load, other operating systems/browsers, physical mobile devices and screen readers. The smallest next proof is a named-platform fault/restore/security plan after the operational defects above are repaired. Hosted CI was not run; nothing was pushed, merged or deployed.
