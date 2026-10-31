ALTER TABLE invites ADD COLUMN id TEXT;
UPDATE invites SET id=lower(hex(randomblob(16))) WHERE id IS NULL;
CREATE UNIQUE INDEX invites_id ON invites(id);

CREATE TABLE abuse_reports (
    id TEXT PRIMARY KEY NOT NULL,
    reporter_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('posting','conversation')),
    target_id TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN ('spam','harassment','impersonation','privacy','unsafe_content','other')),
    details TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(reporter_id<>subject_user_id)
);
CREATE INDEX abuse_reports_owner_time ON abuse_reports(reporter_id,created_at);
CREATE INDEX abuse_reports_subject ON abuse_reports(subject_user_id);
CREATE TABLE moderation_receipts (
    reporter_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nonce_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    report_id TEXT NOT NULL REFERENCES abuse_reports(id) ON DELETE CASCADE,
    PRIMARY KEY(reporter_id,nonce_hash)
);
CREATE TABLE operator_subscriptions (
    kind TEXT PRIMARY KEY NOT NULL CHECK(kind IN ('abuse_report','certificate_expiry')),
    id TEXT UNIQUE NOT NULL,
    recipient_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE operator_alerts (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('abuse_report','certificate_expiry')),
    report_id TEXT REFERENCES abuse_reports(id) ON DELETE CASCADE,
    dedupe_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    certificate_expires_at TEXT,
    CHECK((kind='abuse_report' AND report_id IS NOT NULL AND certificate_expires_at IS NULL)
       OR (kind='certificate_expiry' AND report_id IS NULL AND certificate_expires_at IS NOT NULL))
);
CREATE TABLE operator_certificate_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    fingerprint TEXT,
    expires_at TEXT,
    checked_at TEXT NOT NULL,
    verified INTEGER NOT NULL CHECK(verified IN (0,1))
);
ALTER TABLE email_outbox ADD COLUMN operator_alert_id TEXT REFERENCES operator_alerts(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX email_outbox_operator_alert ON email_outbox(operator_alert_id) WHERE operator_alert_id IS NOT NULL;
