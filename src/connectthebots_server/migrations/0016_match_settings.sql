-- Match tuning: one row per member who changed the defaults.
-- settings_json holds per-area strictness levels (0 off to 4 strict) and up to
-- twelve structured stated preferences. It changes only the owner's own match
-- list, except that strict areas and must preferences rule a match out in
-- both directions. Other members never see it. No row means the defaults.
CREATE TABLE match_settings (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL CHECK(json_valid(settings_json)),
    updated_at TEXT NOT NULL
);

-- Changed or removed settings invalidate older backups, so a restore can never
-- bring back preferences the owner changed or removed.
CREATE TRIGGER recovery_match_settings_update AFTER UPDATE ON match_settings
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.settings_json IS NOT NEW.settings_json BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_match_settings_delete AFTER DELETE ON match_settings BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
