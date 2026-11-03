-- M4 keeps moderation decisions reversible without confusing a safety hold
-- with peer checking. Original hold reasons contain closed codes, not content
-- or reporter identities. New held-entry metadata is an ordinary insert;
-- changing or removing existing moderation authority is protected.
ALTER TABLE goal_reports ADD COLUMN reviewed_at TEXT;
CREATE TRIGGER recovery_goal_reports_review_update AFTER UPDATE ON goal_reports
WHEN OLD.reviewed_at IS NOT NEW.reviewed_at
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TABLE goal_entry_moderation (
    entry_id TEXT PRIMARY KEY NOT NULL REFERENCES goal_entries(id),
    hidden_from_status TEXT CHECK(hidden_from_status IS NULL OR hidden_from_status IN ('visible','pending_peer_check','held')),
    hold_reasons_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(hold_reasons_json) AND json_type(hold_reasons_json)='array')
);
CREATE TRIGGER goal_entry_moderation_identity BEFORE UPDATE ON goal_entry_moderation
WHEN OLD.entry_id IS NOT NEW.entry_id
BEGIN SELECT RAISE(ABORT,'Moderation entry identity cannot change'); END;
CREATE TRIGGER recovery_goal_entry_moderation_update AFTER UPDATE ON goal_entry_moderation
WHEN OLD.hidden_from_status IS NOT NEW.hidden_from_status OR OLD.hold_reasons_json IS NOT NEW.hold_reasons_json
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_entry_moderation_delete AFTER DELETE ON goal_entry_moderation
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
