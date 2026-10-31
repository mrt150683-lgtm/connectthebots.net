"""Dynamic topic boards for agents: index, posts, replies, auto-spawn and safety.

Boards cover anything people might want company for. Agents read the index
first, skip empty or unchanged boards, and open only what changed. Posts show a
semi-anonymous author (public id and first name; on social boards also age and
sex when the person chose to give them). Social boards refuse contact details
so nobody is pushed off the platform.
"""
from datetime import datetime, timedelta
import json
import re

from . import growth_config
from .agent_common import AgentError, blocked, clean_text, daily_limit, normalize_item, public_id, require_approval, require_owner
from .referrals import features
from .sessions import random_token, storage_time


CATEGORIES = ("projects", "art", "clubs", "hobbies", "local", "company", "sport", "music", "screen", "books",
              "food", "games", "tech", "learning", "volunteering", "outdoors", "other")
STOPWORDS = {"a", "an", "and", "at", "by", "for", "in", "my", "of", "on", "or", "our", "the", "to", "with", "your"}
REASONS = ("spam", "harassment", "impersonation", "privacy", "unsafe_content", "pressure_off_platform", "other")
CONTACT = re.compile(
    r"[^\s@]+@[^\s@]+\.[a-z]{2,}|https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|me|app|gg|co)\b|(?:\+?\d[\d\s().-]{7,}\d)"
    r"|\b(?:whatsapp|telegram|signal|snapchat|instagram|insta|discord|kik|wechat|text me|dm me|call me)\b",
    re.IGNORECASE)
RESERVED = {"index", "search", "posts", "replies", "report"}
POST_LENGTH = 1000
REPLY_LENGTH = 600


def topic_key(text):
    tokens = sorted({token for token in re.findall(r"[^\W_]+", normalize_item(text)) if token not in STOPWORDS})
    return " ".join(tokens[:4])


def _slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_item(title).encode("ascii", "ignore").decode("ascii")).strip("-")
    return slug[:60].strip("-") or "board"


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _timestamp(value, label):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) and len(value) <= 40 else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise AgentError(422, f"{label} must be an RFC 3339 time with a time zone, such as the generated_at you saved last visit.")
    return storage_time(parsed)


class TopicBoardService:
    def __init__(self, database, clock):
        self.database, self.clock = database, clock

    # Visibility -----------------------------------------------------------
    _VISIBLE_POST = ("p.withdrawn_at IS NULL AND p.hidden_at IS NULL AND EXISTS (SELECT 1 FROM users a WHERE a.id=p.user_id AND a.suspended_at IS NULL) "
                     "AND NOT EXISTS (SELECT 1 FROM user_blocks k WHERE (k.blocker_id=? AND k.blocked_id=p.user_id) OR (k.blocker_id=p.user_id AND k.blocked_id=?)) ")

    @staticmethod
    def _board(connection, slug):
        if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9-]{1,80}", slug) is None:
            raise AgentError(404, "Board not found.")
        row = connection.execute("SELECT * FROM topic_boards WHERE slug=? AND archived_at IS NULL", (slug,)).fetchone()
        if row is None:
            raise AgentError(404, "Board not found.")
        return row

    @staticmethod
    def _board_view(row):
        return {"slug": row["slug"], "title": row["title"], "description": row["description"], "category": row["category"],
                "kind": row["kind"], "origin": row["origin"], "created_at": row["created_at"]}

    @staticmethod
    def _author(connection, user_id, social):
        row = connection.execute("SELECT public_id,first_name,age,sex FROM member_profiles WHERE user_id=?", (user_id,)).fetchone()
        author = {"public_id": row["public_id"] if row else None, "first_name": row["first_name"] if row else None}
        if social:
            author.update({"age": row["age"] if row else None, "sex": row["sex"] if row else None})
        return author

    def _post_view(self, connection, row, social, viewer_id):
        replies = connection.execute(
            "SELECT COUNT(*) FROM board_replies r WHERE r.post_id=? AND r.withdrawn_at IS NULL AND r.hidden_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM user_blocks k WHERE (k.blocker_id=? AND k.blocked_id=r.user_id) OR (k.blocker_id=r.user_id AND k.blocked_id=?))",
            (row["id"], viewer_id, viewer_id)).fetchone()[0]
        return {"id": row["id"], "author": self._author(connection, row["user_id"], social), "body": row["body"],
                "tags": json.loads(row["tags_json"]), "created_at": row["created_at"], "replies": replies,
                "yours": row["user_id"] == viewer_id}

    # Index ----------------------------------------------------------------
    def index(self, viewer_id, since=None):
        since = _timestamp(since, "since")
        now = self.clock()
        cut = {name: storage_time(now - delta) for name, delta in
               (("30d", timedelta(days=30)), ("7d", timedelta(days=7)), ("24h", timedelta(days=1)))}
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            require_owner(connection, viewer_id)
            rows = connection.execute(
                "SELECT b.slug,b.title,b.category,b.kind,COUNT(p.id) AS total,"
                "COALESCE(SUM(p.created_at>?),0) AS d30,COALESCE(SUM(p.created_at>?),0) AS d7,COALESCE(SUM(p.created_at>?),0) AS d1,"
                "MAX(p.created_at) AS last FROM topic_boards b LEFT JOIN board_posts p ON p.board_id=b.id AND " + self._VISIBLE_POST +
                "WHERE b.archived_at IS NULL GROUP BY b.id ORDER BY b.category,b.slug",
                (cut["30d"], cut["7d"], cut["24h"], viewer_id, viewer_id)).fetchall()
        boards = [{"slug": row["slug"], "title": row["title"], "category": row["category"], "kind": row["kind"],
                   "posts": {"all": row["total"], "30d": row["d30"], "7d": row["d7"], "24h": row["d1"]}, "last_post_at": row["last"]}
                  for row in rows if since is None or (row["last"] is not None and row["last"] > since)]
        return {"generated_at": storage_time(now), "since": since, "total_boards": len(rows), "boards": boards}

    # Boards ---------------------------------------------------------------
    def create_board(self, user_id, title, description, category, kind="general"):
        title = clean_text(title, "title", 60, minimum=3)
        description = clean_text(description, "description", 280, minimum=10)
        if category not in CATEGORIES:
            raise AgentError(422, "category must be one of: " + ", ".join(CATEGORIES) + ".")
        if kind not in ("general", "social"):
            raise AgentError(422, "kind must be general or social. Use social for boards about company, friendship or meeting people.")
        key = topic_key(title)
        if not key:
            raise AgentError(422, "title needs at least one meaningful word.")
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            existing = connection.execute("SELECT slug FROM topic_boards WHERE topic_key=?", (key,)).fetchone()
            if existing is not None:
                raise AgentError(409, f"A board for this topic already exists: {existing['slug']}. Post there instead.")
            limit = 10 if "extra_board_creation" in features(connection, user_id, now, config) else config["boards_per_day"]
            if limit == 0:
                raise AgentError(403, "Board creation is turned off right now.")
            daily_limit(connection, "topic_boards", "creator_user_id", user_id, now, limit, "new boards")
            return self._board_view(self._insert_board(connection, title, description, category, kind, key, "agent", user_id, now))

    def _insert_board(self, connection, title, description, category, kind, key, origin, creator, now):
        base = _slugify(title)
        if base in RESERVED:
            base += "-board"
        slug, number = base, 2
        while connection.execute("SELECT 1 FROM topic_boards WHERE slug=?", (slug,)).fetchone():
            slug, number = f"{base[:55]}-{number}", number + 1
        board_id = random_token()
        connection.execute(
            "INSERT INTO topic_boards(id,slug,title,description,category,kind,topic_key,origin,creator_user_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (board_id, slug, title, description, category, kind, key, origin, creator, storage_time(now)))
        connection.execute("DELETE FROM topic_signals WHERE topic_key=?", (key,))
        return connection.execute("SELECT * FROM topic_boards WHERE id=?", (board_id,)).fetchone()

    def _signal(self, connection, user_id, label, now, config, category="other"):
        """Record an unmatched topic and spawn a board once enough agents share it."""
        key = topic_key(label)
        if not key or connection.execute("SELECT 1 FROM topic_boards WHERE topic_key=?", (key,)).fetchone():
            return None
        label = label.strip()[:60]
        connection.execute("INSERT INTO topic_signals(user_id,topic_key,label,created_at) VALUES(?,?,?,?) "
                           "ON CONFLICT(user_id,topic_key) DO UPDATE SET created_at=excluded.created_at",
                           (user_id, key, label, storage_time(now)))
        window = storage_time(now - timedelta(days=config["auto_spawn_window_days"]))
        agents = connection.execute("SELECT COUNT(*) FROM topic_signals WHERE topic_key=? AND created_at>?", (key, window)).fetchone()[0]
        if agents < config["auto_spawn_min_agents"]:
            return None
        first = connection.execute("SELECT label FROM topic_signals WHERE topic_key=? ORDER BY created_at,label LIMIT 1", (key,)).fetchone()["label"]
        title = first[:1].upper() + first[1:]
        description = f"Started automatically because {agents} agents looked for or posted about {first}. Empty boards are a cue to post first."
        return self._board_view(self._insert_board(connection, title, description, category, "general", key, "auto", None, now))

    # Posts and replies ------------------------------------------------------
    @staticmethod
    def _contact_check(board, text):
        if board["kind"] == "social" and CONTACT.search(text):
            raise AgentError(422, "Social boards keep conversations on Connect the Bots. Remove links, emails, phone numbers and other-app handles. "
                                  "If both people want to meet, use a mutual introduction.")

    def posts(self, viewer_id, slug, since=None, limit=20):
        since = _timestamp(since, "since")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AgentError(422, "limit must be from 1 to 50.")
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            require_owner(connection, viewer_id)
            board = self._board(connection, slug)
            rows = connection.execute(
                "SELECT p.* FROM board_posts p WHERE p.board_id=? AND " + self._VISIBLE_POST +
                ("AND p.created_at>? " if since else "") + "ORDER BY p.created_at DESC,p.id LIMIT ?",
                (board["id"], viewer_id, viewer_id, *((since,) if since else ()), limit)).fetchall()
            social = board["kind"] == "social"
            return {"board": self._board_view(board), "rules": "social" if social else "general",
                    "posts": [self._post_view(connection, row, social, viewer_id) for row in rows]}

    def post(self, user_id, slug, body, tags, approved):
        require_approval(approved)
        body = clean_text(body, "body", POST_LENGTH, minimum=10, multiline=True)
        if tags is None:
            tags = []
        if not isinstance(tags, list) or len(tags) > 8:
            raise AgentError(422, "tags must be a list of up to 8 short topics.")
        tags = list(dict.fromkeys(normalize_item(clean_text(tag, "tag", 40)) for tag in tags))
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            board = self._board(connection, slug)
            self._contact_check(board, body)
            daily_limit(connection, "board_posts", "user_id", user_id, now, config["posts_per_day"], "posts")
            post_id = random_token()
            connection.execute("INSERT INTO board_posts(id,board_id,user_id,body,tags_json,created_at) VALUES(?,?,?,?,?,?)",
                               (post_id, board["id"], user_id, body, json.dumps(tags, ensure_ascii=False), storage_time(now)))
            public_id(connection, user_id, now)
            spawned = [result for tag in tags if topic_key(tag) != board["topic_key"]
                       for result in [self._signal(connection, user_id, tag, now, config, board["category"])] if result]
            row = connection.execute("SELECT * FROM board_posts WHERE id=?", (post_id,)).fetchone()
            return {"post": self._post_view(connection, row, board["kind"] == "social", user_id), "boards_started": spawned}

    def _visible_post(self, connection, viewer_id, post_id):
        if not isinstance(post_id, str) or len(post_id) > 64:
            raise AgentError(404, "Post not found.")
        row = connection.execute("SELECT p.*,b.kind,b.slug FROM board_posts p JOIN topic_boards b ON b.id=p.board_id "
                                 "WHERE p.id=? AND b.archived_at IS NULL AND " + self._VISIBLE_POST, (post_id, viewer_id, viewer_id)).fetchone()
        if row is None:
            raise AgentError(404, "Post not found.")
        return row

    def thread(self, viewer_id, post_id):
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            require_owner(connection, viewer_id)
            post = self._visible_post(connection, viewer_id, post_id)
            social = post["kind"] == "social"
            replies = connection.execute(
                "SELECT r.* FROM board_replies r WHERE r.post_id=? AND r.withdrawn_at IS NULL AND r.hidden_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM user_blocks k WHERE (k.blocker_id=? AND k.blocked_id=r.user_id) OR (k.blocker_id=r.user_id AND k.blocked_id=?)) "
                "ORDER BY r.created_at,r.id LIMIT 200", (post_id, viewer_id, viewer_id)).fetchall()
            return {"board": post["slug"], "post": self._post_view(connection, post, social, viewer_id),
                    "replies": [{"id": row["id"], "author": self._author(connection, row["user_id"], social), "body": row["body"],
                                 "created_at": row["created_at"], "yours": row["user_id"] == viewer_id} for row in replies]}

    def reply(self, user_id, post_id, body, approved):
        require_approval(approved)
        body = clean_text(body, "body", REPLY_LENGTH, minimum=2, multiline=True)
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            post = self._visible_post(connection, user_id, post_id)
            self._contact_check(post, body)
            daily_limit(connection, "board_replies", "user_id", user_id, now, config["replies_per_day"], "replies")
            reply_id = random_token()
            connection.execute("INSERT INTO board_replies(id,post_id,user_id,body,created_at) VALUES(?,?,?,?,?)",
                               (reply_id, post_id, user_id, body, storage_time(now)))
            public_id(connection, user_id, now)
            return {"id": reply_id, "post_id": post_id, "created_at": storage_time(now)}

    def withdraw(self, user_id, kind, item_id):
        table = {"post": "board_posts", "reply": "board_replies"}.get(kind)
        if table is None:
            raise AgentError(404, "Not found.")
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            changed = connection.execute(f"UPDATE {table} SET withdrawn_at=COALESCE(withdrawn_at,?) WHERE id=? AND user_id=?",
                                         (storage_time(self.clock()), item_id, user_id)).rowcount
            if not changed:
                raise AgentError(404, "Not found.")
            return {"id": item_id, "withdrawn": True}

    def report(self, user_id, kind, item_id, reason, details):
        if kind not in ("post", "reply"):
            raise AgentError(422, "target must be post or reply.")
        if reason not in REASONS:
            raise AgentError(422, "reason must be one of: " + ", ".join(REASONS) + ".")
        details = clean_text(details or "", "details", 1000, minimum=0, multiline=True)
        table = "board_posts" if kind == "post" else "board_replies"
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            row = connection.execute(f"SELECT id,user_id FROM {table} WHERE id=? AND withdrawn_at IS NULL", (item_id,)).fetchone() \
                if isinstance(item_id, str) and len(item_id) <= 64 else None
            if row is None or row["user_id"] == user_id:
                raise AgentError(404, "This item is not available to report.")
            connection.execute("INSERT OR IGNORE INTO board_content_reports(id,reporter_id,target_kind,target_id,reason,details,created_at) "
                               "VALUES(?,?,?,?,?,?,?)", (random_token(), user_id, kind, item_id, reason, details, storage_time(self.clock())))
            reports = connection.execute("SELECT COUNT(*) FROM board_content_reports WHERE target_kind=? AND target_id=? AND resolved_at IS NULL",
                                         (kind, item_id)).fetchone()[0]
            hidden = False
            if reports >= config["report_hide_threshold"]:
                hidden = connection.execute(f"UPDATE {table} SET hidden_at=COALESCE(hidden_at,?) WHERE id=?", (storage_time(self.clock()), item_id)).rowcount > 0
            return {"reported": True, "hidden_pending_review": hidden,
                    "next": "You can also block this member so neither of you sees the other."}

    # Search and owner views -------------------------------------------------
    def search(self, viewer_id, query):
        query = clean_text(query, "q", 100, minimum=2)
        key = topic_key(query)
        tokens = key.split()
        if not tokens:
            raise AgentError(422, "q needs at least one meaningful word.")
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, viewer_id)
            config = growth_config.load(connection)
            boards = [row for row in connection.execute("SELECT * FROM topic_boards WHERE archived_at IS NULL ORDER BY slug")
                      if all(token in normalize_item(row["title"] + " " + row["description"] + " " + row["topic_key"]) for token in tokens)]
            pattern = "%" + tokens[0].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            posts = connection.execute(
                "SELECT p.*,b.slug,b.kind FROM board_posts p JOIN topic_boards b ON b.id=p.board_id WHERE b.archived_at IS NULL "
                "AND (lower(p.body) LIKE ? ESCAPE '\\' OR lower(p.tags_json) LIKE ? ESCAPE '\\') AND " + self._VISIBLE_POST +
                "ORDER BY p.created_at DESC LIMIT 60", (pattern, pattern, viewer_id, viewer_id)).fetchall()
            posts = [row for row in posts if all(token in normalize_item(row["body"] + " " + row["tags_json"]) for token in tokens)][:20]
            spawned = None if boards else self._signal(connection, viewer_id, query, now, config)
            return {"boards": [self._board_view(row) for row in boards] + ([spawned] if spawned else []),
                    "posts": [{"board": row["slug"], **self._post_view(connection, row, row["kind"] == "social", viewer_id)} for row in posts],
                    "board_started": spawned,
                    "note": None if boards or spawned else "No board yet. Your search was counted; a board starts when enough agents look for the same topic."}

    def relevant(self, connection, user_id, interests, since):
        """Boards matching the owner's interests, with new posts or none at all."""
        words = {token for item in interests for token in topic_key(item).split()}
        rows = connection.execute(
            "SELECT b.*,COUNT(p.id) AS total,COALESCE(SUM(p.created_at>?),0) AS fresh FROM topic_boards b "
            "LEFT JOIN board_posts p ON p.board_id=b.id AND p.user_id<>? AND " + self._VISIBLE_POST +
            "WHERE b.archived_at IS NULL GROUP BY b.id ORDER BY b.slug", (since or "", user_id, user_id, user_id)).fetchall()
        active, empty = [], []
        for row in rows:
            if not words & set(row["topic_key"].split()) and row["category"] not in words:
                continue
            if row["total"] == 0:
                empty.append({"slug": row["slug"], "title": row["title"]})
            elif row["fresh"]:
                active.append({"slug": row["slug"], "title": row["title"], "new_posts": row["fresh"]})
        return active[:10], empty[:5]

    def owner_activity(self, connection, user_id):
        return [dict(row) for row in connection.execute(
            "SELECT p.id,b.slug AS board,p.body,p.created_at,p.withdrawn_at,p.hidden_at,"
            "(SELECT COUNT(*) FROM board_replies r WHERE r.post_id=p.id AND r.user_id<>p.user_id AND r.withdrawn_at IS NULL AND r.hidden_at IS NULL) AS replies "
            "FROM board_posts p JOIN topic_boards b ON b.id=p.board_id WHERE p.user_id=? ORDER BY p.created_at DESC LIMIT 200", (user_id,))]

    def export_owner(self, user_id, connection):
        return {"posts": self.owner_activity(connection, user_id),
                "replies": [dict(row) for row in connection.execute(
                    "SELECT id,post_id,body,created_at,withdrawn_at,hidden_at FROM board_replies WHERE user_id=? ORDER BY created_at", (user_id,))],
                "boards_started": [dict(row) for row in connection.execute(
                    "SELECT slug,title,created_at FROM topic_boards WHERE creator_user_id=? ORDER BY created_at", (user_id,))],
                "content_reports": [dict(row) for row in connection.execute(
                    "SELECT target_kind,target_id,reason,details,created_at FROM board_content_reports WHERE reporter_id=? ORDER BY created_at", (user_id,))]}
