# Agent operation and human viewing

The owner's confirmed direction on 26 October 2026 is that agents operate AgentMatch; people view their progress. The primary product surface is an authenticated agent API. The human viewer has no brief editor, publication, matching, contact or acceptance buttons. People retain authority through the permissions given to their agents. Neither model text nor another agent's message can create that authority.

The current implementation is a **local fictional integration runtime**. It adds durable encrypted state and owner-bound machine credentials to the workbench rules. It does not complete R1, enable real-history import, run a model or create a real discovery service. The original manual workbench remains a separate demonstration of the domain rules.

## Run the optional runtime

The R0 package still has no runtime dependencies. The separate optional runtime uses pinned `cryptography==50.0.2`. The hosted account milestone updates the shared pin after the dependency audit flagged an advisory in 49.0.0; R0 bytes are unaffected:

```sh
python -m pip install -e ".[runtime]"
python run_agent_runtime.py schema
```

A trusted local administrator supplies an independently stored, random 32-byte binary vault key. Do not give an operating agent this key: it grants administration of every fictional owner in this prototype. Use a protected file outside the database directory; the service never writes a key automatically, puts it in HTML or accepts it through the agent API. Key-file protection, wrapping, recovery and backup discipline are not provided by this milestone.

```sh
python run_agent_runtime.py init --fictional-demo --db local-data/demo.sqlite --key-file /protected/location/demo.key
python run_agent_runtime.py issue --db local-data/demo.sqlite --key-file /protected/location/demo.key --owner marlow --role agent --scope workspace --scope search --scope propose_introduction --scope accept
python run_agent_runtime.py issue --db local-data/demo.sqlite --key-file /protected/location/demo.key --owner marlow --role viewer
python run_agent_runtime.py serve --db local-data/demo.sqlite --key-file /protected/location/demo.key --port 8766
```

Create the database parent directory before initialization. Paths above are illustrative; use local Windows paths on Windows. Issuance returns the new bearer token once as JSON, intended for a private credential handoff, never a public log. Viewer and agent tokens are different roles. Default issuance expires in one hour with 100 command uses; configurable limits are enforced server-side. `revoke --credential <id>` stops subsequent authorization, including retries. Receipt and capability reads remain possible after command-budget exhaustion until expiry/revocation.

The viewer opens at `http://127.0.0.1:8766/`. Enter a **viewer** token. It stays in memory, is absent from URLs and browser persistent storage, and is cleared along with the displayed workspace on disconnect and page-history restoration. A source checkout supplies viewer assets automatically; an installed CLI can use `--viewer-dir` pointing to `workbench/viewer` in a separately verified checkout.

## Discover the contract

| Request | Purpose |
| --- | --- |
| `GET /openapi.json` | Public OpenAPI 3.1 input and response contracts; no owner data or credentials |
| `GET /v1/operations` | Authenticated command catalogue with full JSON input schemas and closed vocabularies |
| `GET /v1/capabilities` | Credential owner, role, scopes, remaining uses, real expiry, and current permissions |
| `GET /v1/workspace` | Current authorized view for that credential's owner |
| `GET /v1/receipts?after=0&limit=50` | Owner-scoped operation metadata in ascending order and next cursor, maximum page size 100 |
| `GET /v1/receipts?order=desc&limit=50` | Newest activity first; use `next_before` as `before` for an older page |
| `POST /v1/commands` | Execute a named operation with structured arguments |

All private requests use `Authorization: Bearer <token>`. There are no owner-selection parameters, wildcard scopes, administrator HTTP routes or CORS grants. Native agents can omit Origin; browser requests must be same-origin. Bind is fixed to IPv4 loopback. The Python HTTP adapter is a local development server, without TLS or internet-service hardening.

Responses use `{version, result}` and, for commands, `{receipt, replayed}`. Mutations and search require an idempotency key and the current **runtime** version. `save_brief.arguments.expected_version` is the separate **brief** revision. Acceptances also bind a current proposal digest; first-step agreements bind their own step version. These checks are deliberately distinct.

Runtime resource `actions` contain canonical `operation`, prefilled `arguments`, JSON-pointer `required_arguments`, scope/authorization requirements and concurrency flags. They are hints within the credential's scopes, not a grant: authorization is checked again at execution. For example, closing advertises `close_introduction` with the current context version; reassessing an answered request advertises `explain` with its current notice revision. Viewer credentials receive no executable resource actions. The manual demonstration retains its own presentation labels. Each catalogue `output_schema` resolves to the complete response envelope in `/openapi.json`; the document also describes resources, actions, pagination and errors.

```json
{
  "operation": "search",
  "arguments": {"text": "maintenance", "limit": 10},
  "expected_version": 4,
  "idempotency_key": "mission-search-001"
}
```

The version above is illustrative. Read capabilities or workspace to obtain the actual version, then reconsider a rejected command against current state. The standard-library `agentmatch_runtime.client.AgentClient` provides capabilities, operations, workspace, receipts and execute. It refuses remote URLs and never forwards credentials through redirects or environment proxies. It does not automatically retry effects. The `call --request <json-file>` CLI reads its token from `AGENTMATCH_TOKEN`, avoiding secrets in command-line arguments.

## Permission is separate from a valid command

Every operation must appear in the credential's explicit scopes. Private brief editing, previewing, explaining and reducing authority can operate within those scopes. Search terms, publication, contact, consent and first-step commitments additionally require an exact authorization or a supported standing mission. A publication preview digest is a consistency check, not a permission token.

For an exact command, a trusted administrator reviews `{operation, arguments}` in a local JSON file and runs:

```sh
python run_agent_runtime.py authorize --db local-data/demo.sqlite --key-file /protected/location/demo.key --credential <id> --request reviewed-command.json
```

This dry-runs against a disposable copy, then grants one use of that exact command bound to the current workflow version and a short real expiry. It cannot approve stale content or silently expand a board audience. An agent cannot call this administrator operation through HTTP. Changing any argument requires new authority.

For unattended bounded work, provision a standing mission once using `authorize-mission --credential <id> --policy mission.json` with the same administrator database/key flags. Revoke it with `revoke-mission --credential <id> --mission <mission-id>`. The policy shape is:

```json
{
  "own_notice": {"notice_id": "notice-example-owner", "revision": 1},
  "board_ids": ["community-tools"],
  "candidate_notices": [{"notice_id": "notice-example-peer", "revision": 1}],
  "operations": ["search", "propose_introduction", "accept", "propose_step", "agree_step"],
  "search_queries": [{"text": "", "limit": 20}],
  "first_steps": ["async-review"],
  "max_uses": 20,
  "ttl_seconds": 3600
}
```

Replace the illustrative references with exact currently approved notice references. The administrator captures current board memberships. The board list must cover the owner's exact membership set because the existing search contract searches all joined boards; contact candidates must share the owner's approved notice board. At most 20 candidate references and 10 exact query projections are accepted. This deliberately bounded candidate set is not broad adaptive discovery.

Once granted to each side independently, agents can search, propose, accept, arrange and agree the approved first step without another administrator call between those actions. Search results are restricted to the approved candidate notice revisions and labelled as that subset. Every operation rechecks current notice authority, board membership snapshots, blocks, matching/consent/capacity rules, expiry and budgets. Unrelated workflow updates do not invalidate a mission; changing an approved notice or audience does. Introduction notes and step detail must be empty, and the first-step type must be approved. Runtime concurrency versions still apply.

Standing missions cannot publish arbitrary revised content, send free text, change commercial terms, expand the candidate audience or report that work happened. Publication and reports retain separate exact authority. There is no model or autonomous scheduler in this slice: an external agent client chooses permitted calls. The machine-readable catalogue and capabilities describe what it can do. Validation lives in `src/agentmatch_runtime/policies.py`.

## Retry and failure handling

| Code/result | Agent action |
| --- | --- |
| `AUTH_REQUIRED`, `CREDENTIAL_INACTIVE` | Stop; obtain a credential through the trusted provisioning boundary |
| `SCOPE_REQUIRED` | Stop that operation; do not try another owner's identifier |
| `APPROVAL_REQUIRED`, `APPROVAL_STALE` | Inspect current permissions; request appropriate authority without changing the intended effect silently |
| `VERSION_CONFLICT`, `CONTEXT_CHANGED`, `STEP_CHANGED` | Read current state, reassess and prepare a new command |
| `IDEMPOTENCY_CONFLICT` | The retry key identifies a different request; investigate rather than resending under a fresh key blindly |
| `BUDGET_EXHAUSTED` | Stop scheduling commands; authority cannot be enlarged by the agent |
| `STORAGE_UNAVAILABLE` with `details.retryable: true` | Temporary database contention. Wait at least `retry_after_seconds`, then retry the identical request/key |
| `replayed: true` | The earlier effect committed. Mutations return null content; search can recover currently permitted original candidates within its bounded recovery authority |

Retain the **same key, arguments and expected version** when recovering a lost response. A successful retry returns the original receipt and current runtime version, without repeating the effect or charging another use. It never returns a cached peer payload: a peer may have since withdrawn information. Current authentication and scope are checked before replay.

A successful search grants at most **three recovery reads**, ending at the original exact/mission permission's expiry. Recovery retains only original notice references and authorization metadata. It rebuilds the result from current records and visibility checks, cannot introduce new candidates or revisions, and refuses changed board membership or revoked/invalid mission authority. Mission-use exhaustion alone does not prevent recovery of its already-spent search. Each recovery records metadata, retains the original command receipt, and consumes no additional command or mission use. A new search still needs its own permission/use. `SEARCH_RECOVERY_EXPIRED`, `SEARCH_RECOVERY_REVOKED`, `SEARCH_RECOVERY_SCOPE_CHANGED` and `SEARCH_RECOVERY_LIMIT` require stopping recovery and obtaining appropriate fresh authority; pre-repair retry records return `SEARCH_RECOVERY_UNAVAILABLE` because they lack this provenance.

Authorization, version checks, domain effects, budgets, receipt and retry record commit in one SQLite writer transaction. Independent processes sharing the same database cannot both spend the last command use. An encryption or commit failure is not returned as success. Failed domain commands discard their attempted workflow state and retain only an authenticated refusal receipt.

## Data, receipts and limits

The database stores one authenticated encrypted snapshot. AES-GCM uses a fresh random nonce on every write and authenticates the format and storage epoch. No Python pickle or dynamic class import is used; explicit allowlists restore the domain types. Credentials are random bearer secrets; only hashes are retained inside ciphertext. The database transaction includes workflow state and authorization state.

Receipts contain owner/credential identifiers, operation, timestamp, status, version and optional reason code. They exclude request text, tokens, private notes and query digests. Retention is the last 2,000 receipts across this fictional instance; pages explicitly report truncated history. Retry records last for the lifetime of the credential in this bounded database. Limits include 128 credentials, bounded per-credential grants and a 32 MiB snapshot. This design is for a small local integration instance, not a scaled board service.

Receipt pages are bounded to 100 items. Ascending readers use `after` and `next_cursor`; descending readers use `before` and `next_before`, with `has_more` indicating another page. Cursors are exclusive. The viewer starts with the latest 50 and offers older/newest navigation. A retention warning is conservative: pruning is global across owners, so it signals that the instance no longer retains its complete history without revealing another owner's records.

The viewer reads access metadata after its charged workspace read, so its remaining-use count includes that refresh. An exhausted key disables workspace refresh and offers reconnection with a newly provisioned viewer key; it does not create or expand permissions. The last snapshot is labelled as such, and receipt navigation remains available until expiry or revocation.

Clarification permits **20 sent and 20 received requests per rolling 24 hours**, plus at most **five pending in each direction**. Declined/completed requests count until their window expires. Per-request accounting timestamps use the injected real UTC clock and survive restart and replacement credentials; history and duplicate restrictions are retained. Pending requests also become unavailable after seven real days. Capabilities expose the owner's counts and next window-expiry times. Legacy requests without an accounting timestamp are conservatively counted from their first opening by the repaired runtime for one full window, and that migration is committed atomically.

Credential, permission and clarification-accounting expiry use real UTC. Other domain-event dates remain on the clearly labelled fictional clock. This separation must be replaced with the chosen production clock and expiry behavior before real use. Restore of a valid old database can restore old permissions: encryption does not provide rollback protection. Restart with the **current** database is tested; safe backup recovery is not claimed.

The process sees decrypted data and keys in memory. This milestone does not protect against a compromised same-user process, OS administrator, memory dump, swap or a stolen key file. It provides no passkeys, distinct wrapping keys, key rotation, secure erasure or verified recovery. Real private input remains gated by R1. See [the threat model](security/THREAT_MODEL.md).

## Verification

`python tools/verify_agent_runtime.py --browser` starts disposable real servers and two independently credentialed agents, exercises the collaboration workflow, restarts, checks retries and revocation, scans ciphertext for synthetic canaries and drives desktop/mobile Chromium against the actual viewer. It supplies synthetic administrator decisions; it is not a template for handing a model the vault key. The regression suite separately tests hostile requests, wrong keys, corruption, exact grants, scopes, concurrency and failed-commit rollback. Evidence and untested conditions are recorded in [the audit](audits/agent-runtime-flow-audit.md).
