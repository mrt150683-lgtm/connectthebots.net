CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL,
    google_sub TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL,
    suspended_at TEXT
);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE INDEX sessions_expiry ON sessions(expires_at);

CREATE TABLE oauth_attempts (
    state_hash TEXT PRIMARY KEY NOT NULL,
    binding_hash TEXT NOT NULL,
    nonce_hash TEXT NOT NULL,
    verifier TEXT NOT NULL,
    invite_hash TEXT,
    expires_at TEXT NOT NULL
);
CREATE INDEX oauth_attempts_expiry ON oauth_attempts(expires_at);

CREATE TABLE invites (
    code_hash TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT
);

CREATE TABLE waitlist (
    id TEXT PRIMARY KEY NOT NULL,
    email TEXT UNIQUE NOT NULL,
    consent_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    removal_hash TEXT UNIQUE NOT NULL
);

-- A recorded event is not an email delivery. The notification milestone owns
-- provider delivery, retry policy, unsubscribe headers and send receipts.
CREATE TABLE email_outbox (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_at TEXT
);
