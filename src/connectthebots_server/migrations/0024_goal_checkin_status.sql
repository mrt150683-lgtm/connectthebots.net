-- Derived owner-only delivery checkpoints. No authored content or permission.
-- Ordinary check-ins do not advance recovery authority; restore discards them.
CREATE TABLE goal_checkin_status (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entry_id TEXT NOT NULL REFERENCES goal_entries(id) ON DELETE CASCADE,
    visibility TEXT NOT NULL CHECK(visibility IN ('visible','pending_peer_check','held','hidden','withdrawn')),
    finding_status TEXT NOT NULL CHECK(finding_status IN ('new','pending_peer_check','checked','confirmed','disputed','held','hidden','withdrawn')),
    public INTEGER NOT NULL CHECK(public IN (0,1)),
    delivered_at TEXT NOT NULL,
    PRIMARY KEY(user_id,entry_id)
);
