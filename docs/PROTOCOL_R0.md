# R0 negotiation and social simulation

This implementation is a deterministic, fictional reference for permission and
state transitions. It does not create a network connection, encrypt anything,
authenticate a person or persist a queue. Its peer bindings and owner actions
are explicit fixture assumptions. Passing its tests does not establish the R1
vault or the R2 transport, identity, crash-recovery or receipt guarantees.

## Run the examples

```bash
agentmatch simulate --scenario mutual_acceptance --as-of 2026-10-25T12:00:00Z
agentmatch simulate --scenario lost_ack_deduplication --as-of 2026-10-25T12:00:00Z
agentmatch simulate --scenario block_then_unblock --as-of 2026-10-25T12:00:00Z
agentmatch simulate --scenario private_coach --as-of 2026-10-25T12:00:00Z
agentmatch simulate --scenario contact_asymmetric_delivery --as-of 2026-10-25T12:00:00Z
```

`simulator.scenario_names()` lists the bundled scripts. CLI callers select a
known scenario, not an arbitrary transcript or script path. `script_completed`
only says a script ran; independently asserted tests establish its expected
policy behavior.

## Architecture and authority

| Module | Input and output | Boundary |
| --- | --- | --- |
| `topics.py` | Exact display grants and already permitted topic statements → identity/topic/context projections | No private profile, source store, inferred real name, contact field or arbitrary interest expansion |
| `simulator.py` | Typed fixture events → new immutable negotiation state and proposed effects | `transition` is pure; in-memory adapter reserves quotas before accepting an effect |
| `channels.py` | Exact proposal and two current owner acceptances → in-memory membership; explicit human sends → queue entries | Proposal kind, context, participants and assistance capabilities are digest-bound |
| `coach.py` | Enabled, explicitly invoked request over approved context → private draft | No archive, embedding lookup, network, model, tools or shared-send method |

The proposal digest covers context and capabilities. Editing the topic, alias,
purpose, version or assistance policy invalidates earlier acceptances. A single
acceptance, a repeated acceptance from the same owner, revoked authority or an
expired acceptance cannot open a channel. Contact permission is a different
proposal kind and never follows from channel acceptance.

`ApprovedTopicStatement` is an input from the trusted disclosure projection,
not evidence that arbitrary peer text is approved. Integration constructs it
only after the core grant checks. Grounded bridges intersect exact permitted
topic identifiers. They do not infer intensity, invent shared beliefs or fill
four slots when only one is supported. Sensitive statements are excluded from
this R0 topic builder. With no supported common thread, an owner-proposed
neutral invitation is labelled as such and changes no match score.

## Negotiation behavior

The initial state is `UNPAIRED`. A fictional verified invitation allows
`PAIRED_IDLE`, then setup-bound HELLO nonces and the coordinator's session open
allow `OPENING`. Both session acknowledgments are required for `EVALUATING`.
The lower of the two fixture key identifiers is coordinator; that choice
confers no extra disclosure or owner-approval authority. A competing setup is
closed without a claim effect.

The protocol then supports a fixed nonsensitive question registry, scoped
claim exchange/withdrawal, ordered question/answer turns, an exact proposal,
proposal acknowledgment, individual owner acceptances and separately permitted
fictional contact release. `PROPOSAL_ACK` confirms an object, not human consent.
`OWNER_ACCEPT` remains a fixture assertion, not proof that a remote human acted.

An unavailable answer contains no private reason. A permitted Boolean `false`
remains a known negative value. Unregistered score queries, reordered turns,
wrong correlations, unknown payload fields, an unknown actor and an oversized
full envelope are rejected. Private refusal/unknown/failure reasons collapse to
the same generic external stop.

Message IDs are idempotent. Repeating an exact event cannot reserve another
effect or charge another quota. Reusing the ID with changed contents aborts.
Peer-claim versions cannot silently change value. Learned withdrawals remove
their cached authority and cancel dependent pending proposals; expiry excludes
new use. No claim is made about an offline peer learning a withdrawal instantly
or deleting previously received content.

`contact_asymmetric_delivery` exercises separate sender and receiver state:
the receiver records one application receipt while the sender remains
`sent_unacknowledged` after a lost ACK. The receiver has not sent its own contact.
Retrying preserves the sender's release ID and does not produce another logical
receipt. A later explicit ACK changes only the sender's knowledge. Bilateral
approval therefore does not imply atomic bilateral delivery. This is an
in-memory event sequence, not injected process-crash or durable-recovery evidence.

Pending disclosures and proposals retain their claim/grant dependencies. Known
source invalidation, learned withdrawal, expiry or owner revocation denies the
dependent effect immediately before execution. A human's deliberate composer
message is its own authorized action under current channel membership; it does
not automatically inherit an archive-source dependency from the introduction.
Source facts are never silently attached to that message. Historical delivered
context can remain visible, but does not renew automated matching or coaching
permission.

Numeric limits come from `data/release_defaults.json`, not UI copies. The
in-memory ledger enforces the one active pair-purpose session rule, owner active
session/day limits, inbound/outbound question limits and contact-release limits
under a process lock. Turn counts and clarification rounds survive a simulated
refresh. A separate `RunBudget` models shortlist/probe limits. The reference
simulator never resets owner quotas because a peer supplies a new identity.

The six-question negotiation budget is distinct from the six-question interview
default. Negotiation and interview extensions are unrelated capabilities.

Evaluation expiry does not destroy a proposal awaiting owner review. Initial
release or channel opening still needs current session and consent authority.
An already-open human channel has independent membership and remains usable
after the short matching session ends. Clock rollback refuses affected effects.

## Human messages, reconnect and block

The chosen topic appears as **Introduction context**, not a fabricated human
turn. A channel starts with zero human messages. A queued message requires an
explicit human send and a human author; an agent draft cannot call that action
on its own behalf. The full encoded message envelope is checked against the
contract byte limit. Stable IDs bind text and membership epoch.

Offline messages are `queued_local`. An attempted send moves to
`sent_unacknowledged`. A simulated application receipt moves to `acknowledged`,
and never means read. A lost ACK leaves the sender unsure. Retrying the same
message ID is deduplicated by the simulated receiver ledger; changed contents
under that ID fail. This ledger is ordinary process memory, so R0 does not
establish durable deduplication or restart recovery.

Owners can explicitly shorten the default unsent expiry; a longer requested
lifetime is capped at the contract maximum. Changing an existing message's
expiry requires a new immutable message ID. Expired and cancelled entries
cannot resend or renew themselves. The queue is
bounded independently of message size. Fresh connections must revalidate the
same participant bindings and membership epoch. Blocking closes membership,
cancels pending messages and rejects further effects immediately. Unblocking
does not reopen a membership or revive messages; a new accepted channel is
required. Already displayed content cannot be recalled.

## Private coach

Coaching starts disabled. An owner explicitly enables it and invokes each
request. The normal context is the agreed introduction context, subject to its
current reuse authority. Every request supplies the injected clock and checks
that the context has neither expired nor been withdrawn; a historical visible
context panel does not grant new automated reuse. Human-chat content additionally requires mutual channel
policy and an explicit list of received message IDs. The R0 template returns a
question from approved context; it does not interpret a prompt embedded in chat.
No source-store or shared-send capability is available. The result is marked
owner-only and is not automatically reingested as matching evidence.

## Verification and remaining limits

`tests/test_social.py` and `tests/test_protocol.py` test the independent
state/permission oracles, including exact acceptance, expiry, IDs, private
drafts, queue limits and concurrent quota reservations. `T-PROTO-002` here checks
only rejection of a wrong *fixture* binding. Real signing-key pins, DTLS
fingerprints, signaling replay and WebAuthn remain unimplemented later-release
tests.

The R0 digest helper uses a restricted, deterministic JSON representation with
ASCII field names and strings/booleans/null/safe integers. It rejects nonfinite
or arbitrary fractional numbers and surrogate strings. It is **not** a general
RFC 8785 implementation or a substitute for production conformance vectors.
Digests do not prove truth or hide guessable personal values. Contact digests
remain inside local simulated authorization state; the trace never emits them.

Persistent transactions, real network ordering, authenticated peer assertions,
encrypted queues, process isolation, crash/restart behavior and human usefulness
all require separate R1/R2 implementation and evidence. The simulator exports
`network_used: false`, `cryptography_verified: false` and
`persistent_storage_verified: false` so demonstrations preserve that distinction.
