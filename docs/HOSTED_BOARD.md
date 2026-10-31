# Public board and exact publication

30 October 2026. This records the milestone-4 board contract; the later [interaction candidate](HOSTED_INTERACTIONS.md) adds introduction choices and signed-in block filtering. Production is unchanged. The public board shares the default-disabled account gate; when enabled it is readable without signing in. Release checks and remaining launch gates are recorded in [HOSTED_V1_PROGRESS.md](HOSTED_V1_PROGRESS.md).

## What can be published

Each owner has one posting and a saved draft. Its entire publishable payload is exactly `text`, `tags`, `looking_for` and `offering`. The text is three to five sentences and at most 800 characters; looking-for/offering fields are each 1–160 characters. Up to five tags are allowed, each 1–30 characters. Tags preserve typed case and order but reject duplicates after Unicode normalization and case folding. Strings are trimmed, bounded and checked for unsupported controls and numeric score/percentage patterns. Sentence counting uses punctuation boundaries and common title-abbreviation exceptions; it is a writing constraint, not language understanding.

No Google name, email, subject, user ID, private brief or report content is inserted into a posting. If the owner types identifying information into the approved payload, it becomes public. All text is escaped and displayed as plain text, with no Markdown rendering or automatic link creation. Outbound URLs typed into prose stay text.

## Draft, approval and withdrawal

`GET /app/posting` shows the owner's saved draft and publication status. `POST /app/posting` saves a new private revision. Saving edits never modifies the current public version. Saved revisions remain for the owner's history and export until account deletion.

`POST /app/posting/preview` renders the exact current saved text, tags and both short fields. It does not publish. A signed token binds the owner, revision, payload hash, random nonce and ten-minute expiry. `POST /app/posting/publish` requires the token plus session CSRF and same-origin checks. Inside one transaction, the service checks the current owner, current draft revision and hash and creates the exact-payload grant. An old preview fails after any newer draft save, even if the words happen to be unchanged. Repeating a valid approval of the already-current version returns the same result without a new grant or changed publication time.

`POST /app/posting/unpublish` withdraws the active grant immediately and advances the draft revision so an old preview cannot resurrect the posting. Public list/search/JSON omit it, and an existing previously public permalink returns 410. Unknown or never-published slugs return 404. Republishing needs a fresh preview; the owner's slug remains stable. Hidden or suspended postings are excluded at read time. All owner mutations derive the owner from the session, reject extra owner selectors and require CSRF. Successful activity renews the session within its original 90-day absolute deadline; invalid forms and invalid public queries do not renew it.

## Public routes and JSON

- `/board?q=<search>&page=<page>`: plain HTML with a no-JavaScript search form and pagination.
- `/board.json`: the same search and page parameters, with a schema-versioned envelope.
- `/board/<slug>`: one public posting.
- `/board/<slug>.json`: the same explicit public fields as JSON.

Search is at most 100 characters and matches normalized, case-folded posting text, tags and the looking-for/offering fields. SQL wildcard characters in the query are literal. Page numbers are 1–10,000; web/API pages contain 20 items. Unknown or duplicate query parameters are rejected. Results are newest-approved first with a stable slug tie-break; editing a private draft does not move its public version. Count, rows and feed timestamp share a read transaction.

The feed contains `schema_version`, `updated_at`, `page`, `page_size`, `total`, `has_next` and `items`. Each item contains only `slug`, `text`, `tags`, `looking_for`, `offering`, `published_at` and `updated_at`. Timestamps use UTC RFC 3339; HTML displays readable UTC times. The initial empty feed timestamp comes from the recorded migration time, not from a page read; publication/withdrawal changes advance it. Public data is validated against [BOARD_SCHEMA.json](BOARD_SCHEMA.json) in the browser test suite.

Anonymous JSON routes share a per-IP token bucket: a burst of 120 requests, refilling at two per second. It stores only secret-keyed address hashes, caps active buckets at 4,096 and returns 429 with `Retry-After`. Valid signed-in sessions bypass this anonymous limit. The limiter is process-local and resets on restart; it is not distributed abuse protection. Dynamic request logging remains suppressed and responses are non-cacheable so withdrawal is immediately reflected in new requests. Copies someone already downloaded cannot be recalled.

## Remaining limits

This is a public opt-in directory, not matching, an invitation or acceptance. The later interaction pages let a person review exact sharing and ask for an introduction; a conversation opens only after the recipient separately agrees. Report keys do not publish postings; `/api/v1/me` only returns its owner's approved public posting if one is currently visible. Account deletion removes the owner's slug, revisions and grants; an old slug then returns 404, with no content/slug tombstone retained. Explicit unpublish while the account exists returns 410 and retains private owner history. Operational moderation, notification delivery, real Google staging, approved legal pages, deletion-aware backups and an explicitly approved production rollout remain separate work. Current introduction/message lifecycle handling is described in the interaction contract.
