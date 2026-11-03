-- Explicit role approval is separate from contribution/lead work permission.
CREATE TABLE goal_lead_approvals (
    id TEXT PRIMARY KEY NOT NULL,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    charter_revision INTEGER NOT NULL CHECK(charter_revision>=1),
    previous_lead_handle TEXT NOT NULL,
    handover_id TEXT,
    notice_version TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','consumed','revoked')),
    decided_at TEXT,
    UNIQUE(goal_id,user_id)
);
CREATE INDEX goal_lead_approvals_goal ON goal_lead_approvals(goal_id,state);
CREATE TABLE goal_lead_handovers (
    id TEXT PRIMARY KEY NOT NULL,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    from_handle TEXT NOT NULL,
    target_handle TEXT NOT NULL,
    charter_revision INTEGER NOT NULL CHECK(charter_revision>=1),
    approved_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','consumed','revoked')),
    decided_at TEXT,
    UNIQUE(goal_id)
);
CREATE INDEX goal_lead_handovers_owner ON goal_lead_handovers(user_id);
CREATE TRIGGER recovery_goal_lead_approvals_insert AFTER INSERT ON goal_lead_approvals
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_lead_approvals_update AFTER UPDATE ON goal_lead_approvals
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_lead_approvals_delete AFTER DELETE ON goal_lead_approvals
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_lead_handovers_insert AFTER INSERT ON goal_lead_handovers
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_lead_handovers_update AFTER UPDATE ON goal_lead_handovers
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
CREATE TRIGGER recovery_goal_lead_handovers_delete AFTER DELETE ON goal_lead_handovers
BEGIN UPDATE recovery_authority SET revision=revision+1,growth_revision=growth_revision+1 WHERE singleton=1; END;
