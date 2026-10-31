# Security policy

## Current release boundary

The code in this repository is R0, an offline synthetic reference. It has **no encrypted vault, model runtime, passkey verifier, network adapter, production discovery service or real-person import path**. All queue and authentication outcomes are simulations. Do not use it to process private histories or as a live matching or dating service.

The CLI accepts only bundled fixture identities and scenario IDs. Import, live interview, contact, connect and server commands return an explicit unsupported-capability error before opening a supplied input file. The lower-level Python modules are inspectable reference components, not a sandbox against someone who can run arbitrary Python on the same machine.

The core distinguishes local evidence from peer projections. Outbound objects are built from explicitly permitted fields, and immutable claim versions plus exact value, audience, purpose and expiry checks protect the simulated consent rules. An authenticated future peer could still lie about their own statements or about human approval; cryptography cannot establish their truth.

`tools/isolated_exec.py` denies socket operations in its child process tree on Linux. It does not change the host firewall and is not the future inference sandbox. R1 needs explicit checks for files, swap, crash dumps, logs, subprocesses, loopback proxies, model downloads and provider egress before any real data is admitted.

Security controls described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for later releases are design requirements, not implemented guarantees. The current threat model is in [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md).

## Reporting a vulnerability

Please report suspected vulnerabilities privately using GitHub's **"Report a vulnerability"** button on the repository's Security tab (private vulnerability reporting). Do not open a public issue, and do not include real personal data in your report.

Reproduce the problem with a fictional fixture and the smallest failing test where you can. Include what you found, how to reproduce it, and what you believe the impact is. We aim to acknowledge reports within seven days and will keep you informed while a fix is prepared. We are happy to credit reporters who want recognition.

## Scope

In scope: anything in this repository, including design flaws in the documented trust model, permission checks, disclosure rules and prompt contracts. Design-level issues are valuable at this stage.

Out of scope: third-party services and models, which are not bundled.
