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

Adding ordinary content alone does not advance authority. Profile/answer no-ops,
timestamp refreshes, routine check-ins, match-history writes, duplicate-operation
receipts and derived access calculations do not write markers. Removing topic
signals when a board is created does advance authority because stored query
labels are being erased. A political consent decision always advances authority,
even if it repeats a choice; it is a new recorded decision.

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
snapshot. Keep the live app invite-only/outbox until the separate launch checks
and reviewed deployment decisions are complete.
