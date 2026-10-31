-- Personality fingerprint: one owner-approved trait vector per member.
-- 192 lowercase hex digits (96 traits, ff = not assessed), its schema, the
-- owner's tier (share = used for matching, never = kept only for the owner)
-- and the consent statement version the owner approved. A changed statement
-- version means the stored fingerprint is no longer used until re-approved.
CREATE TABLE personality_fingerprints (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    schema TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK(length(fingerprint)=192 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    tier TEXT NOT NULL CHECK(tier IN ('share','never')),
    notice_version TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Changed values, tier or approval and deletion invalidate older backups, so a
-- restore can never bring back a fingerprint the owner changed or removed.
CREATE TRIGGER recovery_personality_update AFTER UPDATE ON personality_fingerprints
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.schema IS NOT NEW.schema OR OLD.fingerprint IS NOT NEW.fingerprint OR OLD.tier IS NOT NEW.tier OR OLD.notice_version IS NOT NEW.notice_version BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_personality_delete AFTER DELETE ON personality_fingerprints BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
