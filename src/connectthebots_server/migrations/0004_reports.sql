CREATE TABLE report_keys (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash TEXT UNIQUE NOT NULL,
    last_four TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX report_keys_owner ON report_keys(user_id, revoked_at);

CREATE TABLE reports (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_id TEXT REFERENCES report_keys(id) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK(source IN ('key', 'paste')),
    kind TEXT NOT NULL CHECK(kind IN ('nothing', 'question', 'connection')),
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    handled_at TEXT
);
CREATE INDEX reports_owner_time ON reports(user_id, received_at DESC, id);
CREATE INDEX reports_owner_pending ON reports(user_id, handled_at, kind);
CREATE INDEX reports_key_time ON reports(key_id, received_at);
CREATE INDEX reports_paste_time ON reports(user_id, source, received_at);

CREATE TABLE report_receipts (
    scope TEXT NOT NULL,
    idempotency_hash TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_id TEXT REFERENCES report_keys(id) ON DELETE CASCADE,
    body_hash TEXT NOT NULL,
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    response_json TEXT NOT NULL,
    PRIMARY KEY(scope, idempotency_hash)
);

CREATE TABLE report_settings (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0, 1)),
    cadence_minutes INTEGER CHECK(cadence_minutes BETWEEN 60 AND 525600),
    next_due_at TEXT
);
