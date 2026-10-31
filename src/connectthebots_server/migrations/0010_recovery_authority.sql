-- Separate from erasure_generation: this guards loss of the current host too.
-- Triggers flag changes locally; Database.transaction advances remote authority
-- before committing them once this database has explicitly been enrolled.
CREATE TABLE recovery_authority (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    enrolled INTEGER NOT NULL DEFAULT 0 CHECK(enrolled IN (0,1)),
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0),
    marker TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0)
);
INSERT INTO recovery_authority(singleton) VALUES(1);
CREATE TRIGGER recovery_user_insert AFTER INSERT ON users BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_user_delete AFTER DELETE ON users BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_user_update AFTER UPDATE OF id,google_sub,email,display_name,created_at,suspended_at ON users
WHEN OLD.id IS NOT NEW.id OR OLD.google_sub IS NOT NEW.google_sub OR OLD.email IS NOT NEW.email
 OR OLD.display_name IS NOT NEW.display_name OR OLD.created_at IS NOT NEW.created_at OR OLD.suspended_at IS NOT NEW.suspended_at BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_block_insert AFTER INSERT ON user_blocks BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_block_delete AFTER DELETE ON user_blocks BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_block_update AFTER UPDATE ON user_blocks BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_waitlist_delete AFTER DELETE ON waitlist BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_erasure_update AFTER UPDATE OF erasure_generation ON recovery_state
WHEN OLD.erasure_generation IS NOT NEW.erasure_generation BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_release_update AFTER UPDATE OF recovery_hold ON recovery_state
WHEN OLD.recovery_hold=1 AND NEW.recovery_hold=0 BEGIN
    UPDATE recovery_authority SET revision=revision+1 WHERE singleton=1;
END;
