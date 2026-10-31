# Agent runtime flow audit

Date: 26 October 2026. Target: `app/`, local branch `codex/workbench-lifecycle`. Scope: focused implementation and verification of agent-only operation, standing authority, encrypted persistence/restart and read-only human viewing. Platform: Windows, Python 3.14.3, cryptography 49.0.0, Playwright 1.64.1/Chromium. All scenarios and identities are fictional.

## Goal and baseline

The owner explicitly required agents to operate the system and people to view. The previous 200-test milestone provided a manual browser-driven workbench with a freely selectable owner, a shared page token, in-memory state, no scoped agent interface and no restart persistence. These were documented prototype limits, not claimed authentication. Inspection of `http_server.py`, `memory_store.py` and `docs/STATUS.md` established the missing paths before implementation.

The implemented slice adds a separate optional runtime; it preserves every frozen R0 source/fixture byte. Real private input remains outside the release boundary. The old manual demonstration remains available independently.

## Executed journeys and deepest observed outcome

| Journey | Evidence and result |
| --- | --- |
| Provision and operate as one owner | Random owner-bound credentials; substitution and administrator route attempts denied; viewer mutation denied; private write-only token returns no workspace. Runtime and transport tests pass. |
| Publish through exact authority | Preview alone refused; reviewed exact command dry-run then execution succeeds. Changed arguments/version fail. Private canary excluded from publication. |
| Operate without repeated human/admin actions | Two initial standing mission grants; agents search a fixed approved candidate subset, propose, both accept, propose and agree a permitted first step with no further admin calls for these operations. Real HTTP journey and independent reviewer probes pass. |
| Constrain an autonomous agent | Unapproved query, target, step, free text, peer-added text, forged digest, audience/membership changes, private source edit, withdrawal, expiry, revocation and exhausted budget fail. Unrelated changes do not invalidate a valid mission. |
| Separate consent from outcomes | Both agents accept current context; both agree the step; reports require separate exact authority. Closing retains the reports, releases capacity and does not assert usefulness. |
| Persist the actual effect | Stop process, start a fresh server, retrieve the saved private brief/closed outcomes/capacity. Current encrypted database restores these states. |
| Recover a lost response | Retry same key/arguments/original version after restart returns original receipt, null response content, no duplicate effect. Revoked credentials cannot replay even after another restart. |
| Atomic refusal and concurrency | Independent runtime instances contend for last use; at most one effect. Injected commit failure rolls back workflow/budget/retry state. This is not a power-loss test. |
| Protect persisted strings | Ciphertext database has neither synthetic private-note canary, raw tokens nor supplied key. Wrong-key, tampered ciphertext/epoch and unsupported serialization fail closed. No memory/swap/crash-dump confidentiality claim. |
| Human viewing | Actual desktop/mobile Chromium pages read real server state, send no mutation/external request, retain no token in URL/storage/DOM input, clear data on disconnect and history restoration, and render markup inertly. Screenshots visually inspected. |

## Regression and packaging evidence

- Full source suite: **240 tests passed**, 0 failures/errors/skips. New tests comprise 32 runtime/mission and 8 transport/client/CLI cases.
- Fresh installed wheel outside source root: **240 passed**, 0 failures/errors/skips; installed package path verified. Wheel SHA-256 `0f3da801d6c66a06cd0164cdbe2548106872e744a423267fc43150b4e373bf9a`.
- Python with site packages disabled: **200 passed**, **40 optional-runtime tests skipped**. The frozen R0/default installation does not acquire a cryptography dependency.
- R0 regenerated demo: complete original manifest and semantic/canonical LF bytes equal; raw Windows newline difference remains explicitly reported.
- `python tools/verify_agent_runtime.py --browser`: actual two-agent, standing mission, restart, idempotency, revocation, ciphertext scan and both viewer viewports pass.
- Generated records: `artifacts/agent-runtime/{verification,installed-verification,base-only,reproducibility,journey.json,screenshots}`. Independent diagnostic probes are under `artifacts/agent-contract-probe`, `artifacts/agent-mission-probe` and `artifacts/agent-mission-drift-probe`.

Issues caught and repaired during implementation included SQLite connection cleanup, a browser-history restored private DOM, a missing capability metadata import, strict boolean brief-revision handling, and misleading exact-only permission metadata after adding standing missions. The affected checks and final full suite were rerun successfully. An optional handoff copying credentials to another directory was rejected by automatic approval review and omitted; it is not a runtime feature.

## Limits and remaining work

R1 remains **partial**. Passkeys, wrapping/data-key separation, unlock/rotation/recovery, safe backup restore, deletion/retention and real platform crash/private-input tests are not complete. A valid older ciphertext can restore older permissions. Domain event time remains fictional while credentials/missions expire against real UTC. There is no real board, peer transport, local model, scheduler, broad adaptive mission policy, human pilot or usefulness finding. Current notices are a fixed fictional seed population.

No hosted CI run, remote push, deployment, Linux isolation rerun, macOS/other browser/device/screen-reader verification or independent cryptographic audit is claimed. The local HTTP server is not an internet service. The viewer uses a separately scoped credential and protects against its tested web boundaries, not hostile software running under the same OS account.
