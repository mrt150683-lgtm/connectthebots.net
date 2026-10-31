# Hosted reports and dashboard

**Paste retry update, 30 October 2026:** authenticated report validation errors return the submitted text in the escaped editor and explain the actual error. Recovering a submission with stale CSRF requires an explicit matching Origin; unreadable forms, unauthenticated requests and cross-origin requests do not echo submitted text. A signed, owner-bound expired report preview can be reconstructed in the editor, but saving still requires a fresh preview, current CSRF and explicit confirmation. Altered or another owner's preview is never recovered. Paused intake and quota errors retain verified text without saving it; quota responses keep `Retry-After`. No server-side draft storage is added, and invalid forms do not refresh the session. Twenty focused HTTP tests pass, including six added cases; the following milestone counts are historical. The limited owner test is already live, but this correction is not yet deployed.

30 October 2026. This records the milestone-3 report contract and its verification. The later [board candidate](HOSTED_BOARD.md) extends publication and `/api/v1/me`; current work is tracked separately. Production is unchanged. Access shares the default-disabled account gate; real Google staging, approved trust pages and explicit deployment approval remain required. The later [interaction candidate](HOSTED_INTERACTIONS.md) adds introductions and conversations; the [notification candidate](HOSTED_NOTIFICATIONS.md) implements the worker, while real SMTP delivery remains unverified.

Verified candidate: 159 server tests (158 passed, one Linux-only skip); no-extra path 81 passed/78 skipped; 144 report browser checks on Windows and Linux, 74 account checks, 93 static checks and six prompt tests. The installed Ubuntu wheel passed 31 checks, plus 32 hosted-browser and 93 static checks. Its SHA-256 is `791715884b68c121cb1497eba153a2b687ece44e54b8e4acecd3c06d3646de0d`; release `e63db7ce8abd5ae1e3fa1f11` contains four migrations. All 40 current server files matched the wheel before installation. Evidence: `artifacts/reports-staging/`, `artifacts/reports-browser-final/`, `artifacts/reports-browser-linux/` and `artifacts/reports-reviewed-wheel-parity.json`. Enabled browser flows use a local test identity provider; installed staging keeps sign-in disabled. Neither demonstrates a real Google login or a public launch.

## Report keys and API

An authenticated owner can create up to three active named keys at `/app/keys`, rename them and revoke them. Keys are `ctb_` followed by 52 unpadded base32 characters encoding 32 random bytes. The server stores SHA-256 hashes, name, last four characters, creation/use/revocation times and owner only. Names are 1–80 characters. The full key appears only in its creation response and the private prompt/download derived from that response; it cannot be recovered later. A lost key should be revoked and replaced. Successful ingestion, replay and `/api/v1/me` update last-use time; rejected attempts do not.

`POST /api/v1/reports` requires exactly one `Authorization: Bearer <key>` header, one `Idempotency-Key` header and `Content-Type: application/json`. Session cookies alone do not authorize ingestion. The body is UTF-8 JSON of at most 32 KiB; duplicate/unknown fields, invalid encoding, non-finite numbers and wrong types are refused. The idempotency value is 1–128 ASCII letters, digits, `.`, `_`, `:` or `-`.

A successful request returns 201 with `report_id`, `received_at` and `status: "received"`. An authenticated retry with the same key, idempotency value and exact body bytes returns the same receipt with 201. Different bytes, even equivalent JSON formatting, produce 409. Keys have separate replay namespaces. Revoked keys and suspended accounts are checked before replay, so a stored receipt cannot bypass current authority. Authorization, replay, quotas and insertion share one SQLite transaction.

`GET /api/v1/me` uses only the bearer key and returns `owner.id`, `owner.display_name`, `posting` and `posting_available`. The board milestone now supplies only that owner's currently approved visible public posting; otherwise posting is null and availability is false. It returns no email, session material, full key, drafts or report contents. Report keys cannot publish a posting.

New reports are limited to 10 per rolling minute and 60 per rolling 24 hours per key. Confirmed pastes have the same limits per owner. Quotas are database-backed and survive restart; exact retries do not consume another quota slot. A 429 response includes `Retry-After`. Paused intake returns 409 for new reports and permits authenticated replay of an earlier receipt.

## Strict report schema

All reports require `kind`, `checked_at`, `places_checked` and `posts_read`. `kind` is exactly `nothing`, `question` or `connection`. Only the matching optional object is allowed: question details for `question`, connection details for `connection`, neither for `nothing`.

| Field | Bounds |
| --- | --- |
| `checked_at` | Valid RFC 3339 with seconds and timezone; at most six fractional digits; stored normalized to UTC |
| `places_checked` | 0–20 objects, each with required `name` (1–120 characters) and optional `url` |
| `posts_read` | Integer 0–1,000,000; booleans are rejected |
| `question.text`, `question.why` | Required strings, 1–1,000 and 1–2,000 characters respectively |
| `connection.who`, `connection.where` | Required name (1–200 characters) and HTTPS URL |
| `connection.why_now`, `they_bring`, `they_could_gain`, `i_bring`, `i_could_gain`, `still_unknown`, `first_step` | Each required, 1–2,000 characters |
| `connection.evidence` | 1–20 objects with required `quote_or_link` (1–2,000 characters) and `matches` (1–1,000 characters) |
| `connection.board_slug` | Optional 1–80 lowercase letters/digits/hyphens, starting and ending with a letter or digit; a reference only until the board exists |
| URLs | HTTPS only, at most 2,048 characters, with a hostname and without credentials, whitespace or backslashes; URL-shaped evidence is checked too |

Text must be nonblank and rejects unsupported controls, surrogate characters and invisible formatting controls. Numeric percentage/score patterns, including normalized Unicode equivalents, are refused with a clear 422 message. This is a bounded syntactic guard, not semantic interpretation of arbitrary prose. The complete canonical report must also fit 32 KiB. Report times and counts are the assistant's claims; they are not independently verified. Deadline calculations use server receipt time, never a submitted `checked_at`.

All stored content is untrusted. Templates escape text and do not render Markdown or HTML. HTTPS links carry `rel="nofollow noopener ugc"`; the server never fetches those links. Evidence is displayed as text. There are no scores or percentages in the dashboard UI.

## Paste, preview and confirmation

`/app/reports/paste` accepts the explicit v2 plain-text format. Each nonblank line has a case-sensitive label followed by `:` and a nonempty value. Required common labels are `Kind`, `Checked at`, `Places checked` and `Posts read`. Use `Places checked: none`, or repeat `Places checked: Name | https://...` lines. Question reports add `Question` and `Why`. Connection reports add `Connection: Name | https://...`, `Why now`, `They bring`, `They could gain`, `I bring`, `I could gain`, `Evidence: quote or link | reason`, `Still unknown` and `Small first step`; `Board slug` is optional. Only places and evidence may repeat. Unknown, duplicate or mismatched fields are refused. Older free-form reports must be converted explicitly; missing times, sources or counts are never invented.

Preview validates the same schema and shows escaped content. It writes no report, receipt or draft. A stateless HMAC token binds the canonical payload to the signed-in owner, purpose, random nonce and ten-minute expiry. Confirmation requires that token, the session's CSRF token and same-origin request checks. Only confirmation calls the transactional save operation. The nonce makes repeated confirmation idempotent. Altered, expired or other-owner previews fail without saving. Valid account activity still renews the existing session; “no report saved” does not mean session expiry is unchanged.

The v2 personalised prompt is generated only at key creation. It includes the exact HTTPS endpoint, key, JSON request and paste fallback. Copy is a local script enhancement; selecting text and downloading via a CSRF-protected signed form work without JavaScript. The download form expires after ten minutes, carries no key in a URL and stores no cleartext key on the server. Public prompt text has no key. External assistants control their own searches and schedules.

## Dashboard states and controls

The page order is needs your attention, what your agent is looking for, what it did, next check-in. The website does not hold the private brief or claim that an assistant is currently searching. Report history and pending review queues are queried separately, so a later quiet report does not dismiss an unresolved question or connection. The page shows up to 50 recent reports and 50 most recent pending reports with the total pending count; reviewing items exposes older pending ones. Account export contains the complete stored history.

| State, in precedence order | Actual condition and wording |
| --- | --- |
| Paused | Explicit website intake pause; tells the owner this does not stop their assistant |
| Connection or question | Newest unresolved connection/question; remains until the owner marks it reviewed |
| Missed (`failed` internally) | An explicitly selected cadence has a due time and the six-hour grace period has elapsed |
| Quiet | Latest report says `nothing`; the page attributes the statement to that report |
| Working | At least one report exists, no pending review and none of the above; no invented search activity |
| Setup | No report exists, including when a key has been created but never used; shows first-report instructions |

The owner chooses no schedule, daily or weekly in the UI. Enabling/changing the cadence or resuming anchors it to current time; a new receipt starts the next interval. Saving an unchanged active cadence does not silently postpone its due time. Setting a cadence does not schedule the assistant. Marking a report reviewed is owner-bound and does not reply, contact anyone or accept an introduction. Keys, pause/settings and review forms reject extra owner selectors and require CSRF. Valid page/form activity renews the 30-day session within its original 90-day absolute lifetime; invalid forms do not renew it.

## Data and remaining gates

Export includes the owner's key metadata, reports and dashboard settings, never full keys/hashes or authentication secrets. Account deletion cascades keys, reports, idempotency receipts and settings with existing account data; the date-only deletion receipt remains. Anonymous waitlist requests still use separate removal-code authority. Marking a report reviewed or revoking a key does not delete earlier reports.

SQLite is plaintext within OS permissions. Reports may contain information about other people; approval still needs a reviewed lawful basis, source/transparency handling, minimisation and retention for that information, as well as controller/provider/transfer details. The unapproved [privacy draft](PRIVACY_DRAFT.md) records these decisions. Backup retention and deletion-aware restoration remain unfinished. The later notification candidate can create missed-check-in and weekly notices; default outbox mode sends nothing. No production readiness, real Google success or complete hosted v1 is implied by local fixture tests.
