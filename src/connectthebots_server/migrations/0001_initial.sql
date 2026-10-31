-- Migration history is part of the same transaction as every schema change.
-- Feature tables arrive with the feature that owns and tests their lifecycle.
CREATE TABLE schema_migrations (
    name TEXT PRIMARY KEY NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    applied_at TEXT NOT NULL
);
