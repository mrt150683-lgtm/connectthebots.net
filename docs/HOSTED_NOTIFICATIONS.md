# Hosted notifications and email

30 October 2026. This describes the milestone-6 local candidate. Account access remains disabled by default, email defaults to an outbox without sending, and production is unchanged. The SMTP adapter exists, but no real Google login, provider delivery or public rollout is established by these tests. See [status](STATUS.md), [email setup](EMAIL_SETUP.md) and [remaining work](HOSTED_V1_PROGRESS.md).

## In-app notices and preferences

The bell is rendered only from the actual signed-in session, with an owner-bound unread count. Public pages do not infer or expose an account. `/app/notifications` shows newest-first generic notices, twenty per page, with a separate POST to mark a notice read. Opening a page does not mark anything read. The service accepts pages 1–10,000 and page sizes 1–50; the web page fixes the size at twenty. Counts, navigation and listing recheck current eligibility so revoked access is not restored through an old notice.

`/app/notifications/preferences` provides six native checkboxes and works without JavaScript. Preferences control email, not in-app notices. Missing checkbox fields mean off; duplicate, unknown or non-checkbox values are refused. Only the following keys are accepted:

| Key | Event | Initial email setting |
| --- | --- | --- |
| `security_first_login` | The account was created | On |
| `introduction_request` | An incoming request awaits the owner | Off |
| `introduction_accepted` | The recipient accepted the owner's request | Off |
| `new_message` | Another participant sent a message | Off |
| `checkin_missed` | An explicitly chosen report deadline is overdue | Off |
| `weekly_summary` | A weekly summary of actual account activity | Off |

Account and interaction events are recorded in the same transaction as the action that caused them. Stable event keys suppress duplicates on retries. First-sign-in is not every subsequent login. An introduction notice never accepts a request, opens a conversation by itself or exposes a peer's Google identity. Message notices require a still-open, authorized conversation. Pending-request notices require the exact currently valid publication pair; accepted-context notices follow the accepted interaction. Expiry, withdrawal, block, suspension and deletion make unavailable notices disappear from the active list and count. Their cancelled metadata may remain in the owner's export until deletion.

A missed-check-in notice uses the existing explicit cadence and stored deadline plus six hours of grace. Paused intake or a new report invalidates an obsolete missed notice. No deadline is inferred from key creation or silence.

Weekly scheduling begins once at account creation or, for an older account without a schedule, on its first maintenance tick. It runs for all active accounts independently of the email preference. Toggling or unsubscribing does not reset the schedule, replay old activity or send a catch-up email. A delayed worker produces one current summary and sets the next date seven days later rather than replaying every missed week. Each summary counts reports received, currently available introduction updates and messages received in available conversations within `(event time − 7 days, event time]`. Its period stays fixed; later reports do not inflate an old summary, while later loss of authority can reduce eligible interaction counts. The same plain-text summary appears in the in-app row and eligible email.

## Web authority and routes

| Route | Authority and effect |
| --- | --- |
| `GET /app/notifications?page=N` | Current cookie session; list only |
| `POST /app/notifications/<id>/read` | Current owner, CSRF and Origin; idempotent read mark |
| `GET /app/notifications/preferences` | Current cookie session; show six choices |
| `POST /app/notifications/preferences` | Current owner, CSRF and Origin; replace the six choices |
| `GET` or `HEAD /email/unsubscribe/<token>` | Signed capability; confirmation only, no mutation |
| `POST /email/unsubscribe/<token>` | Signed capability and exact one-click marker; stop one email type |

No report key authorizes notification choices. Account owner selectors are never accepted from query/form data. Notification links are generated first-party account paths, filtered again at rendering, and grant no access by themselves. Titles and summaries are escaped. Valid account activity refreshes the session within the existing thirty-day idle and ninety-day absolute limits; rejected forms and invalid queries do not extend it. Account notification pages return 503 while sign-in is disabled.

Unsubscribe is the sole narrow capability exception to cookie/CSRF authorization. It does not authenticate a person or reveal an email, account ID or preference history. The token contains an opaque random subscription handle, one allowlisted type and a purpose-bound HMAC. It cannot enable email or affect a different type. There is no time expiry; account deletion or a signing-secret change invalidates the link. Re-enabling a type does not rotate its old link, so anyone retaining that link can disable it again.

The POST body must contain exactly `List-Unsubscribe=One-Click`. Both URL-encoded and single-part `multipart/form-data` encodings are accepted with a 1 KiB limit. Extra or duplicate fields, files, nested/ambiguous MIME, unsupported content types and query parameters are refused. Valid POST is idempotent and returns 200 without a redirect or session cookie. Malformed or invalid capabilities return a generic 400. No cookie or Origin header is required for a provider callback; an explicitly foreign Origin is still rejected by the global boundary. GET/HEAD scanners cannot unsubscribe. These choices implement the receiver encodings and sender behavior in [RFC 8058 sections 3–4](https://www.rfc-editor.org/rfc/rfc8058.html#section-3.1). Actual received mail must also prove the required signed headers; see the setup guide.

## Email contents and delivery states

Mail is plain text with a fixed event title, a first-party account link, a signed type-specific unsubscribe link and, only for weekly summaries, the bounded aggregate counts. It contains no peer name, posting, introduction note/reason or message text. The application emits `List-Unsubscribe`, `List-Unsubscribe-Post`, stable `Message-ID` and `Date` headers. It emits no tracking pixel or tracking redirect. A provider may still transform or retain mail; that must be checked against a received message and the actual provider settings.

`EMAIL_MODE=outbox` stores test payloads and never invokes a transport. It is the staging/development default. Legacy `recorded` and outbox-mode records are not replayed automatically when SMTP is later enabled. Optional email opt-in applies to future eligible events, not a replay of past notices. Turning a type off cancels pending/outbox mail for it and clears outstanding message batching when applicable; the in-app history and weekly schedule remain.

New-message mail waits one hour from the first pending message and groups subsequent messages in that conversation. Queue and attempt times enforce at most one attempt per owner/conversation per hour, including retries. Other enabled event types can queue immediately. Before every hand-off, the service rechecks the current account email, preference, active owner/peer and source authority. Mail already handed to a relay cannot be recalled if a choice changes during transmission.

| Stored state | Meaning |
| --- | --- |
| `recorded` | Historical outbox record, not a sending job |
| `outbox` | Locally stored payload; no transport opened |
| `pending` | Eligible SMTP job awaiting an attempt or retry |
| `sending` | Atomically claimed with a fifteen-minute lease |
| `accepted` | Relay acknowledged the message; not proof of inbox receipt or reading |
| `failed` | Permanent refusal or exhausted retry budget |
| `uncertain` | Acceptance may have happened; never automatically resend |
| `cancelled` | Preference or source authority no longer permits sending |

SMTP requires production configuration, valid sender/host/credentials and verified STARTTLS. Credentials are not sent before TLS succeeds. Each socket has a ten-second timeout. Temporary explicit failures retry at increasing delays of five, ten, twenty and forty minutes, with a maximum of five total attempts; the message batching window can delay attempts further. Permanent refusals fail without retry. A disconnect after DATA begins, a lost lease result or an interrupted lease becomes uncertain instead of risking a duplicate. Transport outcomes retain only bounded safe status codes, never raw provider responses or exception text. Network calls occur outside SQLite transactions; transactional claims prevent simultaneous workers sending the same job.

## Worker and deployment

`python -m connectthebots_server.notifications tick --limit 20` runs one maintenance/delivery pass. The service default limit is fifty, accepted range 1–100; the limit bounds SMTP claims per pass. Maintenance also initializes weekly schedules, creates due summaries/missed-check-in notices, groups messages and prunes stale events. The CLI requires a current migrated database and prints only aggregate counters. It does not migrate a database or expose an HTTP worker endpoint. Injected test transports are forbidden in production.

Deployment installs `connectthebots-notifications.service` and its timer alongside the app. The oneshot worker runs as the no-login `ctb` user from the immutable installed environment, reads the root-owned environment file and can write only the application data directory. The timer starts about two minutes after boot and runs about one minute after the preceding invocation ends, with small scheduling jitter. The service has a ten-minute time limit. Accounts can remain disabled and outbox mode can remain active while the timer is tested safely.

## Storage, export and deletion

Migration `0007_notifications.sql` adds six preferences and schedule, subscription handles, event/read/cancellation metadata, conversation batching windows and delivery states/attempt/lease metadata. SQLite remains plaintext within OS permissions. Queued payloads contain private account/unsubscribe links and must not appear in logs or browser evidence. Dynamic unsubscribe access/error logs are suppressed; path-category logging does not retain tokens.

Owner export includes preferences and event/delivery metadata with acceptance and attempt times. It excludes subscription capabilities, raw mail payloads, lease IDs, internal actor/source identifiers and peer content. Marking read or stopping a type does not erase history. Account deletion removes preferences, own notifications, subscriptions, queue/delivery data and affected peer-facing shared-interaction notices in the account deletion transaction. Existing shared-thread erasure rules still apply. Provider-held mail, recipient copies and old backups are separate retention/recovery concerns.

## Verification and unfinished work

Local core/transport tests cover defaults, independent weekly scheduling, eligibility, suppression, duplicate/retry/concurrency, TLS, uncertainty and lifecycle. Web tests cover ownership, strict native forms, valid-session renewal, GET safety, malformed capabilities, both one-click encodings and actual weekly summary rendering. Desktop/mobile browser journeys use fictional OIDC accounts and no-JavaScript forms; outbox evidence records metadata only and proves zero transport calls. Enabled notification journeys passed 116 checks on each of Windows and Ubuntu. Current-schema regressions passed 74 account, 166 interaction, 120 board and 144 report checks. The reviewed package passed 43 installed Ubuntu checks, 54 hosted-browser checks and 93 static-site checks, including the real hardened worker/timer in outbox mode. The reviewed wheel matches all 72 server files and seven migrations. [STATUS](STATUS.md) records final suite totals, artifact paths and the release digest.

Before real email or public activation: complete provider activation and protected configuration; authenticate the sending domain; establish and verify incoming support mail; inspect received MIME, tracking, SPF/DKIM/DMARC and both signed unsubscribe headers; verify exactly one type is stopped; record an approved successful delivery and failure/retry case; confirm provider retention/locations/transfers and approve the exact legal pages. Operator tools, monitoring and deletion-aware recovery remain further launch gates. No provider account, DNS record or real email was changed by this milestone.
