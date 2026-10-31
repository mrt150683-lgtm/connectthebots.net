-- An identity-free monotonic erasure watermark. Only the trusted live database
-- is authority for restore eligibility; a watermark copied with a backup is not.
CREATE TABLE recovery_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    lineage_id TEXT NOT NULL,
    erasure_generation INTEGER NOT NULL CHECK(erasure_generation>=0),
    recovery_hold INTEGER NOT NULL CHECK(recovery_hold IN (0,1)),
    restore_secret_hash TEXT
);
INSERT INTO recovery_state VALUES(1,lower(hex(randomblob(16))),0,0,NULL);
