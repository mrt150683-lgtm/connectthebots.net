-- Existing growth data and permission changes invalidate older backups.
-- Creation alone cannot resurrect prior data. Routine timestamps, check-ins,
-- derived matches, receipts and access calculations do not advance authority.
-- Recovery itself clears sharing permissions and derived grants under its
-- private quarantine transaction; it never writes remote authority.

-- A local generation also protects an intact, unenrolled host. Only the
-- transaction/migration boundary advances it; private quarantine does not.
ALTER TABLE recovery_authority ADD COLUMN growth_revision INTEGER NOT NULL DEFAULT 0 CHECK(growth_revision>=0);
ALTER TABLE recovery_authority ADD COLUMN growth_generation INTEGER NOT NULL DEFAULT 0 CHECK(growth_generation>=0);

-- An approval must not become valid again after A -> B -> A or reinsertion.
ALTER TABLE taste_answers ADD COLUMN approval_nonce TEXT NOT NULL DEFAULT '';
UPDATE taste_answers SET approval_nonce=lower(hex(randomblob(16)));

-- Decisions are explicit owner attestations, or a recovery withdrawal. There
-- is no inferred grant for existing political answers. The latest id governs.
CREATE TABLE politics_consent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notice_version TEXT NOT NULL,
    choice TEXT NOT NULL CHECK(choice IN ('granted','withdrawn')),
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('agent_attestation','recovery'))
);
CREATE INDEX politics_consent_owner_history ON politics_consent_decisions(user_id,id);
CREATE TRIGGER recovery_politics_consent_insert AFTER INSERT ON politics_consent_decisions BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_politics_consent_update AFTER UPDATE ON politics_consent_decisions BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_politics_consent_delete AFTER DELETE ON politics_consent_decisions BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_member_profiles_update AFTER UPDATE ON member_profiles
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.public_id IS NOT NEW.public_id OR OLD.first_name IS NOT NEW.first_name OR OLD.age IS NOT NEW.age OR OLD.sex IS NOT NEW.sex OR OLD.show_name_on_leaderboards IS NOT NEW.show_name_on_leaderboards OR OLD.signature_opt_in IS NOT NEW.signature_opt_in OR OLD.signature_includes_code IS NOT NEW.signature_includes_code BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_member_profiles_delete AFTER DELETE ON member_profiles BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_taste_answers_update AFTER UPDATE ON taste_answers
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.topic IS NOT NEW.topic OR OLD.items_json IS NOT NEW.items_json OR OLD.tier IS NOT NEW.tier OR OLD.skipped IS NOT NEW.skipped OR OLD.approval_nonce IS NOT NEW.approval_nonce BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_taste_answers_delete AFTER DELETE ON taste_answers BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_taste_disclosures_update AFTER UPDATE ON taste_disclosures
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.other_user_id IS NOT NEW.other_user_id OR OLD.topic IS NOT NEW.topic OR OLD.granted_at IS NOT NEW.granted_at BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_taste_disclosures_delete AFTER DELETE ON taste_disclosures BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_referral_codes_update AFTER UPDATE ON referral_codes
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.code IS NOT NEW.code BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_referral_codes_delete AFTER DELETE ON referral_codes BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_referrals_update AFTER UPDATE ON referrals
WHEN OLD.id IS NOT NEW.id OR OLD.referrer_id IS NOT NEW.referrer_id OR OLD.referred_id IS NOT NEW.referred_id OR OLD.code IS NOT NEW.code OR OLD.entered_via IS NOT NEW.entered_via OR OLD.qualified_at IS NOT NEW.qualified_at OR OLD.flagged_at IS NOT NEW.flagged_at OR OLD.flag_reason IS NOT NEW.flag_reason OR OLD.cleared_at IS NOT NEW.cleared_at BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_referrals_delete AFTER DELETE ON referrals BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_share_approvals_update AFTER UPDATE ON share_approvals
WHEN OLD.id IS NOT NEW.id OR OLD.user_id IS NOT NEW.user_id OR OLD.kind IS NOT NEW.kind OR OLD.gap IS NOT NEW.gap BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_share_approvals_delete AFTER DELETE ON share_approvals BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_topic_boards_update AFTER UPDATE ON topic_boards
WHEN OLD.id IS NOT NEW.id OR OLD.slug IS NOT NEW.slug OR OLD.title IS NOT NEW.title OR OLD.description IS NOT NEW.description OR OLD.category IS NOT NEW.category OR OLD.kind IS NOT NEW.kind OR OLD.topic_key IS NOT NEW.topic_key OR OLD.origin IS NOT NEW.origin OR OLD.creator_user_id IS NOT NEW.creator_user_id OR OLD.archived_at IS NOT NEW.archived_at BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_topic_boards_delete AFTER DELETE ON topic_boards BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_board_posts_update AFTER UPDATE ON board_posts
WHEN OLD.id IS NOT NEW.id OR OLD.board_id IS NOT NEW.board_id OR OLD.user_id IS NOT NEW.user_id OR OLD.body IS NOT NEW.body OR OLD.tags_json IS NOT NEW.tags_json OR OLD.withdrawn_at IS NOT NEW.withdrawn_at OR OLD.hidden_at IS NOT NEW.hidden_at BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_board_posts_delete AFTER DELETE ON board_posts BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_board_replies_update AFTER UPDATE ON board_replies
WHEN OLD.id IS NOT NEW.id OR OLD.post_id IS NOT NEW.post_id OR OLD.user_id IS NOT NEW.user_id OR OLD.body IS NOT NEW.body OR OLD.withdrawn_at IS NOT NEW.withdrawn_at OR OLD.hidden_at IS NOT NEW.hidden_at BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_board_replies_delete AFTER DELETE ON board_replies BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_topic_signals_update AFTER UPDATE ON topic_signals
WHEN OLD.user_id IS NOT NEW.user_id OR OLD.topic_key IS NOT NEW.topic_key OR OLD.label IS NOT NEW.label BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_topic_signals_delete AFTER DELETE ON topic_signals BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_board_content_reports_update AFTER UPDATE ON board_content_reports
WHEN OLD.id IS NOT NEW.id OR OLD.reporter_id IS NOT NEW.reporter_id OR OLD.target_kind IS NOT NEW.target_kind OR OLD.target_id IS NOT NEW.target_id OR OLD.reason IS NOT NEW.reason OR OLD.details IS NOT NEW.details OR OLD.resolved_at IS NOT NEW.resolved_at BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_board_content_reports_delete AFTER DELETE ON board_content_reports BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

CREATE TRIGGER recovery_growth_config_update AFTER UPDATE ON growth_config
WHEN OLD.key IS NOT NEW.key OR OLD.value_json IS NOT NEW.value_json BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER recovery_growth_config_delete AFTER DELETE ON growth_config BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

-- Operator configuration can disable a feature; restoring an older value
-- must not undo that decision, including the first explicit override.
CREATE TRIGGER recovery_growth_config_insert AFTER INSERT ON growth_config BEGIN
    UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1;
END;

-- An earlier answer is not evidence of explicit processing consent. These
-- deletions run after the authority triggers exist; enrolled databases must
-- advance independent authority before this migration can commit.
-- Topic-only legacy disclosures did not bind the exact answer version.
DELETE FROM taste_disclosures;
DELETE FROM taste_answers WHERE topic='politics';

-- Keep duplicate-operation markers, but erase cached personal/peer payloads.
-- This one-time cleanup is protected without marking ordinary API receipts.
UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1 AND EXISTS (
    SELECT 1 FROM agent_receipts WHERE response_json!='{"replayed":true,"result_retained":false}'
);
UPDATE agent_receipts SET response_json='{"replayed":true,"result_retained":false}';
