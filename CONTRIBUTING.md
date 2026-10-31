# Contributing to AgentMatch

Thank you for helping. AgentMatch is meant to be a transparent, auditable tool for connecting people, so contributions are judged on correctness, clarity and respect for the people the system represents.

## Before you start

- Read the [README](README.md) and the relevant document in [docs/](docs/). The [architecture](docs/ARCHITECTURE.md) and trust and prompt contract describe the boundaries that changes must respect.
- For anything larger than a small fix, open an issue first so the approach can be agreed before you write code.

## Ground rules

1. **No real personal data, ever.** Do not include real conversations, profiles, contact details or chat exports in issues, tests, fixtures, screenshots or pull requests. Use fictional examples. If you need a realistic case, write a new fictional one.
2. **The model is not the security authority.** Permission, audience, field, version, expiry and budget checks must be deterministic code with tests. A prompt instruction is never the only control.
3. **Explain changes to matching or consent.** Changes to scoring, thresholds, matching policy, consent rules, disclosure rules or fixtures need a written rationale in the pull request and tests that show the old and new behavior. Numerical changes need an independent oracle case.
4. **Version prompts.** A change to an important prompt updates its version, schema, assumptions, limits and evaluation cases together.
5. **State what a control does not guarantee.** When adding a security control, document both what it prevents and what it cannot prevent.
6. **Keep docs current.** If your change affects what the project does, update the README and the relevant document in the same pull request, and add an entry under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).

## Working on the R0 code

Run the full suite before and after your change:

```bash
python3 run_agentmatch.py verify --offline
```

On Linux, also run it under the network-denial harness (needs `libseccomp`):

```bash
python3 tools/isolated_exec.py -- python3 run_agentmatch.py verify --offline
```

- `src/agentmatch/data/release_defaults.json` is the runtime authority for numerical defaults. Change a default only through a versioned decision and an independent expected-outcome review.
- Keep evidence provenance, approval, disclosure and delivery separate. A summary acknowledgment is not a permission. Never let a model or peer string act as an authority instruction.
- Do not update golden values by copying the scorer's output. Add a hand-worked oracle case instead.
- Keep R0 free of runtime packages, cloud services and model downloads. Introduce a dependency only in the gated release that needs it, with exact version and license review.
- Add new test modules under `tests/`. The package mapping ships them in the wheel as `agentmatch_selftest`, so there are no duplicate copies to maintain.
- Do not claim that tests of a state machine establish real encrypted transport, persistence, passkey identity or human benefit.

To build a wheel with the pinned build-only tools already installed:

```bash
python3 -m pip wheel --no-index --no-deps --no-build-isolation . --wheel-dir dist
```

## Pull requests

- Keep each pull request focused on one change.
- Describe what changed, why, and how you verified it.
- Make sure tests pass locally before requesting review.
- By submitting a contribution you agree that it is licensed under the [Apache License 2.0](LICENSE), as described in section 5 of that license.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).

## Conduct

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
