-- Common goals M1. Memberships, allocations and work leases arrive in M2.
CREATE TABLE goals (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    topic_key TEXT NOT NULL,
    lead_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    lead_handle TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('proposed','open','paused','closed','refused','hidden')),
    tier TEXT NOT NULL CHECK(tier IN ('standard','sensitive')),
    category TEXT NOT NULL,
    membership TEXT NOT NULL CHECK(membership IN ('open','lead_approves')),
    charter_json TEXT CHECK(charter_json IS NULL OR json_valid(charter_json)),
    charter_revision INTEGER NOT NULL DEFAULT 1 CHECK(charter_revision>=1),
    latest_charter_revision INTEGER NOT NULL DEFAULT 1 CHECK(latest_charter_revision>=charter_revision),
    pending_charter_json TEXT CHECK(pending_charter_json IS NULL OR json_valid(pending_charter_json)),
    pending_entry_id TEXT,
    review_pending INTEGER NOT NULL DEFAULT 0 CHECK(review_pending IN (0,1)),
    last_lead_step_at TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    closed_at TEXT
);
CREATE UNIQUE INDEX goals_open_topic ON goals(topic_key) WHERE state='open';
CREATE INDEX goals_lead_state ON goals(lead_user_id,state);

CREATE TABLE goal_tasks (
    id TEXT PRIMARY KEY NOT NULL,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    author_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    state TEXT NOT NULL CHECK(state IN ('open','closed','superseded')),
    task_json TEXT CHECK(task_json IS NULL OR json_valid(task_json)),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    last_worked_at TEXT
);
CREATE INDEX goal_tasks_goal_state ON goal_tasks(goal_id,state);

CREATE TABLE goal_entries (
    id TEXT PRIMARY KEY NOT NULL,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK(seq>=1),
    kind TEXT NOT NULL,
    author_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    author_handle TEXT NOT NULL,
    session_id TEXT,
    refs_json TEXT NOT NULL CHECK(json_valid(refs_json)),
    content_json TEXT CHECK(content_json IS NULL OR json_valid(content_json)),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    previous_sha256 TEXT NOT NULL CHECK(length(previous_sha256)=64),
    entry_sha256 TEXT NOT NULL CHECK(length(entry_sha256)=64),
    status TEXT NOT NULL DEFAULT 'visible' CHECK(status IN ('visible','pending_peer_check','held','withdrawn','hidden')),
    created_at TEXT NOT NULL,
    UNIQUE(goal_id,seq)
);
CREATE INDEX goal_entries_author ON goal_entries(author_user_id,goal_id,seq);
CREATE INDEX goal_entries_goal_created ON goal_entries(goal_id,created_at);

CREATE TABLE goal_reports (
    id TEXT PRIMARY KEY NOT NULL,
    entry_id TEXT NOT NULL REFERENCES goal_entries(id) ON DELETE CASCADE,
    reporter_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(entry_id,reporter_id)
);

-- Immutable envelopes cannot be rewritten. Erasure can remove content and
-- account links; moderation can change visibility, leaving hashes intact.
CREATE TRIGGER goal_entries_immutable BEFORE UPDATE ON goal_entries
WHEN OLD.id IS NOT NEW.id OR OLD.goal_id IS NOT NEW.goal_id OR OLD.seq IS NOT NEW.seq
 OR OLD.kind IS NOT NEW.kind OR OLD.author_handle IS NOT NEW.author_handle
 OR OLD.session_id IS NOT NEW.session_id OR OLD.refs_json IS NOT NEW.refs_json
 OR OLD.content_sha256 IS NOT NEW.content_sha256 OR OLD.previous_sha256 IS NOT NEW.previous_sha256
 OR OLD.entry_sha256 IS NOT NEW.entry_sha256 OR OLD.created_at IS NOT NEW.created_at
 OR (OLD.content_json IS NOT NEW.content_json AND NEW.content_json IS NOT NULL)
 OR (OLD.author_user_id IS NOT NEW.author_user_id AND NEW.author_user_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'Goal record envelopes are immutable'); END;
CREATE TRIGGER goal_entries_append_only BEFORE DELETE ON goal_entries
BEGIN SELECT RAISE(ABORT,'Goal record entries cannot be deleted'); END;
CREATE TRIGGER goal_entries_sequence BEFORE INSERT ON goal_entries
WHEN NEW.seq != COALESCE((SELECT MAX(seq)+1 FROM goal_entries WHERE goal_id=NEW.goal_id),1)
 OR NEW.previous_sha256 != COALESCE((SELECT entry_sha256 FROM goal_entries WHERE goal_id=NEW.goal_id ORDER BY seq DESC LIMIT 1),printf('%064d',0))
BEGIN SELECT RAISE(ABORT,'Goal record entries must append consecutively'); END;

-- Content creation does not advance recovery authority. Changed/erased content
-- and operator decisions must not be undone by restoring an older snapshot.
CREATE TRIGGER recovery_goals_update AFTER UPDATE ON goals
WHEN OLD.slug IS NOT NEW.slug OR OLD.topic_key IS NOT NEW.topic_key OR OLD.lead_user_id IS NOT NEW.lead_user_id
 OR OLD.lead_handle IS NOT NEW.lead_handle OR OLD.state IS NOT NEW.state OR OLD.tier IS NOT NEW.tier
 OR OLD.category IS NOT NEW.category OR OLD.membership IS NOT NEW.membership
 OR OLD.charter_json IS NOT NEW.charter_json OR OLD.charter_revision IS NOT NEW.charter_revision
 OR OLD.latest_charter_revision IS NOT NEW.latest_charter_revision
 OR OLD.pending_charter_json IS NOT NEW.pending_charter_json OR OLD.pending_entry_id IS NOT NEW.pending_entry_id
 OR OLD.review_pending IS NOT NEW.review_pending OR OLD.approved_at IS NOT NEW.approved_at OR OLD.closed_at IS NOT NEW.closed_at
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goals_delete AFTER DELETE ON goals
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_tasks_update AFTER UPDATE ON goal_tasks
WHEN OLD.goal_id IS NOT NEW.goal_id OR OLD.author_user_id IS NOT NEW.author_user_id OR OLD.state IS NOT NEW.state
 OR OLD.task_json IS NOT NEW.task_json OR OLD.closed_at IS NOT NEW.closed_at
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_tasks_delete AFTER DELETE ON goal_tasks
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_entries_update AFTER UPDATE ON goal_entries
WHEN OLD.author_user_id IS NOT NEW.author_user_id OR OLD.content_json IS NOT NEW.content_json OR OLD.status IS NOT NEW.status
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_reports_update AFTER UPDATE ON goal_reports
WHEN OLD.reporter_id IS NOT NEW.reporter_id OR OLD.entry_id IS NOT NEW.entry_id OR OLD.reason IS NOT NEW.reason OR OLD.details IS NOT NEW.details
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_reports_delete AFTER DELETE ON goal_reports
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
