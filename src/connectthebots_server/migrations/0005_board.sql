CREATE TABLE board_postings (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT UNIQUE NOT NULL,
    draft_revision INTEGER NOT NULL CHECK(draft_revision > 0),
    active_grant_id TEXT,
    ever_published INTEGER NOT NULL DEFAULT 0 CHECK(ever_published IN (0,1)),
    hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)),
    updated_at TEXT
);

CREATE TABLE board_revisions (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision > 0),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    search_text TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY(user_id, revision)
);

CREATE TABLE board_publication_grants (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(user_id, revision) REFERENCES board_revisions(user_id, revision) ON DELETE CASCADE
);
CREATE INDEX board_grants_owner ON board_publication_grants(user_id, approved_at);

-- The migration's recorded applied_at supplies the initial timestamp. There is
-- no wall-clock SQL default and no content or owner identifier in global state.
CREATE TABLE board_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    updated_at TEXT
);
INSERT INTO board_state(id) VALUES(1);
