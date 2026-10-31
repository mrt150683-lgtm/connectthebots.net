CREATE TABLE notification_preferences (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    security_first_login INTEGER NOT NULL DEFAULT 1 CHECK(security_first_login IN (0,1)),
    introduction_request INTEGER NOT NULL DEFAULT 0 CHECK(introduction_request IN (0,1)),
    introduction_accepted INTEGER NOT NULL DEFAULT 0 CHECK(introduction_accepted IN (0,1)),
    new_message INTEGER NOT NULL DEFAULT 0 CHECK(new_message IN (0,1)),
    checkin_missed INTEGER NOT NULL DEFAULT 0 CHECK(checkin_missed IN (0,1)),
    weekly_summary INTEGER NOT NULL DEFAULT 0 CHECK(weekly_summary IN (0,1)),
    weekly_next_at TEXT
);
CREATE TABLE notification_subscriptions (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    UNIQUE(user_id,kind)
);
CREATE TABLE notification_events (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('security_first_login','introduction_request','introduction_accepted','new_message','checkin_missed','weekly_summary')),
    source_id TEXT,
    actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    cancelled_at TEXT,
    UNIQUE(user_id,dedupe_key)
);
CREATE INDEX notification_events_owner_time ON notification_events(user_id,created_at);
CREATE INDEX notification_events_unread ON notification_events(user_id,read_at,cancelled_at);
CREATE TABLE notification_maintenance (
    name TEXT PRIMARY KEY NOT NULL,
    last_rowid INTEGER NOT NULL
);
CREATE TABLE notification_mail_windows (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    pending_since TEXT,
    latest_event_id TEXT REFERENCES notification_events(id) ON DELETE SET NULL,
    last_queued_at TEXT,
    last_attempt_at TEXT,
    PRIMARY KEY(user_id,conversation_id)
);
-- Old rows are historical records, not a queue to send on first activation.
ALTER TABLE email_outbox ADD COLUMN event_id TEXT REFERENCES notification_events(id) ON DELETE CASCADE;
ALTER TABLE email_outbox ADD COLUMN state TEXT NOT NULL DEFAULT 'recorded' CHECK(state IN ('recorded','outbox','pending','sending','accepted','failed','uncertain','cancelled'));
ALTER TABLE email_outbox ADD COLUMN available_at TEXT;
ALTER TABLE email_outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE email_outbox ADD COLUMN last_attempt_at TEXT;
ALTER TABLE email_outbox ADD COLUMN last_error_code TEXT;
ALTER TABLE email_outbox ADD COLUMN lease_id TEXT;
ALTER TABLE email_outbox ADD COLUMN lease_until TEXT;
CREATE UNIQUE INDEX email_outbox_event ON email_outbox(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX email_outbox_due ON email_outbox(state,available_at);
