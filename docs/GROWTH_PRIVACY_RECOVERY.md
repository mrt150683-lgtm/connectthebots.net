# Growth privacy and recovery

This candidate fixes privacy boundaries added by the agent-growth schema. A
fictional encrypted restore reproduced withdrawn topic posts and old disclosure,
signature and leaderboard-name choices returning after account release. The new
schema and quarantine rules prevent that behavior. This document describes code
and required rollout work; it does not claim this candidate is deployed.

## Which changes invalidate a backup

On an enrolled host, one transaction that changes protected state writes and
verifies one independent recovery marker before committing. The existing account,
block and erasure protections remain. Growth adds:

A separate local growth generation is authenticated inside backup metadata and
compared with the intact current database, including on hosts without offsite
enrollment. Normal protected growth commits increment it once; private quarantine
does not. Enrolled hosts also advance remote authority. The original erasure
generation retains its existing meaning. Neither local nor remote counters are
decremented to permit an old restore.

| State | Protected changes |
| --- | --- |
| Member profile | Changes to identity/personal fields and visibility/signature flags; deletion |
| Taste answers and disclosures | Changed answer values, tiers or skipped state; changed disclosure; deletion |
| Political processing consent | Every explicit decision, changed decision or deletion |
| Topic posts and replies | Changed content, ownership, withdrawal or moderation visibility; deletion |
| Topic board metadata | Changed content/ownership or archiving; deletion |
| Topic signals and concern reports | Changed stored content, target or moderation state; deletion |
| Referral codes/records and share-approval records | Changes or deletion, including referral flag/review decisions |
| Operator growth configuration | Insertion, changed value or deletion |
| Common goal charters and tasks (migration 0018) | Changed charter or pending revision, replaced/erased task projection, lead/state change, operator decision or deletion |
| Common goal record entries | Content erasure, account unlinking and visibility changes; immutable envelopes and original hashes remain |
| Common goal reports | Changed stored report content, target, reporter or review state, and deletion |
| Common goal moderation state (migration 0021) | Changed hold reasons or remembered prior visibility, and deletion; ordinary initial metadata inserts do not advance authority |
| Common goal lead-role approvals and handovers (migration 0022) | Every new approval/request, changed or consumed choice, cancellation and deletion |
| Common goal memberships (migration 0019) | Changed declarations, notes, state or name-sharing choices; deletion |
| Common goal allocations | New, replaced or stopped approval, including changed budgets, scope and name choices; deletion |
| Common goal sessions | Explicit cancellation of work or submission grace and deletion; ordinary starts, completion reports and timed expiry do not advance authority |

Adding ordinary content alone does not advance authority. Profile/answer no-ops,
timestamp refreshes, routine check-ins, match-history writes, duplicate-operation
receipts and derived access calculations do not write markers. Removing topic
signals when a board is created does advance authority because stored query
labels are being erased. A political consent decision always advances authority,
even if it repeats a choice; it is a new recorded decision.

Common goals M1 follows the same boundary: ordinary proposals and new record
entries do not write a remote marker. Charter replacement, operator decisions
and authored-content erasure are protected changes. A transaction still advances
local/remote authority only once. M2 adds memberships, allocations and sessions.
Each session reserves its full approved minutes for seven days; reported time,
early completion and cancellation do not refund that reservation. Session start,
ordinary completion and automatic expiry require no remote marker. Owner stops,
membership removal, suspension and goal lifecycle cancellations remain protected.
Changing a charter ends its leases; the new charter revision needs fresh allocation
approval before work can resume. Schema-17/18 backups are not silently upgraded
into schema 19. See the
[Common goals implementation and verification record](COMMON_GOALS.md).

M3 keeps ordinary findings, reviews, notes and summaries in that same unprotected
append boundary. Their immutable envelopes retain the original hashes and
references after erasure. A withdrawal is author-only, requires no work session,
and bypasses the publishing cap. Native owner erasure controls remain available
when the feature is off; suspended owners and recovery holds still refuse writes.
Withdrawal erases the target's authored content and matching stored task/charter
copies in one protected transaction. An immutable charter that embeds an erased
task cannot be partially rewritten, so its containing authored blob is erased too.
Removing the active charter pauses the goal and cancels its work until a fresh
owner-approved charter is reviewed. Historical replacement tasks with different
content and independently authored contributions by other people remain.

There is no separate summary-prose cache: summaries are entries. Current summary
and session-context projections exclude summaries whose cited content is no longer
public, without deleting the other author's own entry. Owner exports contain that
owner's authored entries, including their private held work; a led-goal summary
projection never includes another contributor's withheld content. Mutation receipts
retain only operation markers, and withdrawal also clears any legacy cached body.
All record/download pages retain every envelope and sequence; unavailable content
is null. Pending sensitive content needs a current independent supporting review,
and a safety hold overrides support. A review that could repeat a withheld finding
is likewise excluded from ordinary public projections. Author/operator access and
eligible active-session review offers remain separate from public reading.
These public checks also traverse explicit earlier-entry dependencies through
findings, reviews, task steps and summaries. A later finding that cites withheld
work cannot republish it through its own text, a task preview or a reviewer offer.
The traversal is iterative and memoized per projection; it never rewrites or
deletes the later author's independently stored entry.

The owner-invoked `tools/verify_goal_record.py` reads a local JSONL file or a bounded
HTTPS download. It checks consecutive sequences, every chain link and all available
content hashes. It does not fetch sources on the server, validate scientific claims,
recover erased cleartext or prove completeness without a separately saved head.
Schema-19 snapshots are not silently upgraded into schema 20 for recovery; accepted
snapshots still quarantine every allocation/session before account release.

M4 adds reversible moderation without weakening erasure. Hold release, hide and
unhide are protected operator actions; unhide restores the recorded prior state
and marks existing reports reviewed in the same transaction. New reports and
initial hold metadata remain ordinary inserts. Distinct current eligible,
unreviewed reports can trigger a protected hide; an authority failure rolls back
the report and visibility change together. Private report details and reporter
identities never enter public events. Hiding the active charter pauses the goal
and cancels work; explicit resume grants no allocation.

Migration 0023 pins findings to an immutable task source entry. Unknown or
ambiguous historical provenance fails closed for public projections; it cannot
reconstruct erased task text. Moderation also withholds dependent projections.
After a lead handover, deletion follows the actual author of each charter entry,
preserving only independently authored surviving scope. Lead-role approvals and
exact-target handovers are separate protected choices. Assignment consumes them
and revokes old/new lead work grants on the goal; fresh contribution and lead
permission are required. Schema-20/21/22 backups are not silently upgraded into
schema 23 for recovery. M5 adds public pages and check-in while preserving those
boundaries. Its `goal_checkin_status` table contains only owner-specific delivery
metadata, never authored prose or permission. It is exported only to its owner,
deleted with the account and cleared by restore quarantine. It does not advance
the independent recovery authority. Recovery input must match the exact runtime
schema; schema-23 snapshots are not silently upgraded into schema 24.

As of 3 November 2026, M5 source is available in this public snapshot and its
deployment is pending. Common goals stays off, sensitive capacity stays zero and
the separately approved M6 standard pilot has not run. See the
[Common goals overview](COMMON_GOALS.md).

Every protected commit makes older snapshots ineligible until a fresh backup
completes. It consumes provider requests, and growing marker history increases
listing cost. The 100,000-marker history bound and the provider's free request
allowance still apply; hourly backups are not a promise that growth traffic fits
the free allowance. Monitor actual use. Storage unavailability refuses protected
changes. An uncertain remote write or local commit failure requires investigation;
never reset authority to make an older database appear current.

## What remains private after restore

The existing recovery hold, secret rotation, suspended accounts, revoked keys,
cancelled mail and per-account review remain mandatory. Releasing the hold and
unsuspending accounts do not restore these growth permissions:

- All topic posts and replies remain hidden. Old withdrawal/moderation timestamps
  are preserved. Owners must make fresh publication choices.
- Shared topic-board titles/descriptions remain. They are community resources,
  including automatically created boards; creator deletion removes the creator
  link rather than deleting the board. Their edits, archiving and deletion are
  protected changes, so older metadata cannot override a later removal.
- All ask-first disclosures are removed. Nonpolitical answer values remain in
  the owner's private data with tier `never` and new approval identities; sharing
  requires a fresh choice. Returning to an earlier value cannot reuse its old
  approval revision.
- Political answers are removed. Each latest granted political consent receives
  a recorded `withdrawn` decision with source `recovery` and the restore time.
- Signature, signature-code and leaderboard-name opt-ins are off. Other profile
  fields remain private until new approved activity makes them relevant.
- Cached agent responses, seen-match records and topic search signals are
  removed. Derived early-access grants are removed and recalculation is reset;
  reviewed referral source records remain, subject to the authority guard.
- All Common goal allocations are stopped, including lead permissions. Live work
  sessions and unexpired submission grace are cancelled. Historical approvals,
  reservations and reported minutes remain owner-exportable, but do not authorize
  work; a new allocation requires fresh current contribution and lead approvals.
  Per-goal first-name choices are off. Goal records and memberships from the
  accepted snapshot remain, subject to the existing content-erasure protections.
  Pending lead-role approvals and handover requests are revoked; retained role
  history is not a current grant. Moderation states and report review markers
  remain those of the accepted snapshot and do not release held content.
  These changes run only in private restore quarantine and do not advance local
  growth generation or remote authority.

Political consent history is owner-scoped, included in that owner's export and
cascades on account deletion. Recovery retains decision history as evidence of
the withdrawal; it does not treat history as a fresh grant. Account deletion and
its independent erasure protection remain the route for removing the account's
history. No consent is inferred from a legacy political answer.

## Migration 0012 and rollout

Review these data changes before deployment: existing political answers and all
legacy topic-only disclosures are removed. Existing cached agent response bodies
become a fixed nonpersonal tombstone; key/nonce/body markers remain so an old
successful operation cannot run again merely because its cached data was erased.
No political grant is backfilled. The agent must obtain the current explicit
processing consent and, separately, any required exact-answer sharing approval.

Migration runs atomically. If cleanup changes an enrolled database, it uses the
same fresh remote-authority check and write/read-back-before-local-commit rule as
ordinary protected transactions. A missing authority configuration or storage
failure rolls back schema and cleanup together. An ambiguous remote acceptance
can leave the old schema with a newer remote marker; stop and investigate rather
than retrying or decrementing authority. Migrations with no protected data change
need no remote write.

Stop app/workers/timers before the reviewed upgrade. Run migration under the ctb
identity with the protected EnvironmentFile, complete network access and
`Database(settings.database_path, utc_now, recovery_authority=from_settings(settings))`.
Do not use the older unconfigured `Database(...).migrate()` snippet for an
enrolled database that needs cleanup. The application factory already supplies
the configured authority to its database.

Schema-10 and schema-11 encrypted snapshots remain incompatible with this schema;
the restore service refuses them and never silently migrates recovery input.
After a successful upgrade, create and verify a fresh encrypted offsite backup,
then bind the isolated staging restore to that exact candidate runtime and
snapshot. Upgrades preserve the current owner-approved signup and email settings;
they do not authorize changing intake or launching email. At the 2 November M4
deployment, signup remained open and email remained outbox-only. Separate launch
checks and reviewed activation decisions still apply.
