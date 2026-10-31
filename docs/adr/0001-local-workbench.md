# ADR 0001: Local collaboration workbench tooling

- Status: accepted
- Date: 2026-10-26

## Context

The R0 core is a Python standard-library package with no runtime dependencies, and that must stay true. The next fictional milestone needs a runnable browser journey across two owner perspectives, with typed contracts between the interface and the core, and a search component that can later be replaced.

## Decision

1. **Separate Python package.** The workbench is `agentmatch_workbench`, next to `agentmatch` in `src/`. It imports R0 but is not part of it, so R0's package fingerprint, recorded demo and frozen specification are unchanged. It also uses only the standard library.
2. **Thin local adapter.** `http.server` on loopback, with a transport-independent `WorkbenchApp.handle` that tests call without sockets. No web framework: the API is a handful of JSON routes, and a framework would add a runtime dependency to protect less code than it brings.
3. **TypeScript compiled by `tsc` alone.** `workbench/ui` has one dev dependency, `typescript`, pinned to an exact version. The compiler emits ES modules that the browser loads directly. No framework, bundler or CSS toolchain: the interface is a few components rendered with a small `h()` helper that only ever sets text content, which keeps the untrusted-text rule easy to audit.
4. **Ports for storage, search and time.** `WorkbenchStore`, `SearchIndex` and `Clock` are protocols in `application/ports.py`. The in-memory implementations are adapters. The visibility gate never trusts the index.
5. **Browser test outside the package.** `e2e/journey.mjs` drives Playwright if it is available, but Playwright is not a declared dependency, so `npm ci` installs only the compiler.

## Consequences

- `pip install` of the project still has no runtime dependencies, and `npm ci` is needed only to build the interface.
- Re-rendering whole panels is simple but needs explicit focus restoration (`app.ts`), which the browser test checks.
- A future real board service can replace the lexical index and in-memory store without changing domain rules or views.
- If the interface grows well beyond the current five areas, a component framework can be reconsidered in a new ADR.
