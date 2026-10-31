# AgentMatch 0.1.0: R0 release report

**Executed 26 October 2026. Scope: offline synthetic reference alpha.**

The first build runs from source and from an installed wheel. Its matching, claim review, exact disclosure and consent/delivery simulations are executable. There is no live service, real-history processing, model invocation, encrypted vault, passkey ceremony or peer transport in this release.

## Verified results

| Check | Observed result |
| --- | --- |
| Packaged test suite | **129 tests passed, 0 failures, 0 errors, 0 skips.** |
| Independent fixture oracles | 22 hand-authored numerical/permission cases executed and passed through the suite. |
| Fictional corpus | 40 owners; 200 conversations; 800 messages; five conversations per owner. |
| Scripted scenarios | 21 social/protocol scenarios plus 7 interview scenarios. |
| Full candidate scan | Lumen's evaluation scans all 39 other fictional owners before ranking/offer limits. |
| Clean install | Local wheel installed into a fresh venv, then executed from an unrelated temporary directory. |
| Source/installed reproducibility | **Demo JSON is byte-for-byte identical** at the fixed seed and clock. |
| Runtime dependencies | None beyond Python's standard library. Build-only tools are pinned separately. |
| Network denial | Every isolated command passed IPv4, IPv6 and Unix socket positive controls returning EPERM under libseccomp. |
| Inherited socket controls | Nonstdio inherited descriptors closed; socket standard streams rejected; dedicated bypass checks passed. |
| Deliberate regressions | Removing the hard veto or disclosure audience check caused the corresponding independent test to fail. Both mutations detected. |
| Static report checks | Escaped input and no active/external assets covered by tests. Browser visual rendering was not inspected because no executable browser was installed; no download was performed. |

The source and installed suites both passed under the external isolation harness. The original 98 acceptance cases span multiple releases and are **not** all marked passed. See [ACCEPTANCE_STATUS.md](ACCEPTANCE_STATUS.md) for the specific boundary of each evidence group.

## What the worked example proves

| Direction | Preference F | Supported S | Coverage C |
| --- | --- | --- | --- |
| Lumen → Harbor | 17/20 = 0.85 | 41/50 = 0.82 | 1 |
| Harbor → Lumen | 4/5 = 0.80 | 77/100 = 0.77 | 1 |

Both qualify at default 0.60. Both at close-fit 0.85 fail reciprocal qualification, because Harbor's direction falls short. Independently selected 0.85/0.80 pass. The reference ranking value 0.77 is not a probability of success.

The integrated interview starts with Harbor's feedback inference unreviewed. Lumen's coverage is then 0.9, preference fit 0.75 and supported fit 0.75. A summary acknowledgment changes nothing. Exact local approval with retained fictional evidence creates one baseline-bound immutable revision, restoring coverage 1, preference 0.85 and supported 0.82. The inference stays an inference and grants no disclosure.

The introduction first refuses to open with only one acceptance. After two current acceptances it opens with a labelled context and zero human messages. A scripted human send is queued locally, delivered with its acknowledgment lost, and retried under the same ID without duplicate display. A draft coach prompt remains private. A separate two-endpoint contact scenario shows why application receipt, sender acknowledgment and reciprocal contact delivery differ.

## Environment and measurements

- Python: 3.12.14.
- Platform: Linux-6.18.55-x86_64-with-glibc2.39.
- Installed-suite execution time reported by the suite: 2.2984 seconds.
- Maximum child-process peak RSS across the release runner: 64804 KiB. This includes build/install/test processes; it is not a local-model memory measurement.
- Seed: 1729. Frozen scenario clock: 2026-10-25T12:00:00Z.
- Design baseline: 3.1.0. Numerical contract: 0.4.0. Owner-data schema: 0.1.

These are observations from this Linux execution environment. They do not establish performance on the owner's Mac Studio, the 5500U/32 GB target, another Python version, Windows or macOS. No 7B/13B model benchmark was performed.

## Reproduction

From the source checkout:

```bash
python3 run_agentmatch.py fixtures verify
python3 run_agentmatch.py demo --out artifacts/r0/demo
python3 run_agentmatch.py verify --offline --out artifacts/r0/verification
```

To repeat the actual Linux installation/isolation/mutation checks with the pinned build tools already present:

```bash
python3 tools/release_checks.py --out artifacts/r0/release
```

The runner builds and installs a local wheel without an index or runtime dependency downloads, tests the installed entry point away from source, compares deterministic reports and exercises the external harness. The harness communicates through ordinary captured standard streams; it is not a general sandbox for a malicious parent process or private inference worker.

## Evidence files

The source archive includes these recorded outputs under `examples/r0/`:

- `demo/demo.html`, `demo/demo.json`, `demo/run_manifest.json` and `demo/fixture_report.json`.
- `demo/decisions.jsonl`, `demo/protocol_traces.jsonl`, `demo/social_traces.jsonl`, `demo/interview_traces.jsonl`.
- `verification/verification.json`, `verification/test_results.xml` and `verification/test_output.txt`.
- `offline_run.json`, `mutation_results.json` and `oracle_results.json`.

The installable wheel is `dist/agentmatch-0.1.0-py3-none-any.whl`.

## Fingerprints

| Artifact | SHA-256 |
| --- | --- |
| Runtime source and packaged data | `76d1f34f7b7c3e5348c25dd2bb99a0728120e1f6b71c5a3bcf686c5225925491` |
| Test source suite | `7883fa142481a53bbb7d06e21ef1692989178071b9c69e0b4c286e3075ac041c` |
| Deterministic demo JSON | `5c42649749e11268b6425e1a4e3c99e2fe4834c9ff6bf056f8cc0e67158dcb65` |
| Built wheel | `e17b07a08a93061c03a44946d4d789bf391b0e7ee11880ac727cf537623a14a8` |
| Fictional corpus | `b6d93a17b9b9a534188ebe3be9625cf387f19acf7244909941965e1d5663d0e6` |

The wheel hash identifies this exact build; no bit-identical wheel-build promise is made across timestamps/toolchains. Source and installed **application output** was compared exactly.

## Remaining gates

R1 still needs real authenticated encrypted storage, source-selection/import controls, recovery, deletion/canary tests, passkey verification and actual confined local inference. R2 still needs authenticated encrypted signaling, DTLS identity binding, two live peers, encrypted persistent outboxes and crash/restart recovery. Synthetic receiver ledgers do not establish durable deduplication or an exactly-once network guarantee.

Human usefulness, meaningful cold-start density, moderation workload, UK data-protection readiness and IP/brand freedom to operate remain unestablished. The project has no new hardware purchase, VPS/domain deployment, accepted budget cap or committed next deadline. The Mac Studio workload audit and budget workshop remain open.

**Release decision:** make this R0 reference build available for inspection and further local development. Do not promote it to a private-data processor or live social service on the strength of these results.
