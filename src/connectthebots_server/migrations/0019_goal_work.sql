-- Common goals M2: membership declarations and explicitly approved work leases.
-- Existing leads receive a pseudonymous membership, never work permission.
CREATE TABLE goal_members (
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','active','left','removed')),
    capabilities_json TEXT NOT NULL CHECK(json_valid(capabilities_json)),
    note TEXT,
    show_first_name INTEGER NOT NULL DEFAULT 0 CHECK(show_first_name IN (0,1)),
    joined_at TEXT NOT NULL,
    decided_at TEXT,
    charter_revision INTEGER NOT NULL CHECK(charter_revision>=1),
    work_generation INTEGER NOT NULL DEFAULT 0 CHECK(work_generation>=0),
    PRIMARY KEY(goal_id,user_id),
    UNIQUE(goal_id,handle)
);
CREATE INDEX goal_members_user_state ON goal_members(user_id,state);
INSERT INTO goal_members(goal_id,user_id,handle,state,capabilities_json,joined_at,charter_revision)
SELECT id,lead_user_id,lead_handle,'active','[]',created_at,charter_revision
FROM goals WHERE lead_user_id IS NOT NULL;

CREATE TABLE goal_allocations (
    goal_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    sessions_per_week INTEGER NOT NULL CHECK(sessions_per_week BETWEEN 1 AND 14),
    minutes_per_session INTEGER NOT NULL CHECK(minutes_per_session BETWEEN 5 AND 240),
    until TEXT NOT NULL,
    permission_expires_at TEXT NOT NULL,
    notice_version TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    approval_id TEXT NOT NULL UNIQUE,
    charter_revision INTEGER NOT NULL CHECK(charter_revision>=1),
    show_first_name INTEGER NOT NULL DEFAULT 0 CHECK(show_first_name IN (0,1)),
    lead_notice_version TEXT,
    lead_approved_at TEXT,
    stopped_at TEXT,
    stop_reason TEXT,
    PRIMARY KEY(goal_id,user_id),
    FOREIGN KEY(goal_id,user_id) REFERENCES goal_members(goal_id,user_id) ON DELETE CASCADE,
    CHECK((lead_notice_version IS NULL)=(lead_approved_at IS NULL))
);
CREATE INDEX goal_allocations_owner_active ON goal_allocations(user_id,stopped_at,permission_expires_at);

CREATE TABLE goal_sessions (
    id TEXT PRIMARY KEY NOT NULL,
    goal_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    task_id TEXT REFERENCES goal_tasks(id) ON DELETE SET NULL,
    allocation_approval_id TEXT NOT NULL,
    charter_revision INTEGER NOT NULL CHECK(charter_revision>=1),
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    grace_ends_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    minutes_used INTEGER CHECK(minutes_used IS NULL OR minutes_used BETWEEN 0 AND reserved_minutes),
    did TEXT,
    reserved_minutes INTEGER NOT NULL CHECK(reserved_minutes BETWEEN 5 AND 240),
    findings_count INTEGER NOT NULL DEFAULT 0 CHECK(findings_count BETWEEN 0 AND 10),
    reviews_count INTEGER NOT NULL DEFAULT 0 CHECK(reviews_count BETWEEN 0 AND 20),
    FOREIGN KEY(goal_id,user_id) REFERENCES goal_members(goal_id,user_id) ON DELETE CASCADE
);
CREATE INDEX goal_sessions_owner_time ON goal_sessions(user_id,started_at);
CREATE INDEX goal_sessions_goal_owner_time ON goal_sessions(goal_id,user_id,started_at);
CREATE UNIQUE INDEX goal_sessions_one_open_owner ON goal_sessions(user_id) WHERE ended_at IS NULL;

-- A lease's original reservation cannot be rewritten to recover budget or
-- extend a session. Ending/stopping may shorten grace; counters only increase.
CREATE TRIGGER goal_sessions_immutable BEFORE UPDATE ON goal_sessions
WHEN OLD.id IS NOT NEW.id OR OLD.goal_id IS NOT NEW.goal_id OR OLD.user_id IS NOT NEW.user_id
 OR (OLD.task_id IS NOT NEW.task_id AND NEW.task_id IS NOT NULL)
 OR OLD.allocation_approval_id IS NOT NEW.allocation_approval_id OR OLD.charter_revision IS NOT NEW.charter_revision
 OR OLD.started_at IS NOT NEW.started_at OR OLD.ends_at IS NOT NEW.ends_at
 OR OLD.reserved_minutes IS NOT NEW.reserved_minutes OR NEW.grace_ends_at>OLD.grace_ends_at
 OR NEW.findings_count<OLD.findings_count OR NEW.reviews_count<OLD.reviews_count
BEGIN SELECT RAISE(ABORT,'Goal session reservations cannot be rewritten or extended'); END;

-- Ordinary membership creation, session activity and record inserts make no
-- recovery marker. Changed membership or standing permission must not return
-- after a restore, including a newly granted permission or first-name opt-in.
CREATE TRIGGER recovery_goal_members_update AFTER UPDATE ON goal_members
WHEN OLD.goal_id IS NOT NEW.goal_id OR OLD.user_id IS NOT NEW.user_id OR OLD.handle IS NOT NEW.handle
 OR OLD.state IS NOT NEW.state OR OLD.capabilities_json IS NOT NEW.capabilities_json
 OR OLD.note IS NOT NEW.note OR OLD.show_first_name IS NOT NEW.show_first_name
 OR OLD.charter_revision IS NOT NEW.charter_revision
 OR OLD.work_generation IS NOT NEW.work_generation
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_members_delete AFTER DELETE ON goal_members
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_allocations_insert AFTER INSERT ON goal_allocations
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_allocations_update AFTER UPDATE ON goal_allocations
WHEN OLD.goal_id IS NOT NEW.goal_id OR OLD.user_id IS NOT NEW.user_id
 OR OLD.sessions_per_week IS NOT NEW.sessions_per_week OR OLD.minutes_per_session IS NOT NEW.minutes_per_session
 OR OLD.until IS NOT NEW.until OR OLD.permission_expires_at IS NOT NEW.permission_expires_at
 OR OLD.notice_version IS NOT NEW.notice_version OR OLD.approved_at IS NOT NEW.approved_at
 OR OLD.approval_id IS NOT NEW.approval_id OR OLD.charter_revision IS NOT NEW.charter_revision
 OR OLD.show_first_name IS NOT NEW.show_first_name OR OLD.lead_notice_version IS NOT NEW.lead_notice_version
 OR OLD.lead_approved_at IS NOT NEW.lead_approved_at OR OLD.stopped_at IS NOT NEW.stopped_at
 OR OLD.stop_reason IS NOT NEW.stop_reason
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_allocations_delete AFTER DELETE ON goal_allocations
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_sessions_revoke AFTER UPDATE ON goal_sessions
WHEN (OLD.end_reason IS NOT NEW.end_reason AND (NEW.end_reason IS NULL OR NEW.end_reason NOT IN ('completed','expired','permission_expired')))
 OR (OLD.ended_at IS NOT NULL AND NEW.ended_at IS NULL)
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_sessions_delete AFTER DELETE ON goal_sessions
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
