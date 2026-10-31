CREATE TABLE introductions (
    id TEXT PRIMARY KEY NOT NULL,
    requester_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    recipient_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    requester_grant_id TEXT REFERENCES board_publication_grants(id) ON DELETE SET NULL,
    recipient_grant_id TEXT REFERENCES board_publication_grants(id) ON DELETE SET NULL,
    requester_snapshot_json TEXT,
    recipient_snapshot_json TEXT,
    note TEXT,
    reason TEXT,
    context_hash TEXT,
    status TEXT NOT NULL CHECK(status IN ('requested','accepted','declined','expired','withdrawn','blocked')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    requester_accepted_at TEXT,
    recipient_accepted_at TEXT,
    resolved_at TEXT,
    blocked_by_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    CHECK(requester_id IS NULL OR recipient_id IS NULL OR requester_id != recipient_id)
);
CREATE INDEX introductions_requester_time ON introductions(requester_id,created_at);
CREATE INDEX introductions_recipient_state ON introductions(recipient_id,status,created_at);
CREATE UNIQUE INDEX introductions_pending_pair ON introductions(
    min(requester_id,recipient_id),max(requester_id,recipient_id)
) WHERE status='requested';

CREATE TABLE intro_receipts (
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nonce_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    introduction_id TEXT NOT NULL REFERENCES introductions(id) ON DELETE CASCADE,
    PRIMARY KEY(owner_id,nonce_hash)
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY NOT NULL,
    introduction_id TEXT UNIQUE NOT NULL REFERENCES introductions(id) ON DELETE CASCADE,
    requester_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    recipient_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    closed_reason TEXT,
    CHECK(requester_id IS NULL OR recipient_id IS NULL OR requester_id != recipient_id)
);
CREATE UNIQUE INDEX conversations_open_pair ON conversations(
    min(requester_id,recipient_id),max(requester_id,recipient_id)
) WHERE closed_at IS NULL;
CREATE TRIGGER conversation_requires_mutual_acceptance BEFORE INSERT ON conversations
WHEN NOT EXISTS (
    SELECT 1 FROM introductions i WHERE i.id=NEW.introduction_id AND i.status='accepted'
    AND i.requester_id=NEW.requester_id AND i.recipient_id=NEW.recipient_id
    AND i.requester_accepted_at IS NOT NULL AND i.recipient_accepted_at IS NOT NULL
    AND i.context_hash IS NOT NULL
) BEGIN SELECT RAISE(ABORT,'Mutual acceptance is required'); END;

CREATE TABLE messages (
    id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX messages_conversation_time ON messages(conversation_id,created_at);
CREATE INDEX messages_sender_time ON messages(sender_id,created_at);
CREATE TABLE message_receipts (
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nonce_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    PRIMARY KEY(owner_id,nonce_hash)
);

CREATE TABLE user_blocks (
    blocker_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(blocker_id,blocked_id),
    CHECK(blocker_id != blocked_id)
);
CREATE TABLE board_view_state (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    updated_at TEXT NOT NULL
);
