# CLAUDE.md

This file provides guidance to coding agents working in this repository.

## What this is

AgentMatch: owner-controlled agents that find collaborators and make explainable, mutually accepted introductions. **R0** is the completed offline fictional matching, consent and disclosure reference (`src/agentmatch/`, Python 3.11+, **standard library only, no runtime dependencies**). The separate fictional collaboration workbench (`src/agentmatch_workbench/`, `workbench/ui/`) provides domain workflows. The optional `agentmatch_runtime` adds scoped machine access, encrypted persistence and a human viewer. These are integration prototypes and do not complete R1 or R2. Real discovery boards, local models, passkeys, key recovery and peer transport remain unbuilt. R0 refuses real-person input and has no path/URL/account loaders.

## Current agent-operation direction

The owner confirmed on 26 October 2026: agents solely operate the system; people view. The separate optional `src/agentmatch_runtime/` implements a fictional owner-bound agent API, encrypted SQLite snapshot, exact/mission permissions, receipts, Python client and CLI. `workbench/viewer/` is read-only. The original workbench is still a manual domain demonstration. This supersedes manual-primary product operation; it does not waive owner authority or R1 gates. Read `docs/AGENT_API.md` and `docs/adr/0003-agent-operation-and-viewing.md`.

Do not put an admin vault key into an agent client or add admin HTTP routes. Keep owner selection in authenticated credentials, permissions deterministic, and current-resource checks at use. Never serialize with pickle or cache old peer payloads for retry. Optional runtime tests may skip without the runtime extra; report that honestly and run the runtime CI job with the extra. Frozen R0 bytes remain unchanged.

## Commands

```bash
python3 run_agentmatch.py fixtures verify                 # validate the bundled fictional corpus (CI runs this)
python3 run_agentmatch.py verify --offline                # full suite + JSON/JUnit evidence in artifacts/
PYTHONPATH=src python3 -m unittest discover -s tests      # plain unittest during development
PYTHONPATH=src python3 -m unittest tests.test_matching    # one module
PYTHONPATH=src python3 -m unittest tests.test_matching.MatchingTests.test_T_MATCH_002_hard_failure_overrides_score
python3 tools/isolated_exec.py -- python3 run_agentmatch.py verify --offline   # Linux, needs libseccomp; denies sockets
python3 tools/mutation_checks.py --out artifacts/mutations.json  # confirms two deliberate regressions are caught
python3 tools/verify_demo.py                              # original manifest + canonical LF comparison; raw newline difference reported
python3 run_agentmatch.py demo --out artifacts/r0/demo    # scripted journey -> demo.json/demo.html/JSONL
python3 run_agentmatch.py evaluate --owner lumen --intent lumen-collaboration --json
python3 run_agentmatch.py simulate --list
(cd workbench/ui && npm ci && npm run build)              # compile the workbench UI (tsc only)
python3 run_workbench.py                                  # workbench on http://127.0.0.1:8765/ (loopback only)
python3 -m pip install -e ".[runtime]"                     # separate optional cryptography dependency
python3 run_agent_runtime.py --help                       # trusted local provisioning and agent client commands
python3 tools/verify_agent_runtime.py --browser            # actual two-agent HTTP + restart + read-only viewer
node tools/verify_viewer_operations.mjs                    # current activity, exhaustion, mobile and failure regressions
(cd workbench/ui && npm run typecheck)
(cd workbench/ui && npx playwright install chromium)      # once for browser checks
(cd workbench/ui && npm run e2e)                          # isolated desktop and mobile journeys
python3 -m http.server --directory site 8080              # Connect the Bots site preview (static, fictional)
node site/tools/check_site.mjs                            # site desktop/mobile browser checks
node site/tools/record_explainer.mjs                      # re-export site/assets/explainer.mp4 from film.js (ffmpeg)
site/deploy/deploy.sh user@host --dry-run                 # VPS deploy; never run for real without the owner's approval
DEPLOY.cmd                                                # the owner runs it on their PC: pull main, build, backup, deploy, health check
```

No linter or formatter is configured. CI (`.github/workflows/verify.yml`) includes the Python checks and browser journeys; see the workflow for the platform matrix. `artifacts/` is gitignored; `examples/r0/` holds the immutable recorded evidence. A regenerated demo must retain the complete original manifest and match the recorded JSON after only CRLF-to-LF normalization. `tools/verify_demo.py` reports raw equality separately and writes a canonical copy; do not rewrite the core or goldens to conceal a difference.

`tests/` is packaged into the wheel as `agentmatch_selftest` (see `pyproject.toml`), so `agentmatch verify --offline` works from an install. Add new test modules there; don't duplicate them.

## Public site

`site/` is the Connect the Bots (`connectthebots.net`) public site: static HTML/CSS/JS, no build step, no remote requests, self-hosted fonts, fictional data only. Design rules (paper, ink, one cobalt accent; a connection is a line with its reason on it) are in `docs/DESIGN_SYSTEM.md`; scope and integration gaps in `docs/CONNECT_THE_BOTS.md`. The home page (`index.html`) is only the full-screen 66-second music-cut trailer plus one Sign in with Google button; the longer how-it-works page with the prompt is `about.html` and opens on the same trailer (`assets/trailer.mp4`, phones get `assets/trailer-480.mp4`, source `marketing/film-3d` with `?cut=music`; the CC BY 4.0 music credit must stay under the film and on its end card). `assets/film.js`, the earlier 15-second film, is kept for `film.html` only; regenerate its videos with the recorder rather than editing them. After editing `assets/connect-the-bots-prompt.md`, run `python3 site/tools/sync_prompt.py`, then `python3 site/tools/sync_agent_texts.py` (it also regenerates `prompts.html`, `agents.json` and `llms.txt` from `src/connectthebots_server/agent_texts.py`; never add agent instructions anywhere else). Agent growth (taste interview, referrals, boards, leaderboards) is described in `docs/HOSTED_AGENT_GROWTH.md`. Keep it honest: never show searches, schedules, notifications or conversations as live, an invitation is not acceptance, and a conversation opens only after both people say yes.

## Architecture

Data flows through layers that are kept strictly separate; most modules import only from the ones below them:

- `numeric.py`: exact `Fraction`/`Decimal` arithmetic and canonical JSON. Floats are never used for scores; JSON numbers are parsed as `Decimal`.
- `config.py`: loads `src/agentmatch/data/release_defaults.json`, the **runtime authority** for every numeric default (thresholds, coverage floor, selectivity presets, limits, default seed and `as_of`).
- `models.py`: immutable, strictly validated records (`Owner`, `EvidenceClaim`, intents, preference atoms, assessments). Unknown JSON keys are rejected. Schema is described in `CORE_INTERFACE.md`.
- `claims.py`: resolves which claim versions are usable now (current, approved, active, non-contradictory, matching purpose). Revisions create fresh unapproved versions; revocation invalidates dependent evaluations.
- `scoring.py` → `ranking.py` → `explain.py`: directional assessment (F fit, C coverage, S supported fit, U upper bound) over four atom families, hard-constraint veto, unknown-vs-known-negative handling, reciprocal qualification (both owners must pass their own threshold; pairs rank by the lower S), full-population scan with deterministic tie-breaks and capacity limits.
- `consent.py`: builds peer projections from exact audience/purpose/value/version grants. Private source IDs never enter peer records.
- `interview.py`: scripted gap-selection interview state machine (no model, no free-text extraction).
- `topics.py`, `channels.py`, `coach.py`, `simulator.py`: introduction contexts, bilateral channel consent, in-memory protocol/outbox simulation, default-off private draft help. No sockets, no encryption, no persistence, and no agent-send capability.
- `fixtures.py` + `data/synthetic/`: 40 fictional owners, 200 conversations, and hand-worked `expected_outcomes.json` oracle cases, all hash-checked against `manifest.json`.
- `demo.py`, `verification.py`, `audit.py`, `cli.py`: reporting, the shipped test runner, deterministic run manifests, and the CLI.

The workbench is a **separate package**, `src/agentmatch_workbench/`, because `audit.package_fingerprint()` hashes every file in `agentmatch` and that hash is inside the recorded `demo.json`. Don't put workbench code or data under `src/agentmatch/`.

- `domain/`: pure rules. `briefs.py` (structured brief, validation), `publication.py` (exact-payload board grant, separate from peer-disclosure grants), `visibility.py` (the single use-time gate: grant, expiry, audience snapshot, membership, blocks, brief changes), `assessment.py` (brief/notice → R0 owner; omitted sections stay unknown), `explanations.py` (wraps R0 `assess_pair_offline`/`render_pair_local`, words only), `introductions.py` (shared context, both acceptances via R0 `open_channel`, first-step states).
- `application/`: `service.py` (`WorkbenchService` use cases), `ports.py` (`Clock`, `SimulatedClock`, `WorkbenchStore`, `SearchIndex`), `views.py` (the only builders of browser-facing records).
- `adapters/`: in-memory store, lexical index (a hint only; never updated on expiry or blocks, on purpose), `seed.py` (`data/seed.json`), `http_server.py` (loopback bind, Host/Origin/token/JSON checks; `WorkbenchApp.handle` is socket-free for tests).
- `domain/information.py`: bounded named-field requests between approved notices; no free-text chat or private-draft answer path.
- `workbench/ui/`: pinned `typescript` and `playwright` development dependencies. `src/contracts.ts` mirrors `views.py`; `dom.ts`'s `h()` only sets text content. `dist/` is gitignored.

All clocks are explicit timezone-aware datetimes passed in; nothing reads the wall clock for decisions.

The optional runtime injects real UTC for credential/permission expiry and rolling clarification accounting. Remaining fictional domain dates use the simulated clock. Search retry recovery rechecks current authority and the original candidate references; never return cached peer content. See `docs/AGENT_API.md` for the three-recovery cap and 24-hour clarification window.

## Rules that matter here (from CONTRIBUTING.md)

- Never add real personal data anywhere (tests, fixtures, docs, PRs). Write new fictional cases.
- Permission/audience/field/version/expiry/budget checks are deterministic code with tests; a model or peer string is never an authority.
- Changes to scoring, thresholds, consent, disclosure or fixtures need a written rationale and tests showing old vs new behavior. **Do not update golden values by copying scorer output**; add an independent hand-worked oracle case.
- `tools/mutation_checks.py` patches exact source strings in `scoring.py` (`    if failures:`) and `consent.py` (the audience check). If you edit those lines, update the mutation anchors.
- Keep R0 free of runtime packages, cloud services and model downloads.
- `README.md` is the friendly public introduction for non-technical visitors; keep it short and link out. Technical overview and status history live in `docs/DEVELOPERS.md`. The public repository is `mrt150683-lgtm/connectthebots.net`.
- Every change that affects what the project does updates `README.md` (only if the visitor-facing story changes) or `docs/DEVELOPERS.md`, the relevant `docs/` file, and the "Unreleased" section of `CHANGELOG.md` in the same PR. PRs follow `.github/pull_request_template.md`.
- `docs/STATUS.md` is the one-page project status. Update it in the same PR whenever a milestone merges ("As of" line, built and verified tables, limitations, next steps), rerunning the checks rather than copying old numbers.
- The workbench's controls table in `docs/WORKBENCH.md` names the tests for each control; keep it in sync when adding or renaming tests.
- The original planning documents are kept privately and are not part of this public repository; current design lives in `docs/*.md`.
