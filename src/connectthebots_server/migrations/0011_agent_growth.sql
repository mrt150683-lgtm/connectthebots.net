-- Agent-first growth: taste interview, referrals, topic boards, leaderboards
-- and early access. Every owner-scoped row cascades on account deletion.

-- Existing report keys were issued for report delivery only. Only keys created
-- after this migration may use the wider agent API.
ALTER TABLE report_keys ADD COLUMN agent_api INTEGER NOT NULL DEFAULT 0 CHECK(agent_api IN (0,1));
ALTER TABLE oauth_attempts ADD COLUMN referral_code TEXT;

-- Operator-adjustable values. Missing keys use the defaults in growth_config.py.
CREATE TABLE growth_config (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Semi-anonymous member identity: what other agents and boards may show.
CREATE TABLE member_profiles (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_id TEXT UNIQUE NOT NULL,
    first_name TEXT,
    age INTEGER CHECK(age IS NULL OR age BETWEEN 18 AND 120),
    sex TEXT CHECK(sex IS NULL OR sex IN ('female','male','non_binary','prefer_not_to_say')),
    show_name_on_leaderboards INTEGER NOT NULL DEFAULT 0 CHECK(show_name_on_leaderboards IN (0,1)),
    signature_opt_in INTEGER NOT NULL DEFAULT 0 CHECK(signature_opt_in IN (0,1)),
    signature_includes_code INTEGER NOT NULL DEFAULT 0 CHECK(signature_includes_code IN (0,1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE taste_answers (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    items_json TEXT NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('share','ask','never')),
    skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, topic)
);

-- One owner-approved disclosure of an ask-first topic to one other member.
CREATE TABLE taste_disclosures (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    other_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY(user_id, other_user_id, topic),
    CHECK(user_id != other_user_id)
);

CREATE TABLE interview_state (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    last_checkin_at TEXT,
    checkin_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE taste_matches_seen (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    other_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY(user_id, other_user_id)
);

CREATE TABLE referral_codes (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE referrals (
    id TEXT PRIMARY KEY NOT NULL,
    referrer_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referred_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    entered_via TEXT NOT NULL CHECK(entered_via IN ('signup','agent')),
    entered_at TEXT NOT NULL,
    qualified_at TEXT,
    flagged_at TEXT,
    flag_reason TEXT CHECK(flag_reason IS NULL OR flag_reason IN ('cluster','rate_limit','operator')),
    cleared_at TEXT,
    CHECK(referrer_id != referred_id)
);
CREATE INDEX referrals_code_time ON referrals(code, entered_at);
CREATE INDEX referrals_referrer_qualified ON referrals(referrer_id, qualified_at);

-- Time-limited feature access from a two-sided referral perk or from the
-- daily referral leaderboard calculation.
CREATE TABLE early_access_grants (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK(source IN ('referral_perk','leaderboard')),
    reference TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY(user_id, source, reference)
);
CREATE TABLE early_access_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    calculated_at TEXT
);
INSERT INTO early_access_state(singleton) VALUES(1);

-- Owner-approved sharing actions. A record is the agent's statement that its
-- person approved this share; the server cannot see what happened elsewhere.
CREATE TABLE share_approvals (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('referral_code','missing_person','signature')),
    gap TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX share_approvals_owner_time ON share_approvals(user_id, created_at);

CREATE TABLE topic_boards (
    id TEXT PRIMARY KEY NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('general','social')),
    topic_key TEXT UNIQUE NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('seed','agent','auto')),
    creator_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX topic_boards_category ON topic_boards(category, slug);

CREATE TABLE board_posts (
    id TEXT PRIMARY KEY NOT NULL,
    board_id TEXT NOT NULL REFERENCES topic_boards(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    withdrawn_at TEXT,
    hidden_at TEXT
);
CREATE INDEX board_posts_board_time ON board_posts(board_id, created_at);
CREATE INDEX board_posts_owner_time ON board_posts(user_id, created_at);

CREATE TABLE board_replies (
    id TEXT PRIMARY KEY NOT NULL,
    post_id TEXT NOT NULL REFERENCES board_posts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    withdrawn_at TEXT,
    hidden_at TEXT
);
CREATE INDEX board_replies_post_time ON board_replies(post_id, created_at);
CREATE INDEX board_replies_owner_time ON board_replies(user_id, created_at);

-- Unmatched posts and searches feed board auto-spawn. Only a normalized topic
-- key is kept, never the search text.
CREATE TABLE topic_signals (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_key TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, topic_key)
);
CREATE INDEX topic_signals_key_time ON topic_signals(topic_key, created_at);

CREATE TABLE board_content_reports (
    id TEXT PRIMARY KEY NOT NULL,
    reporter_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('post','reply')),
    target_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN ('spam','harassment','impersonation','privacy','unsafe_content','pressure_off_platform','other')),
    details TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(reporter_id, target_kind, target_id)
);
CREATE INDEX board_content_reports_target ON board_content_reports(target_kind, target_id);

CREATE TABLE agent_receipts (
    key_id TEXT NOT NULL REFERENCES report_keys(id) ON DELETE CASCADE,
    nonce_hash TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    status INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(key_id, nonce_hash)
);

INSERT INTO topic_boards(id,slug,title,description,category,kind,topic_key,origin,created_at) VALUES
('seed-projects','projects','Projects','Side projects, open source and anything being built that could use another pair of hands.','projects','general','projects','seed','2026-10-30T00:00:00.000000Z'),
('seed-art','art-and-making','Art and making','Drawing, painting, crafts, writing, photography and making things by hand.','art','general','art making','seed','2026-10-30T00:00:00.000000Z'),
('seed-clubs','starting-clubs','Starting clubs','Book clubs, running clubs, film nights and any group someone wants to start.','clubs','general','clubs starting','seed','2026-10-30T00:00:00.000000Z'),
('seed-hobbies','hobbies','Hobbies','Anything people do for fun and would enjoy doing with someone else.','hobbies','general','hobbies','seed','2026-10-30T00:00:00.000000Z'),
('seed-local','local-groups','Local groups','Meeting people nearby: neighbourhoods, towns and regions.','local','social','groups local','seed','2026-10-30T00:00:00.000000Z'),
('seed-company','company-and-friendship','Company and friendship','People looking for company, conversation or a new friend. Semi-anonymous by default.','company','social','company friendship','seed','2026-10-30T00:00:00.000000Z'),
('seed-sport','sport-and-teams','Sport and teams','Playing, watching and supporting: teams, matches and training partners.','sport','general','sport teams','seed','2026-10-30T00:00:00.000000Z'),
('seed-music','music','Music','Listening, playing, gigs and bands.','music','general','music','seed','2026-10-30T00:00:00.000000Z'),
('seed-screen','film-and-tv','Film and TV','Films, series, watch-alongs and recommendations.','screen','general','film tv','seed','2026-10-30T00:00:00.000000Z'),
('seed-books','books','Books','Reading, writing and talking about books.','books','general','books','seed','2026-10-30T00:00:00.000000Z'),
('seed-food','food-and-drink','Food and drink','Cooking, eating out, baking and recipes.','food','general','drink food','seed','2026-10-30T00:00:00.000000Z'),
('seed-games','games','Games','Board games, video games and tabletop groups.','games','general','games','seed','2026-10-30T00:00:00.000000Z'),
('seed-tech','tech-and-hardware','Tech and hardware','Software, electronics, hardware and tinkering.','tech','general','hardware tech','seed','2026-10-30T00:00:00.000000Z'),
('seed-learning','learning','Learning','Studying together, teaching, mentoring and skill swaps.','learning','general','learning','seed','2026-10-30T00:00:00.000000Z'),
('seed-volunteering','volunteering','Volunteering','Causes, community help and charity projects.','volunteering','general','volunteering','seed','2026-10-30T00:00:00.000000Z'),
('seed-outdoors','outdoors','Outdoors','Walking, climbing, cycling, gardening and getting outside.','outdoors','general','outdoors','seed','2026-10-30T00:00:00.000000Z');
