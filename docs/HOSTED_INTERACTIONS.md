# Introductions and conversations

30 October 2026. This describes the milestone-5 local candidate. Production is unchanged and account access remains disabled by default. The application contains human-approved introduction requests and two-person conversations; real Google staging, approved trust pages, email delivery and an approved rollout remain separate gates. See [current progress](HOSTED_V1_PROGRESS.md) for release verification.

## Exact request and two choices

An owner needs a currently approved public posting. From a board posting or a connection report's `board_slug`, **Ask for an introduction** opens a form for a note of 1–600 characters and a reason of 1–300 characters. Both are plain text. The preview contains the exact requester and recipient posting versions, note and reason. Previewing performs no introduction or conversation write.

A stateless HMAC form token binds the owner, purpose, complete canonical preview, random nonce and ten-minute expiry. Sending requires the current cookie session, CSRF and same-origin checks. In one transaction, the service rechecks both owners, both current publication grants, blocking, the exact preview and quotas. It stores the two snapshots, note, reason and context hash and records the requester's choice. Repeating the same nonce and exact payload returns the original request; changing its payload is a conflict. Saving a private draft does not change an existing approval; replacing or withdrawing either public version invalidates an unaccepted request.

The requester sees **Waiting for the posting’s author to decide.** The recipient sees the approved context and **Yes**, **Not now** and **Block**. Yes submits a separate ten-minute token bound to the recipient, introduction ID and context hash. The core checks the current request again, records the recipient's choice and creates one conversation atomically. A database trigger also rejects a conversation without both acceptance timestamps. Repeating an accepted choice returns the same conversation. No bearer report key authorizes these human choices.

Requests have these states:

| State | Meaning |
| --- | --- |
| `requested` | Requester approved; recipient has not accepted; no conversation |
| `accepted` | Recipient approved the same context; one conversation exists |
| `declined` | Recipient chose Not now |
| `withdrawn` | Requester withdrew, or a required posting/owner/context became unavailable before acceptance |
| `expired` | The 14-day deadline passed before acceptance |
| `blocked` | Further contact is blocked; the other party receives only `unavailable` |

At most five outgoing requests may be pending. Each person may send twenty new requests in any rolling seven days. Request/accept/decline/withdraw/block checks are owner-bound and transactional; invalid transitions are refused. The milestone-5 release used the recipient's dashboard and introduction list as its delivery surface. The later [notification milestone](HOSTED_NOTIFICATIONS.md) adds the bell and controlled optional email; real provider delivery remains unverified.

## Private messages and closure

A conversation is accessible only to its two participants and authorized operators. The UI calls them **You** and **The other person**; it never inserts the peer's Google name, email or account identifier. Explicitly shared posting text, notes and message text can still contain information that the person chose to type.

Messages contain 1–4,000 characters and preserve valid submitted text. Native web-form line endings use LF internally. Content is escaped, without HTML or Markdown rendering, attachments or automatic user-link conversion. Numeric score/percentage patterns and unsupported controls are rejected in request notes, reasons and messages. There are no server-side model calls, automatic replies or report-key message reads/writes.

The message form uses a ten-minute owner/purpose/conversation-bound token with a random nonce. The service stores the nonce hash and exact conversation/text hash transactionally. Exact retries return the original message; a changed body using the same nonce fails. Each person may send one hundred new messages in any rolling 24 hours across conversations; quotas return 429 and `Retry-After`. Failed submissions and replay do not consume new-message quota.

Either person can leave, closing the conversation for both. Closed conversations remain readable but reject new messages, including forms opened before closure. Blocking also closes existing contact and refuses later requests. It is silent to the other person. There is no reopen or unblock control in this milestone.

## Board filtering and navigation

For a valid signed-in session, board list, search, HTML detail and JSON detail omit postings blocked in either direction. The viewer comes only from the server-validated cookie, never a query/form owner selector. A blocked posting's permalink returns a generic 404. The affected owners' feed timestamps advance without revealing a block reason. Anonymous public access remains available; blocking cannot make an intentionally public posting private or recall downloaded copies.

Introduction lists paginate the incoming, outgoing and history sections, with independent totals. Conversation lists paginate too. Pages contain twenty items per section, with page numbers 1–10,000. Conversation messages show the latest fifty in chronological order; an older-page cursor must belong to the same conversation. All navigation and decisions work without JavaScript. Successful activity slides the session to at most thirty days from use, capped by the original ninety-day absolute deadline; failed forms and invalid queries do not extend it.

## Routes

| Route | Purpose |
| --- | --- |
| `GET /app/introductions` | Incoming/outgoing/history with `page` |
| `GET /app/introductions/new?slug=…` | Write a note and reason for a current public posting |
| `POST /app/introductions/preview` | Validate and render exact sharing, without persistence |
| `POST /app/introductions` | Confirm the signed request preview |
| `GET /app/introductions/{id}` | Participant-only context and available choices |
| `POST /app/introductions/{id}/{accept,decline,withdraw,block}` | Explicit participant decision |
| `GET /app/conversations` | Participant-only list with `page` |
| `GET /app/conversations/{id}` | Thread, optionally with a same-thread `before` cursor |
| `POST /app/conversations/{id}/messages` | Explicitly send plain text |
| `POST /app/conversations/{id}/{close,block}` | Stop further messages/contact |

## Data and limits

Migration `0006_interactions.sql` stores introduction snapshots and choices, message/conversation records, hashed retry receipts, account-to-account blocks and per-viewer board freshness. Introduction history remains stored until either participant deletes their account; ordinary terminal request views stop showing shared context. Account exports include introduction/conversation records, the owner's retained submitted context, their own message bodies and their own block decisions. Peer message bodies and secret credentials are excluded.

Account deletion clears both shared posting snapshots, note and reason in introductions involving the person, removes their participant identifiers and block records, closes conversations and replaces their messages with `[deleted]`. The remaining participant keeps their own messages and neutral closed-thread metadata. When both accounts are gone, the shared introduction/thread rows are removed. A separate date-only deletion receipt remains. Anonymous waitlist ownership still uses its independent removal code. Backups and copies saved outside the service require separate handling; no privacy-safe recovery promise is made.

The unapproved [privacy draft](PRIVACY_DRAFT.md) and [terms draft](TERMS_DRAFT.md) must be reviewed against the actual release. The source tests and local synthetic identity-provider browser runs do not prove real Google login, operational moderation, SMTP delivery, backup safety or production readiness.

## Verified release candidate

The Windows server suite ran 253 tests: 252 passed and one Linux-only test skipped. The no-extra path passed 131 with 122 explicit skips. This includes eighteen interaction web tests and twenty-two account lifecycle tests. Frozen R0 manifest, canonical-LF and semantic checks passed; no frozen source bytes were changed. The enabled desktop/mobile journey passed 166 checks on each of Windows and Linux, in `artifacts/interactions-interactions-final/` and `artifacts/interactions-browser-linux-synchronized/`. Board regression passed 120 checks in `artifacts/interactions-board-final/`, reports passed 144 in `artifacts/interactions-reports-final/`, and accounts passed 74 in `artifacts/interactions-auth-regression/`. These enabled synthetic-provider checks are separate from the installed default-disabled checks.

The reviewed wheel SHA-256 is `de0ad6810de2b1a6429c792e13badc23b5d6208deb2f7e2dcf77b8b5ba34e785`. All 63 server files match the source byte for byte, and all six migrations are packaged. Ubuntu staging installed release `0f07ea5b4d51a035d7050812`, with 37 installed checks, 46 hosted-browser checks and 93 static-site checks passed. Evidence is under `artifacts/interactions-reviewed-wheel-parity.json` and `artifacts/interactions-staging/`. This local fixture uses disabled accounts, closed signups, outbox-only email and HTTP localhost; it makes no production or real-provider claim.
