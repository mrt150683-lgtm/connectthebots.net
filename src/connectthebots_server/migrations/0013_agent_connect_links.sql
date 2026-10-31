-- One-time connect links: the signed-in person hands their agent a single URL.
-- The agent claims it once with POST /api/v1/agent/connect and receives a fresh
-- agent key. Only a SHA-256 hash of the link code is stored. Unclaimed links
-- expire after an hour; links never grant anything on their own.
CREATE TABLE agent_connect_links (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT UNIQUE NOT NULL CHECK(length(code_hash)=64),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    revoked_at TEXT,
    key_id TEXT REFERENCES report_keys(id) ON DELETE SET NULL
);
CREATE INDEX agent_connect_links_user ON agent_connect_links(user_id, created_at);
