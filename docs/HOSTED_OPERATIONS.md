# Hosted reporting and operator controls

**Recovery continuation:** optional B2-backed independent authority and encrypted off-server upload are implemented for review, disabled until configured and enrolled. They allow a fenced replacement host to restore an exactly current authenticated snapshot. See [setup, crash behavior and live evidence still required](OFFSITE_BACKUPS.md). The milestone results below predate this change and the owner-authorized VPS login test.

30 October 2026. This describes the milestone-7 local candidate. Production is unchanged; accounts remain disabled by default. Eleven concern-route tests passed, and enabled concern-report journeys passed 92 checks on each of Windows and Ubuntu. The reviewed wheel passed 54 installed, 58 hosted-browser and 93 static checks, plus a 53-check actual installed CLI recovery rehearsal. The combined suite ran 383 tests on each OS: Windows passed 366 with 17 POSIX-only skips; Ubuntu passed 382 with one Windows-uploader skip. Without optional dependencies, Windows passed 212 and explicitly skipped 171. The short operator checklist is [OPERATIONS.md](OPERATIONS.md); [STATUS](STATUS.md) and the [continuation record](HOSTED_V1_PROGRESS.md) distinguish verified results from launch work.

## Private reports

Each public board card and posting detail has a **Report** link. Every participant conversation, including a closed conversation, has the same link. An anonymous person is directed to sign in; report keys do not grant reporting authority. There are no public report listings or administrator HTTP routes.

`GET /app/report?kind=posting|conversation&target=<slug or conversation ID>` resolves the target from the current cookie owner. Posting reports require a currently visible, exactly approved posting belonging to another account, with neither person blocking the other. Conversation reports require membership and an existing other participant, including after closure, block or suspension. Opening a report form stores nothing and never reopens contact.

The form contains a fixed reason and optional plain-text details, at most 2,000 characters. Reasons are `spam`, `harassment`, `impersonation`, `privacy`, `unsafe_content` and `other`. Unsupported controls, HTML tags, numeric scores and percentages are refused. Native CRLF form transport is normalized to LF. The page displays no peer Google name, email or private profile.

`POST /app/report` accepts only `csrf`, `report_token`, `reason` and `details`. The token binds the actual owner, purpose, target kind/ID, current context hash, random nonce and ten-minute expiry. For a posting the hash binds its current publication grant, revision and payload; a newly published version invalidates the old form. For a conversation it binds that conversation and both participants without authorizing any new contact. Current target authority is checked again in the submission transaction. No caller-supplied subject/account selector is accepted.

The report, exact-retry receipt and operator alert record commit together. Repeating the same nonce and canonical values returns the same report ID/time; changed values conflict. An unavailable target is refused even on a retry. Each owner can create ten reports per rolling 24 hours; an exact replay consumes no new allowance, and quota refusal includes `Retry-After`. Invalid requests do not refresh sessions. Valid activity follows the existing thirty-day idle and ninety-day absolute session limits.

The confirmation contains only the report reference and creation time. It does not repeat the details, notify the subject, automatically hide content or change either person's contact choices. Own submitted reports are included in account export; reports about other people are not exposed to the subject through account pages. Deleting either the reporter or subject removes the associated report and pending operator alert records through shared lifecycle handling. Final lifecycle verification is recorded in STATUS.

## Operator CLI

The CLI is `python -m connectthebots_server.admin <command>` from the installed environment on the server. It relies on OS/SSH authorization, not a browser role or report key. It loads validated environment configuration and refuses an unavailable or stale migration history. Normal output is JSON; fixed failures exit 2 without raw exception, request or provider data. Read-only commands can expose personal account/report data in the authorized operator's terminal: do not put that output in public logs or tickets.

| Command | Effect |
| --- | --- |
| `invite create --days 7` | Create an invite valid for 1–30 days; show the random code once |
| `invite list --page 1 --limit 20` | Paginate metadata without codes/hashes |
| `invite revoke ID` | Revoke future use |
| `user show ID` | Explicitly inspect account identity, status and session/key counts |
| `user suspend ID` | Revoke sessions/keys, hide the posting, withdraw pending requests and close conversations |
| `user unsuspend ID` | Lift suspension only; it does not restore keys, sessions, contact choices, intake or email choices |
| `user delete ID --confirm ID` | Run the same confirmed account deletion lifecycle as the owner's web action |
| `posting hide SLUG` | Remove public visibility and terminally withdraw related pending requests in one transaction |
| `posting unhide SLUG` | Clear the moderation flag; restore visibility only if an exact-approved active grant exists |
| `reports list --page 1 --limit 20` | Show bounded report metadata, without free-text details |
| `reports show ID` | Explicitly inspect one report's details |
| `stats` | Show aggregate counts, email states, recovery hold, operator-mail configuration status and operator alert choices |
| `backup` | Create a protected backup under the configured recovery policy |
| `restore --from PATH` | Restore through the fail-closed recovery service; never copy a database over a live process |
| `recovery release` | Release a reviewed recovery hold only when its safety checks pass |
| `certificate check` | Verify the configured HTTPS certificate and record a near-expiry operator alert |
| `alerts abuse_report on\|off` | Set the independent operator moderation-alert subscription |
| `alerts certificate_expiry on\|off` | Set the independent operator certificate-alert subscription |

Invite codes are stored as hashes and never recoverable from list output. CLI pagination accepts pages 1–10,000 and limits 1–100. Account deletion outputs only `deleted: true`, not the erased identity.

Hiding keeps the approved grant so an operator can unhide that exact version; it cannot revive a request withdrawn by the hide. If a posting has no active grant, unhide only clears the moderation flag and returns `visible: false`, letting the owner approve a fresh publication. An invalid or revoked active grant is refused. Suspension also pauses report intake, turns off account email choices, cancels pending notices/mail and refuses future sign-in. Unsuspension requires separate owner/operator choices to resume each affected capability. Existing conversation messages are not erased merely by suspension or hiding. Operators must apply the published terms and review process; the software does not decide whether a report is true.

## Operator alerts and monitoring

Operator subscriptions are distinct from account email preferences. A moderation alert contains its report ID and an instruction to use the CLI, never reporter/subject identity or complaint text. Certificate alerts contain only the configured certificate's expiry information. They use the existing outbox/SMTP worker, including current-recipient checks, bounded retries and uncertain-delivery handling. Outbox mode stores without sending; no provider delivery is claimed.

Operator unsubscribe uses the same safe confirmation/one-click endpoint with a separate `operator` token namespace, signing purpose and subscription table. A token can disable only one operator alert kind and cannot affect an account preference. Changing the configured operator recipient invalidates links for the old recipient. Re-enabling via CLI does not replay old alert events. Treat these links as private capabilities; the report/CLI contents are never included in the link.

Certificate checking connects only to the configured public HTTPS hostname, with normal TLS trust and hostname verification. A certificate within fourteen days of expiry can create a deduplicated alert, at most once per certificate/week. A failed or expired certificate check exits with a fixed failure rather than falsely reporting health. This is a local check, not an independently hosted uptime monitor; actual public certificate monitoring remains a deployment verification item.

## Backups and trusted restoration

Growth schema recovery additionally clears ask-first/political sharing authority,
resets visibility/signature opt-ins, hides topic posts/replies and removes cached
agent responses. Per-account release does not republish that content. Migration
0012 removes legacy political answers and unversioned disclosures, and enrolled
cleanup must verify/advance independent authority before committing. Review the
[complete data-change and recovery contract](GROWTH_PRIVACY_RECOVERY.md) before
upgrading; old-schema snapshots are refused, so a fresh verified backup and
candidate-specific isolated restore are required after upgrade.

Runtime configuration defaults `BACKUP_DIRECTORY` to the database parent's `backups` directory. The Ubuntu deployment explicitly sets `/var/backups/connectthebots`, owned by `ctb` at mode `0700`; completed and temporary files are `0600`. The installed backup unit may write only the application and backup directories and has no network access. The certificate unit may write only the application directory; HTTP-only local staging skips its network check. Nightly backup runs at 02:15 local server time with up to five minutes' jitter; certificate checking runs weekly with up to one hour's jitter. Both timers are persistent. Notification delivery retains its separate minute timer.

`BACKUP_ENCRYPTION_KEY` is 32 random bytes encoded as base64url. New environment creation generates it without printing it. Existing environments with a missing or malformed key fail deployment preflight unchanged; setup never silently rotates a key or authentication secret. Keep the key protected separately from any copied backup. The deployment environment is root-owned `0600` and supplied by systemd to the service. Loss of the key makes retained backups unreadable. SMTP activation additionally requires a valid private `OPERATOR_EMAIL`; an empty value is permitted in outbox mode and is reported by `stats`.

Backup uses SQLite's snapshot API and streaming AES-256-GCM authenticated encryption. Its authenticated metadata binds format, creation time, database lineage, erasure generation, exact migration list and snapshot digest. Private temporary files are removed after normal processing and handled failures. A crash or forced termination can leave private plaintext scratch files in the protected directory; inspect and remove abandoned scratch files only after confirming no worker uses them. Copy only completed files matching `ctb-backup-*.ctb`, never the whole directory. Validation checks integrity, foreign keys, migration checksums and actual schema objects. A deletion racing snapshot creation is detected before the final encrypted file is published. The retention pass deletes only recognized, service-owned regular backup files older than fourteen days, after a successful new backup; unrelated files and symlinks are not deleted. Failed backup jobs therefore require operator attention to avoid overdue retention.

Every account deletion and successful waitlist removal increments `erasure_generation` in the same transaction as erasure. Pending OAuth attempts are invalidated, and a callback that began before the generation changed must restart instead of recreating a deleted account. This is deliberately conservative: an unrelated erasure can require a fresh sign-in attempt. Deterministic regressions cover both an exchange already in flight (`test_inflight_verified_callback_cannot_recreate_erased_account_but_fresh_login_can`) and an unconsumed attempt (`test_erasure_invalidates_unconsumed_attempt_without_identity_tombstone`). Restore requires an intact, trusted **current database** with the same lineage and **exactly the same generation** as the backup. It does not filter an older backup or trust its own stored watermark. Any erasure invalidates every earlier backup, even one that would not contain the affected person. A fresh backup after an erasure restores availability. The supported procedure cannot recover on a fresh host, from a missing/corrupt live database, or from an old live-database copy treated as new authority. This is a deliberate availability gap, not a complete disaster-recovery solution. Off-server recovery needs a separate reviewed source of current erasure authority.

Managed app, worker and CLI processes cooperate through a persistent filesystem lock. Ordinary operations share it; restore requires a nonblocking exclusive lock and refuses while any managed process is active. Stop the app, its timers and running workers, keeping the current database intact. The chosen encrypted source is authenticated and validated in a private temporary file; current lineage/generation are compared again before replacement. Restore first creates an encrypted checkpoint of the current target, then replaces it atomically and syncs the containing directory. It refuses stale or incompatible schema rather than migrating a recovery source.

Feature content comes from the backup, but account identity/profile rows and blocking restrictions come from the trusted current database. Accounts created after the snapshot remain present and suspended, so later suspension cannot be bypassed through new open signup. Current blocks are carried forward so ordinary unsuspension cannot remove a later blocking choice. An identity ID/Google-subject conflict, or a backup account missing from the trusted current roster, is refused even when generation metadata matches. The service then quarantines all current accounts: suspend everyone; remove sessions/OAuth attempts and unsubscribe handles; revoke keys, invites and publication grants; hide postings and advance draft revisions; withdraw pending introductions; close conversations; pause intake and clear deadlines; turn off email choices; cancel notices and queued mail. An in-flight mail record becomes uncertain, never a retry. Public/contact authority is not revived merely because it existed in the backup.

The recovery hold returns 503 for health and blocks sign-in/external effects. Rotate `SESSION_SECRET` in the protected environment and run `recovery release` only after reviewing the state. The service retains the old secret fingerprint so processes still using it remain held. Accounts stay suspended after release. Review each account before `user unsuspend`; a posting without an active grant may need `posting unhide` to clear the moderation flag, followed by the owner's fresh publication approval. New keys, intake choices, introductions and email opt-ins require explicit new choices. Old conversations never reopen automatically.

## Off-server copies and independent recovery

Unenrolled databases still require trusted live authority. Optional independent B2 enrollment records protected changes remotely before local commit, and `backup` uploads only completed encrypted files with read-back verification. Exact current independent authority permits `restore --lost-host --old-host-fenced` only to a missing target. All quarantine, secret rotation and account review requirements remain. Setup, storage-policy checks, availability tradeoffs and required real-provider rehearsal are in [OFFSITE_BACKUPS.md](OFFSITE_BACKUPS.md). No production destination or key custody is implied by this implementation.

## Identity, verification and remaining work

The public interface uses Connect the Bots. No personal identity is inserted from local notes, Google profile or a guessed legal entity. Privacy/terms retain an unapproved controller-identity placeholder until an accurate arrangement is agreed. An address is optional in configuration and is rendered only when explicitly supplied; contact can show the product brand and support route without an empty address. This is a rendering choice, not a legal conclusion that a brand alone satisfies controller identification. The actual controller identity/contact arrangement and exact trust pages still need approval. See [ICO privacy information guidance](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/the-right-to-be-informed/what-privacy-information-should-we-provide/).

The installed rehearsal covers exact-generation refusal, current identity/profile and block preservation, global quarantine, rotation/release and actual CLI commands. Evidence is in `artifacts/operator-recovery-installed/` (53 checks), `artifacts/operator-recovery-final/` (52 source-mode checks) and `artifacts/operator-staging-final/` (54 service, 58 hosted-browser and 93 static checks). The final wheel SHA-256 is `5012dbe861afc5602253538d65e208f871a2e780169326c1cf1db53c706643be`; installed release `04c5653d78f27d837c46d88a` includes all nine migrations and matches all 83 server files. [STATUS](STATUS.md) records the full evidence paths and the 74/144/120/166/116 account/report/board/interaction/notification browser regressions. Real Google acceptance, actual email delivery and incoming support mail, approved trust pages, independent monitoring, a recovery rehearsal and explicit deployment approval remain launch requirements. Off-server storage has not been selected or configured. No personal controller identity, private mailbox, provider account or external storage value belongs in tracked documentation.
