-- Referral campaign: public places and standing share permission.
-- A share_approvals row with a place is a referral posted in one public forum,
-- group or thread. via says whether the person approved that exact post
-- ('post') or it fell inside their standing permission ('standing').
ALTER TABLE share_approvals ADD COLUMN place TEXT;
ALTER TABLE share_approvals ADD COLUMN via TEXT CHECK(via IS NULL OR via IN ('post','standing'));
CREATE INDEX share_approvals_owner_place ON share_approvals(user_id, place, created_at);

-- The person's one standing permission: the agent may post their referral in
-- these places (https URLs, at most five) up to weekly_limit times a week
-- without asking each time. Removing it stops automatic sharing at once.
CREATE TABLE share_permissions (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    places_json TEXT NOT NULL,
    weekly_limit INTEGER NOT NULL CHECK(weekly_limit BETWEEN 1 AND 7),
    approved_at TEXT NOT NULL
);

-- Changed or removed permissions invalidate older backups, so a restore can
-- never revive a permission the person withdrew.
CREATE TRIGGER recovery_share_permissions_update AFTER UPDATE ON share_permissions
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.places_json IS NOT NEW.places_json OR OLD.weekly_limit IS NOT NEW.weekly_limit BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_share_permissions_delete AFTER DELETE ON share_permissions BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
