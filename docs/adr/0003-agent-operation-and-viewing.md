# ADR 0003: agents operate; humans view

Status: implemented as a bounded fictional local runtime; R1 remains incomplete.

The owner explicitly established on 26 October 2026 that this system is solely operated by agents, with people entering through viewing. A manual editor as the primary product would contradict that direction. The previous workbench remains useful as a demonstration and regression harness, but is not the operating product surface.

Add a separate `agentmatch_runtime` package around the existing deterministic workflow. Keep the R0 source and numerical contract unchanged and its installation free of runtime dependencies. Put the encryption library in an optional runtime extra. Expose stable JSON commands, schemas, errors, concurrency preconditions, receipts and explicit retry behavior, with a standard-library client. A human viewer uses only authenticated reads and no operator controls.

Credentials bind an agent to one owner, finite scopes, uses and real expiry. Separate viewer credentials cannot mutate collaboration state. Generic credentials do not authorize arbitrary publication/contact content: exact commands and bounded standing missions are distinct authority forms. Agents cannot issue their own authority. The trusted local administrator provisions permissions outside HTTP. Human approval of a policy is separate from clicking through every action; domain consent remains bilateral and current.

An encrypted SQLite snapshot is the bounded first storage adapter because the existing workflow is small and operates on in-memory domain objects. Restore allowlisted types into a disposable service per transaction, reauthorize, apply the command, then commit ciphertext, permissions, counters and receipts together. A failed write must not leave a successful in-memory effect. This avoids treating a process lock as durable concurrency protection. Do not adopt this full-snapshot pattern as a scale claim; replace it with explicit row-level storage when the production workload and migration requirements are established.

Keep only retry effect metadata, not an old response body, because replaying a cached peer payload after revocation would cross the current authorization boundary. Database restore is a different operation from process restart: a valid old ciphertext can contain old credentials. Safe backup recovery requires additional key/revocation/rollback design and remains unimplemented.

Alternatives rejected for this slice: reusing the workbench's page token as authentication; allowing agents to self-approve preview digests; unbounded generic publication scopes; browser owner switching; plaintext persistence; custom cryptography; claiming that loopback, encryption or a passkey proves unique humanity. Full passkey authentication, key wrapping/rotation/deletion/recovery, model confinement, remote boards and peer transport keep their existing release gates.
