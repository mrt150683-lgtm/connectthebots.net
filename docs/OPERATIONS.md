# Connect the Bots: operator checklist

**Current checkpoint, 30 October:** the owner approved the combined consent/recovery/notice update; its original PR histories are integrated here and its application/site bytes match reviewed candidate `26e785f`. Last verified live release `6017c823d5c0ad4e997cdd2b` retains open signup and outbox-only email. Final main CI, the attended deployment, real isolated staging restore and fictional VPS acceptance remain pending. This supersedes earlier pending-approval/source-state wording below; it does not change the operational limits or authorize an email send. See [current status](STATUS.md).

**Later public observation, 30 October:** `/signin` now shows a Google button without the invite step, and `/privacy` no longer shows its draft warning or operator-identity placeholder. A separate open PR #35 describes signups as open. These observations suggest an external activation after the last private check; the exact installed release and protected settings have not been reverified over SSH. They do not establish completed launch acceptance or shipment of the consent/recovery safeguards. The owner explicitly chose to keep signups open and prioritize the fixes. This retains the observed public signup state; it does not complete safety/acceptance work or approve an unreviewed deployment. The public observations were recorded at 19:36:34–35 UTC in `artifacts/public-launch-gate-divergence.json`. No server change was made as part of this documentation update.

30 October 2026. The owner confirmed retaining release `383e9a4281a77c0429fbe399`. All 98 installed server files match verified main `5d02f4e9fbfc998218dcbbde5d7507950e50c475` (PR #30 growth and PR #32 HTTPS setup correction), with migration 0011. At 18:56:58 UTC on 30 October the app and backup timer were active; `APP_ENV=staging`, `SIGNUPS=invite`, `EMAIL_MODE=outbox`, `LEGAL_PAGES_APPROVED=false` and sign-in enabled were unchanged. At that private check signups were not public and routine email was off; the later public observations above supersede any assumption that those gates are still unchanged.

Source main is now `a928ff0` after the onboarding merge; the latest exact live inventory remains the recorded 18:56:58 check. Pin the next combined candidate explicitly.

**Recovery work is held for the growth safety correction and a new build-pinned plan.** The earlier schema-10 upload/read-back and six-minute alert/recovery receipts passed, with the normal hourly/65-minute policy restored. The current growth release has a reproduced privacy gap on restore; do not reuse the older restore plan or treat transport repair as resolution. Final migration-0012 implementation, reviewed upgrade, fresh matching-schema backup, isolated staging restore and real-VPS fictional acceptance are pending. Use authorized SSH; there is no admin website. [Current launch record](HOSTED_V1_PROGRESS.md) and [full operations contract](HOSTED_OPERATIONS.md).

## Deploying from the owner's PC

The owner keeps a dedicated clone (for example `Desktop\ctb-deploy`) and runs `DEPLOY.cmd` in it. The first run creates `~/.ssh/ctb_deploy`, adds an SSH config entry for the VPS and installs the public key with one password prompt. After that it pulls main, builds the wheel with the pinned build tools, takes a verified offsite backup, runs `site/deploy/deploy.ps1 -Environment production -WithApp`, and checks `/healthz`. It stops before changing anything if the backup is not verified. If it stops later, read the message before running it again. Running it is the owner's approval for that deploy.

## Installed CLI

Define this private-shell helper. systemd reads the protected environment; do not source the file or put secrets in arguments.

```bash
ctb-admin() {
  sudo systemd-run --quiet --wait --pipe --collect \
    -p EnvironmentFile=/etc/connectthebots/app.env -p User=ctb -p Group=ctb \
    /opt/connectthebots/venv/bin/python -m connectthebots_server.admin "$@"
}
```

Account/report inspection and newly created invites can reveal private information. Do not record their output in public logs.

| Need | Action |
| --- | --- |
| Deploy | Build the reviewed wheel/hash bundle, verify staging, then use [HOSTED_SERVER.md](HOSTED_SERVER.md). Production requires separate approval. |
| Owner numbers | On the owner's PC: `STATS.cmd` in the deploy clone (read-only, `STATS 30` for 30 days). On the server: `ctb-admin numbers --days 14` for counts only (sign-ups, agents, check-ins, referrals, introductions) and, as root, `/opt/connectthebots/venv/bin/python -m connectthebots_server.traffic --days 14 /var/log/nginx/connectthebots.access.log*` for approximate visitors and film plays (`/v/play`, `/v/sound`, `/v/done` beacons, logged as `/video-*`). |
| Health and statistics | `ctb-admin stats`; `curl --fail http://127.0.0.1:8100/healthz`; `systemctl list-timers 'connectthebots-*'`. Recovery hold intentionally returns 503. Protected `/stats/` contains website statistics, not exact person counts. |
| Review concerns | `ctb-admin reports list --page 1 --limit 20`, then `ctb-admin reports show REPORT_ID` when details are needed. |
| Hide a posting | `ctb-admin posting hide SLUG`. Pending introductions are withdrawn. `posting unhide SLUG` never revives them or publishes an unapproved draft. |
| Suspend an account | `ctb-admin user show USER_ID`, then `ctb-admin user suspend USER_ID`. This revokes access, hides the posting and closes contact. `user unsuspend USER_ID` does not restore those choices. |
| Delete an account | After explicit approval: `ctb-admin user delete USER_ID --confirm USER_ID`. Earlier backups become ineligible for supported restoration. |
| Invites | `ctb-admin invite create --days 7`; `invite list`; `invite revoke INVITE_ID`. Protect the code shown once. |
| Backup and certificate | `ctb-admin backup`; `ctb-admin certificate check`. The installed monitored backup schedule is hourly and active after the completed attended alert rehearsal; certificate checks remain weekly. The normal external heartbeat threshold is 65 minutes. Default installations without the optional monitor remain nightly. Investigate failures and overdue backups; local health does not prove offsite recovery. |
| Security contact | Verify receipt at the configured role `CONTACT_EMAIL`; set `SECURITY_CONTACT_EXPIRES` privately to a fixed `YYYY-MM-DDTHH:MM:SSZ` value later than now and at most 365 days ahead. Review and renew before expiry, restart the app, then check `/.well-known/security.txt` over public HTTPS. |

## Rollback and recovery

**Application rollback:** stop the app/workers; repoint `/opt/connectthebots/venv` to a reviewed prior release only if it supports the current schema. Restart and check health. Otherwise deploy a compatible correction; do not replace the database to make old code start.

**Restore planning:** the existing command is `ctb-admin restore --from ABSOLUTE_BACKUP_PATH`, but do not execute the old reviewed plan on current growth data. The safety candidate adds a local growth-generation match and broader independent protection, hides restored topic posts/replies, resets retained answers to never-share, removes disclosures and political answers/consent, and clears optional name/signature choices. Schema parity is exact. Before an approved upgrade, stop app/workers/timers and run migration with the configured independent authority; cleanup can make a remote write before local commit. Any uncertain acceptance requires investigation, never an authority reset or blind retry. After upgrade, create a fresh verified backup and prove the isolated staging restore. For a separately approved lost-host exercise, follow [old-writer fencing and recovery](OFFSITE_BACKUPS.md#recovery-and-availability); a downloaded-snapshot inspection is not that exercise.

Every account deletion or waitlist removal invalidates **all earlier backups**. With independent authority enrolled, every protected account/profile/suspension/block change also invalidates earlier snapshots. The pending growth correction extends this to protected growth changes; its local growth-generation check also covers intact unenrolled hosts. Take a new verified backup after protected changes when recovery availability is needed. The periodic success signal is evidence at that instant, not a guarantee that later changes are covered.

Restore preserves the trusted current account roster/profiles and block graph, then suspends everyone, revokes credentials/grants, closes contact and disables intake/email. Rotate `SESSION_SECRET` privately, review the restored state, then run `ctb-admin recovery release`. Restart under the new secret and check health. Review each account before unsuspending; publication, keys, introductions and preferences require fresh choices. Old processes remain held.

## Secrets and outstanding gates

Edit `/etc/connectthebots/app.env` privately with root authority, retain mode `0600`, and restart affected services. Rotate Google/SMTP credentials at their provider too. `SESSION_SECRET` rotation invalidates signed forms/unsubscribe links; it does **not** revoke stored sessions or report keys. Suspend an affected account; global compromise needs a reviewed incident procedure. Preserve the backup encryption key for retained files.

The security-contact file uses a role mailbox without a personal name; it is unsigned and advertises no encryption key. Missing/stale configuration or HTTP returns 503, and the expiry never renews itself. Configuration belongs in the protected environment, not committed source. On 30 October the public health endpoint returns 200, but `/.well-known/security.txt` returns 503. Incoming mailbox receipt is now verified. The security endpoint still needs the protected contact/expiry configuration and a post-deployment HTTPS check. `2027-09-30T00:00:00Z` is a prepared fixed expiry within 365 days, not an installed setting or automatic renewal.

Copy only completed encrypted `ctb-backup-*.ctb` files, never the directory: interrupted jobs can leave private plaintext scratch files. Owner Google login, post-update logout/relogin and the external health alert are confirmed. Restricted backup credentials and separate encryption-key custody are installed/retained privately. Real offsite upload/read-back and downloaded-snapshot quarantine inspection passed; no replacement host has been restored. Overdue/recovery alert receipts and normal-policy restoration passed. One approved email reached the inbox and passed sender-authentication/header/unsubscribe checks; original preferences and the notification timer were restored. The provider added HTML and a tracking image; the owner chose accurate anonymous-measurement disclosure and routine mail remains outbox-only pending final approval. Remaining: growth safety implementation and reviewed deployment, combined growth/Brevo notice approval, required staging restore, remaining real two-account journeys, the owner-selected temporary Gmail reply practice and the pending mailbox-retention decision, accurate trust-page approval and final public-launch authorization. [Remaining work](HOSTED_V1_PROGRESS.md).
