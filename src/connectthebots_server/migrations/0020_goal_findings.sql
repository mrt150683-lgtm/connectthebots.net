-- M3 keeps private review independence and task attribution outside the hash
-- envelope. Ordinary findings/reviews and metadata inserts need no recovery
-- advance. Account unlinking is protected and never changes captured checks.
CREATE TABLE goal_findings (
    entry_id TEXT PRIMARY KEY NOT NULL REFERENCES goal_entries(id),
    task_id TEXT NOT NULL REFERENCES goal_tasks(id)
);
CREATE INDEX goal_findings_task ON goal_findings(task_id,entry_id);
CREATE TABLE goal_review_checks (
    entry_id TEXT PRIMARY KEY NOT NULL REFERENCES goal_entries(id),
    finding_id TEXT NOT NULL REFERENCES goal_entries(id),
    reviewer_handle TEXT NOT NULL,
    reviewer_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    independent INTEGER NOT NULL CHECK(independent IN (0,1)),
    UNIQUE(finding_id,reviewer_handle)
);
CREATE INDEX goal_review_checks_finding ON goal_review_checks(finding_id,independent);
CREATE INDEX goal_review_checks_owner ON goal_review_checks(reviewer_user_id);
CREATE TRIGGER goal_findings_immutable BEFORE UPDATE ON goal_findings
BEGIN SELECT RAISE(ABORT,'Finding task attribution is immutable'); END;
CREATE TRIGGER goal_findings_append_only BEFORE DELETE ON goal_findings
BEGIN SELECT RAISE(ABORT,'Finding task attribution cannot be deleted'); END;
CREATE TRIGGER goal_review_checks_immutable BEFORE UPDATE ON goal_review_checks
WHEN OLD.entry_id IS NOT NEW.entry_id OR OLD.finding_id IS NOT NEW.finding_id
 OR OLD.reviewer_handle IS NOT NEW.reviewer_handle OR OLD.independent IS NOT NEW.independent
 OR (OLD.reviewer_user_id IS NOT NEW.reviewer_user_id AND NEW.reviewer_user_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'Review independence is immutable'); END;
CREATE TRIGGER goal_review_checks_append_only BEFORE DELETE ON goal_review_checks
BEGIN SELECT RAISE(ABORT,'Review verification metadata cannot be deleted'); END;
CREATE TRIGGER recovery_goal_review_checks_unlink AFTER UPDATE ON goal_review_checks
WHEN OLD.reviewer_user_id IS NOT NEW.reviewer_user_id
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
