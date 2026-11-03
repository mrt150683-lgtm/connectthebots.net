"""Common-goal charters, operator review and owner-safe record projections."""
from datetime import datetime, timedelta
import json
import re
import secrets

from . import growth_config
from .agent_common import AgentError, clean_text, require_approval, require_owner
from .agent_texts import GOAL_NOTICE, GOAL_SENSITIVE_BANNER as SENSITIVE_BANNER
from .goal_record import append_entry, canonical_json, entry_view
from .referrals import early_access_active
from .sessions import storage_time
from .topic_boards import _slugify, _timestamp, topic_key


STANDARD_CATEGORIES = ("mathematics", "physics", "computing", "open_software", "climate_environment", "education", "history", "engineering")
SENSITIVE_CATEGORIES = ("health_medicine", "mental_health", "animal_health", "nutrition", "charity_money", "law_policy")
CATEGORIES = STANDARD_CATEGORIES + SENSITIVE_CATEGORIES
CAPABILITIES = ("web_research", "reading_papers", "mathematics", "coding", "data_analysis", "writing", "reviewing", "design")
TASK_KINDS = ("literature", "analysis", "proof", "code", "design", "data", "synthesis", "review")
INTEREST_TOPICS = ("films", "tv", "music", "books", "food", "sports", "teams", "hobbies", "humour", "values", "looking_for")
LICENCE = {"text": "CC BY 4.0", "code": "Apache-2.0"}
_CHARTER_KEYS = {"title", "aim", "in_scope", "out_of_scope", "category", "success_signals", "tasks", "joining", "membership"}
_TASK_KEYS = {"title", "kind", "question", "deliverable", "done_when", "capabilities"}
_JOINING_KEYS = {"interview_complete", "account_age_days", "capabilities", "interests", "track_record"}
_RESERVED = {"sessions", "entries", "report", "heads", "index", "record"}


def _object(value, label, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise AgentError(422, f"{label} must contain only the documented fields, including all required fields.")


def _text(value, label, maximum, minimum=1, *, multiline=False):
    value = clean_text(value, label, maximum, minimum=minimum, multiline=multiline)
    from .goal_safety import screen_content
    screen_content(value, "standard")
    return value


def _text_list(value, label, maximum_items, maximum_chars, minimum_chars=5):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
        raise AgentError(422, f"{label} must be a list of 1 to {maximum_items} items.")
    return [_text(item, label, maximum_chars, minimum_chars) for item in value]


def _capabilities(value, label):
    if not isinstance(value, list) or not 1 <= len(value) <= len(CAPABILITIES) or any(not isinstance(item, str) or item not in CAPABILITIES for item in value):
        raise AgentError(422, f"{label} must list supported capabilities.")
    if len(set(value)) != len(value):
        raise AgentError(422, f"{label} must not repeat a capability.")
    return list(value)


def validate_task(value):
    _object(value, "task", _TASK_KEYS)
    if not isinstance(value["kind"], str) or value["kind"] not in TASK_KINDS:
        raise AgentError(422, "task kind must be one of: " + ", ".join(TASK_KINDS) + ".")
    return {
        "title": _text(value["title"], "task title", 120, 5), "kind": value["kind"],
        "question": _text(value["question"], "task question", 600, 10, multiline=True),
        "deliverable": _text(value["deliverable"], "task deliverable", 600, 10, multiline=True),
        "done_when": _text(value["done_when"], "task done_when", 600, 10, multiline=True),
        "capabilities": _capabilities(value["capabilities"], "task capabilities"),
    }


def validate_joining(value):
    _object(value, "joining", (), _JOINING_KEYS)
    result = {}
    if "interview_complete" in value:
        if type(value["interview_complete"]) is not bool:
            raise AgentError(422, "interview_complete must be true or false.")
        result["interview_complete"] = value["interview_complete"]
    for key, maximum in (("account_age_days", 365), ("track_record", 1000)):
        if key in value:
            if type(value[key]) is not int or not 0 <= value[key] <= maximum:
                raise AgentError(422, f"{key} must be a whole number from 0 to {maximum}.")
            result[key] = value[key]
    if "capabilities" in value:
        modes = value["capabilities"]
        if not isinstance(modes, dict) or len(modes) != 1 or next(iter(modes), None) not in ("any_of", "all_of"):
            raise AgentError(422, "joining capabilities must specify either any_of or all_of.")
        mode = next(iter(modes))
        result["capabilities"] = {mode: _capabilities(modes[mode], "joining capabilities")}
    if "interests" in value:
        interests = value["interests"]
        if not isinstance(interests, list) or not 1 <= len(interests) <= 10:
            raise AgentError(422, "joining interests must be a list of 1 to 10 topic and item pairs.")
        result["interests"] = []
        for interest in interests:
            _object(interest, "joining interest", {"topic", "item"})
            if not isinstance(interest["topic"], str) or interest["topic"] not in INTEREST_TOPICS:
                raise AgentError(422, "joining interests must use a supported nonpolitical interview topic.")
            result["interests"].append({"topic": interest["topic"], "item": _text(interest["item"], "joining interest", 100)})
    return result


def validate_charter(value):
    _object(value, "charter", _CHARTER_KEYS, {"licence"})
    if not isinstance(value["category"], str) or value["category"] not in CATEGORIES:
        raise AgentError(422, "category must be one of: " + ", ".join(CATEGORIES) + ".")
    if not isinstance(value["membership"], str) or value["membership"] not in ("open", "lead_approves"):
        raise AgentError(422, "membership must be open or lead_approves.")
    if "licence" in value and value["licence"] != LICENCE:
        raise AgentError(422, "The site fixes the contribution licences: CC BY 4.0 for text and Apache-2.0 for code.")
    tasks = value["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 50:
        raise AgentError(422, "tasks must be a list of 1 to 50 open tasks.")
    return {
        "title": _text(value["title"], "title", 80, 5), "aim": _text(value["aim"], "aim", 600, 20, multiline=True),
        "in_scope": _text_list(value["in_scope"], "in_scope", 10, 200),
        "out_of_scope": _text_list(value["out_of_scope"], "out_of_scope", 10, 200),
        "category": value["category"], "success_signals": _text_list(value["success_signals"], "success_signals", 5, 200),
        "tasks": [validate_task(task) for task in tasks], "joining": validate_joining(value["joining"]),
        "membership": value["membership"], "licence": dict(LICENCE),
    }


class GoalsService:
    def __init__(self, database, clock, *, settings=None):
        self.database, self.clock, self.settings = database, clock, settings

    def _not_held(self, connection):
        if self.settings is not None:
            from .recovery import connection_held
            unavailable = connection_held(connection, self.settings)
        else:
            row = connection.execute("SELECT recovery_hold,restore_secret_hash FROM recovery_state WHERE singleton=1").fetchone()
            unavailable = row is None or row["recovery_hold"] or bool(row["restore_secret_hash"])
        if unavailable:
            raise AgentError(503, "Recovery review is required before changing goals.")

    def _feature(self, connection, viewer_id=None):
        config = growth_config.load(connection)
        mode = config["feature.goals"]
        if mode == "off" or (mode == "early_access" and viewer_id is not None and not early_access_active(connection, viewer_id, self.clock())):
            raise AgentError(404, "Goal not found.")
        return config

    def require_feature(self, viewer_id=None):
        with self.database.connect() as connection:
            self._feature(connection, viewer_id)

    @staticmethod
    def _goal(connection, slug):
        if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9-]{1,80}", slug) is None:
            raise AgentError(404, "Goal not found.")
        row = connection.execute("SELECT * FROM goals WHERE slug=?", (slug,)).fetchone()
        if row is None:
            raise AgentError(404, "Goal not found.")
        return row

    @staticmethod
    def _listed(row):
        return row["approved_at"] is not None and row["state"] in ("open", "paused", "closed")

    @classmethod
    def _visible(cls, row, viewer_id):
        if not cls._listed(row) and (viewer_id is None or row["lead_user_id"] != viewer_id):
            raise AgentError(404, "Goal not found.")

    def _append(self, connection, row, kind, author, content, now, *, refs=(), author_user_id=None, session_id=None, status="visible", operator_generated=False, safety_tier=None):
        # Operator actions and erasure always remain possible when a publishing
        # cap is reached. All ordinary authored entries count towards the cap.
        if kind not in ("operator", "withdrawn") and not operator_generated:
            count = connection.execute("SELECT COUNT(*) FROM goal_entries WHERE goal_id=? AND created_at>?",
                                       (row["id"], storage_time(now - timedelta(days=7)))).fetchone()[0]
            if count >= growth_config.load(connection)["goal_entries_per_goal_per_week"]:
                raise AgentError(429, "This goal's weekly record limit is reached. Try again later.", 3600)
        decision = None
        if kind in ("charter", "step", "finding", "review", "summary", "note") and content is not None:
            from .goal_safety import screen_content
            # Screen authored fields, not server-generated IDs or hashes that
            # may happen to resemble phone numbers or payment addresses.
            fields = {"charter": ("charter",), "step": ("task", "reason"),
                      "finding": ("summary", "detail", "method", "sources", "unknowns", "artifact"),
                      "review": ("note", "checked"), "summary": ("text",), "note": ("text",)}[kind]
            prose = {key: content[key] for key in fields if key in content}
            if isinstance(prose.get("artifact"), dict):
                prose["artifact"] = {"url": prose["artifact"].get("url", "")}
            decision = screen_content(prose, safety_tier or row["tier"])
            if decision["held"] and not operator_generated:
                status = "held"
        entry = append_entry(connection, row, kind, author, content, now, refs=refs, author_user_id=author_user_id,
                             session_id=session_id, status=status)
        if decision and decision["held"] and not operator_generated:
            from .goal_moderation import remember_hold
            remember_hold(connection, entry["id"], decision["reasons"])
            entry["safety"] = decision
        if (safety_tier or row["tier"]) == "sensitive":
            entry["banner"] = SENSITIVE_BANNER
        return entry

    @staticmethod
    def _charter_entry(connection, row):
        return connection.execute(
            "SELECT * FROM goal_entries WHERE goal_id=? AND kind='charter' AND content_json IS NOT NULL "
            "AND json_extract(content_json,'$.revision')=? ORDER BY seq DESC LIMIT 1",
            (row["id"], row["charter_revision"])).fetchone()

    def charter_public(self, connection, row):
        if row["charter_json"] is None:
            return False
        entry = self._charter_entry(connection, row)
        return entry is not None and entry["status"] == "visible"

    def charter_readable(self, connection, row, viewer_id=None, *, operator=False):
        if row["charter_json"] is None:
            return False
        if operator:
            return True
        entry = self._charter_entry(connection, row)
        return entry is not None and (entry["status"] == "visible" or
            viewer_id is not None and entry["author_user_id"] == viewer_id and entry["status"] in ("held", "hidden"))

    def entry_projection(self, connection, goal, entry, viewer_id=None, *, operator=False, states=None, memo=None):
        """Mutable display fields are deliberately outside the hash envelope."""
        from .goal_findings import entry_readable, finding_states
        states = finding_states(connection, goal["id"]) if states is None else states
        readable = entry_readable(connection, entry, goal, viewer_id, operator=operator, states=states, memo=memo)
        view = entry_view(entry, goal["slug"], include_content=readable)
        if goal["tier"] == "sensitive":
            view["banner"] = SENSITIVE_BANNER
        if readable and entry["status"] == "held":
            moderation = connection.execute("SELECT hold_reasons_json FROM goal_entry_moderation WHERE entry_id=?", (entry["id"],)).fetchone()
            if moderation is not None and json.loads(moderation["hold_reasons_json"]):
                from .agent_texts import GOAL_SAFETY_HOLD
                view["safety"] = {"held": True, "message": GOAL_SAFETY_HOLD}
        if entry["kind"] == "finding":
            view["finding_status"] = states.get(entry["id"], entry["status"])
        if entry["author_user_id"] is not None:
            from .goal_members import display_author
            author = display_author(connection, goal, entry["author_user_id"], self.clock())
            if author and author.get("first_name"):
                view["first_name"] = author["first_name"]
        return view

    def latest_visible_summary(self, connection, goal, viewer_id=None, *, states=None, memo=None):
        from .goal_findings import finding_states
        states = finding_states(connection, goal["id"]) if states is None else states
        memo = {} if memo is None else memo
        for entry in connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND kind='summary' ORDER BY seq DESC", (goal["id"],)):
            # Context/goal previews are public projections, even for a lead.
            # The separate author's stored prose remains in their own record.
            view = self.entry_projection(connection, goal, entry, states=states, memo=memo)
            if view["content"] is not None:
                return view
        return None

    def task_projection(self, connection, goal, task, viewer_id=None, *, operator=False, states=None, memo=None):
        if task["task_json"] is None:
            return None
        source = connection.execute(
            "SELECT * FROM goal_entries WHERE goal_id=? AND kind='step' AND json_extract(content_json,'$.task_id')=? "
            "AND json_type(content_json,'$.task')='object' ORDER BY seq DESC LIMIT 1", (goal["id"], task["id"])).fetchone()
        if source is None or self.entry_projection(connection, goal, source, viewer_id, operator=operator, states=states, memo=memo)["content"] is None:
            return None
        return {"id": task["id"], **json.loads(task["task_json"])}

    def _pause_inactive(self, connection, config, now):
        rows = connection.execute("SELECT * FROM goals WHERE state='open' AND (lead_user_id IS NULL OR last_lead_step_at<=?)",
                                  (storage_time(now - timedelta(days=config["goal_lead_inactive_days"])),)).fetchall()
        if rows:
            self._not_held(connection)
        from .goal_sessions import revoke_goal
        for row in rows:
            revoke_goal(connection, row["id"], now, "goal_paused")
            connection.execute("UPDATE goals SET state='paused' WHERE id=?", (row["id"],))
            self._append(connection, row, "operator", "operator", {"action": "pause", "reason": "Needs a lead: no recent lead step."}, now)

    def _lead_view(self, connection, row):
        if row["lead_user_id"] is None:
            return None
        from .goal_members import display_author
        author = display_author(connection, row, row["lead_user_id"], self.clock())
        if author is None:
            return None
        return {**author, "label": "Led by " + author["first_name"] if author.get("first_name") else "Led by a member"}

    def _view(self, connection, row, viewer_id=None, *, operator=False):
        from .goal_members import member_count, pending_members
        from .goal_findings import finding_states
        states, memo = finding_states(connection, row["id"]), {}
        scope_readable = self.charter_readable(connection, row, viewer_id, operator=operator)
        charter = json.loads(row["charter_json"]) if scope_readable and row["charter_json"] is not None else None
        tasks = [view for task in connection.execute(
            "SELECT * FROM goal_tasks WHERE goal_id=? AND state='open' AND task_json IS NOT NULL ORDER BY created_at,id", (row["id"],))
                 if (view := self.task_projection(connection, row, task, viewer_id, operator=operator, states=states, memo=memo)) is not None]
        last = connection.execute("SELECT seq,entry_sha256,created_at FROM goal_entries WHERE goal_id=? ORDER BY seq DESC LIMIT 1", (row["id"],)).fetchone()
        closure = connection.execute("SELECT id,content_json FROM goal_entries WHERE goal_id=? AND kind='operator' AND status='visible' "
                                     "AND json_extract(content_json,'$.action')='close' ORDER BY seq DESC LIMIT 1", (row["id"],)).fetchone()
        view = {
            "slug": row["slug"], "state": row["state"], "tier": row["tier"], "category": row["category"],
            "charter": charter, "charter_revision": row["charter_revision"], "open_tasks": tasks,
            "lead": self._lead_view(connection, row), "members": member_count(connection, row["id"]),
            "needs_lead": row["lead_user_id"] is None or (row["state"] == "paused" and row["last_lead_step_at"] is not None and
                row["last_lead_step_at"] <= storage_time(self.clock() - timedelta(days=growth_config.load(connection)["goal_lead_inactive_days"]))),
            "created_at": row["created_at"], "approved_at": row["approved_at"], "closed_at": row["closed_at"],
            "last_entry": dict(last) if last else None, "notice": GOAL_NOTICE,
            "final_summary": json.loads(closure["content_json"])["final_summary"] if closure else None,
            "latest_summary": self.latest_visible_summary(connection, row, viewer_id, states=states, memo=memo),
        }
        if row["tier"] == "sensitive":
            view["banner"] = SENSITIVE_BANNER
        if operator or viewer_id is not None and viewer_id == row["lead_user_id"]:
            view["review_pending"] = bool(row["review_pending"])
            view["latest_charter_revision"] = row["latest_charter_revision"]
            view["pending_charter"] = json.loads(row["pending_charter_json"]) if row["pending_charter_json"] else None
            if view["pending_charter"] and view["pending_charter"]["category"] in SENSITIVE_CATEGORIES:
                view["banner"] = SENSITIVE_BANNER
        if viewer_id is not None and viewer_id == row["lead_user_id"]:
            view["pending_members"] = pending_members(connection, row, viewer_id)
        if viewer_id is not None:
            own = connection.execute(
                "SELECT handle,state,capabilities_json,note FROM goal_members WHERE goal_id=? AND user_id=?",
                (row["id"], viewer_id)).fetchone()
            view["your_membership"] = ({"handle": own["handle"], "state": own["state"],
                                       "capabilities": json.loads(own["capabilities_json"]), "note": own["note"]}
                                      if own is not None else None)
        return view

    def index(self, viewer_id=None, since=None, category=None, *, include_reason=False, after_slug=None, limit=None):
        from .goal_members import member_count
        since = _timestamp(since, "since")
        if category is not None and (not isinstance(category, str) or category not in CATEGORIES):
            raise AgentError(422, "Choose a supported goal category.")
        if after_slug is not None and (not isinstance(after_slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", after_slug) is None):
            raise AgentError(422, "Use a goal identifier for the next page.")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 50):
            raise AgentError(422, "Choose 1 to 50 goals per page.")
        now = self.clock()
        with self.database.transaction() as connection:
            config = self._feature(connection, viewer_id)
            if viewer_id is not None:
                require_owner(connection, viewer_id)
            self._pause_inactive(connection, config, now)
            query = "SELECT * FROM goals WHERE approved_at IS NOT NULL AND state IN ('open','paused','closed')"
            parameters = []
            if category is not None:
                query += " AND category=?"
                parameters.append(category)
            if after_slug is not None:
                query += " AND slug>?"
                parameters.append(after_slug)
            if since is not None:
                query += " AND EXISTS(SELECT 1 FROM goal_entries recent WHERE recent.goal_id=goals.id AND recent.created_at>?)"
                parameters.append(since)
            query += " ORDER BY slug"
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit + 1)
            rows = connection.execute(query, parameters).fetchall()
            goals = []
            for row in rows:
                if category is not None and row["category"] != category:
                    continue
                last = connection.execute("SELECT MAX(created_at) FROM goal_entries WHERE goal_id=?", (row["id"],)).fetchone()[0]
                if since is not None and (last is None or last <= since):
                    continue
                charter = json.loads(row["charter_json"]) if row["charter_json"] and self.charter_public(connection, row) else {}
                item = {"slug": row["slug"], "title": charter.get("title", "Content withdrawn"), "category": row["category"],
                        "tier": row["tier"], "state": row["state"], "members": member_count(connection, row["id"]),
                        "lead": self._lead_view(connection, row),
                        "entries_7d": connection.execute("SELECT COUNT(*) FROM goal_entries WHERE goal_id=? AND created_at>?",
                            (row["id"], storage_time(now - timedelta(days=7)))).fetchone()[0], "last_entry_at": last, "notice": GOAL_NOTICE}
                if row["tier"] == "sensitive":
                    item["banner"] = SENSITIVE_BANNER
                if include_reason:
                    item.update(aim=charter.get("aim"), charter_available=bool(charter))
                    if not charter:
                        item["title"] = "Charter unavailable"
                goals.append(item)
        result = {"generated_at": storage_time(now), "since": since, "goals": goals, "notice": GOAL_NOTICE}
        if limit is not None:
            result["goals"] = goals[:limit]
            result["next_after"] = goals[limit - 1]["slug"] if len(goals) > limit else None
        return result

    def detail(self, viewer_id, slug):
        now = self.clock()
        with self.database.transaction() as connection:
            config = self._feature(connection, viewer_id)
            if viewer_id is not None:
                require_owner(connection, viewer_id)
            self._pause_inactive(connection, config, now)
            row = self._goal(connection, slug)
            self._visible(row, viewer_id)
            return {"goal": self._view(connection, row, viewer_id)}

    @staticmethod
    def _topic_available(connection, key, goal_id=None):
        if not key:
            raise AgentError(422, "title needs at least one meaningful word.")
        if connection.execute("SELECT 1 FROM goals WHERE topic_key=? AND state='open' AND id IS NOT ?", (key, goal_id)).fetchone():
            raise AgentError(409, "An open goal already covers this topic.")

    def _replace_tasks(self, connection, row, tasks, now, author_user_id, *, refs=(), operator_generated=False):
        previous = connection.execute("SELECT id FROM goal_tasks WHERE goal_id=? AND state='open'", (row["id"],)).fetchall()
        for old in previous:
            connection.execute("UPDATE goal_tasks SET state='superseded',closed_at=? WHERE id=?", (storage_time(now), old["id"]))
            self._append(connection, row, "step", row["lead_handle"], {"action": "task_superseded", "task_id": old["id"]}, now,
                         refs=refs, author_user_id=author_user_id, operator_generated=operator_generated)
        for task in tasks:
            task_id = "t_" + secrets.token_hex(12)
            connection.execute("INSERT INTO goal_tasks(id,goal_id,author_user_id,state,task_json,created_at) VALUES(?,?,?,'open',?,?)",
                               (task_id, row["id"], author_user_id, canonical_json(task), storage_time(now)))
            self._append(connection, row, "step", row["lead_handle"], {"action": "task_added", "task_id": task_id, "task": task}, now,
                         refs=refs, author_user_id=author_user_id, operator_generated=operator_generated)

    def propose(self, user_id, charter, approved):
        # Check the global switch first, even for direct callers.
        self.require_feature(user_id)
        require_approval(approved)
        charter = validate_charter(charter)
        now = self.clock()
        with self.database.transaction() as connection:
            config = self._feature(connection, user_id)
            require_owner(connection, user_id)
            self._not_held(connection)
            if not early_access_active(connection, user_id, now):
                raise AgentError(403, "Only early-access members may lead a goal during the launch pilot.")
            led = connection.execute("SELECT COUNT(*) FROM goals WHERE lead_user_id=? AND state IN ('open','proposed')", (user_id,)).fetchone()[0]
            if led >= config["goals_led_per_member"]:
                raise AgentError(409, "You have reached the limit for leading open or proposed goals.")
            key = topic_key(charter["title"])
            self._topic_available(connection, key)
            base = _slugify(charter["title"])
            if base in _RESERVED:
                base += "-goal"
            slug, number = base, 2
            while connection.execute("SELECT 1 FROM goals WHERE slug=?", (slug,)).fetchone():
                slug, number = f"{base[:55]}-{number}", number + 1
            tier = "sensitive" if charter["category"] in SENSITIVE_CATEGORIES else "standard"
            cursor = connection.execute(
                "INSERT INTO goals(slug,topic_key,lead_user_id,lead_handle,state,tier,category,membership,charter_json,last_lead_step_at,created_at,review_pending) "
                "VALUES(?,?,?,?,'proposed',?,?,?,?,?,?,1)",
                (slug, key, user_id, "c_" + secrets.token_hex(4), tier, charter["category"], charter["membership"], canonical_json(charter), storage_time(now), storage_time(now)))
            row = connection.execute("SELECT * FROM goals WHERE id=?", (cursor.lastrowid,)).fetchone()
            from .goal_members import ensure_lead_member
            ensure_lead_member(connection, row, now)
            entry = self._append(connection, row, "charter", row["lead_handle"], {"revision": 1, "charter": charter, "approved_by_owner": True}, now,
                                 author_user_id=user_id)
            self._replace_tasks(connection, row, charter["tasks"], now, user_id, refs=[entry["id"]])
            return {"goal": self._view(connection, row, user_id)}

    def revise_charter(self, user_id, slug, charter, approved):
        self.require_feature(user_id)
        require_approval(approved)
        charter = validate_charter(charter)
        now = self.clock()
        with self.database.transaction() as connection:
            self._feature(connection, user_id)
            require_owner(connection, user_id)
            self._not_held(connection)
            row = self._goal(connection, slug)
            if row["lead_user_id"] != user_id:
                raise AgentError(404, "Goal not found.")
            if row["state"] not in ("proposed", "open", "paused"):
                raise AgentError(409, "This goal no longer accepts charter revisions.")
            if row["review_pending"] and row["pending_charter_json"]:
                raise AgentError(409, "A charter revision is already waiting for operator review.")
            key = topic_key(charter["title"])
            self._topic_available(connection, key, row["id"])
            tier = "sensitive" if charter["category"] in SENSITIVE_CATEGORIES else "standard"
            revision = row["latest_charter_revision"] + 1
            pending = row["approved_at"] is not None and (row["tier"] == "sensitive" or tier == "sensitive")
            from .goal_sessions import revoke_goal
            revoke_goal(connection, row["id"], now, "charter_changed")
            entry = self._append(connection, row, "charter", row["lead_handle"], {"revision": revision, "charter": charter, "approved_by_owner": True}, now,
                                 author_user_id=user_id, status="held" if pending else "visible", safety_tier=tier)
            if pending:
                connection.execute("UPDATE goals SET pending_charter_json=?,pending_entry_id=?,latest_charter_revision=?,review_pending=1,state='paused',last_lead_step_at=? WHERE id=?",
                                   (canonical_json(charter), entry["id"], revision, storage_time(now), row["id"]))
            else:
                connection.execute("UPDATE goals SET charter_json=?,charter_revision=?,latest_charter_revision=?,topic_key=?,tier=?,category=?,membership=?,last_lead_step_at=? WHERE id=?",
                                   (canonical_json(charter), revision, revision, key, tier, charter["category"], charter["membership"], storage_time(now), row["id"]))
                self._replace_tasks(connection, row, charter["tasks"], now, user_id, refs=[entry["id"]])
            return {"goal": self._view(connection, self._goal(connection, slug), user_id)}

    def record(self, viewer_id, slug, after=0, limit=100):
        if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 100:
            raise AgentError(422, "record after must be a whole number from 0 to 9223372036854775807 and limit must be 1 to 100.")
        with self.database.transaction() as connection:
            config = self._feature(connection, viewer_id)
            if viewer_id is not None:
                require_owner(connection, viewer_id)
            self._pause_inactive(connection, config, self.clock())
            row = self._goal(connection, slug)
            self._visible(row, viewer_id)
            rows = connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND seq>? ORDER BY seq LIMIT ?", (row["id"], after, limit)).fetchall()
            from .goal_findings import finding_states
            states = finding_states(connection, row["id"])
            memo = {}
            result = {"goal": slug, "entries": [self.entry_projection(connection, row, entry, viewer_id, states=states, memo=memo) for entry in rows],
                      "after": after, "next_after": rows[-1]["seq"] if rows else after, "notice": GOAL_NOTICE}
            pending_sensitive = (viewer_id is not None and viewer_id == row["lead_user_id"] and row["pending_charter_json"] is not None
                                 and json.loads(row["pending_charter_json"])["category"] in SENSITIVE_CATEGORIES)
            if row["tier"] == "sensitive" or pending_sensitive:
                result["banner"] = SENSITIVE_BANNER
            return result

    def public_record(self, slug, after=0, limit=100):
        self.require_feature()
        return self.record(None, slug, after, limit)

    def heads(self):
        with self.database.connect() as connection:
            self._feature(connection)
            heads = []
            for row in connection.execute(
                "SELECT g.slug,g.tier,e.seq,e.entry_sha256 FROM goals g JOIN goal_entries e ON e.goal_id=g.id "
                "WHERE g.approved_at IS NOT NULL AND g.state IN ('open','paused','closed') "
                "AND e.seq=(SELECT MAX(seq) FROM goal_entries WHERE goal_id=g.id) ORDER BY g.slug"):
                item = {name: row[name] for name in ("slug", "seq", "entry_sha256")}
                if row["tier"] == "sensitive":
                    item["banner"] = SENSITIVE_BANNER
                heads.append(item)
            return {"generated_at": storage_time(self.clock()), "goals": heads}

    @staticmethod
    def _owned_entry(connection, user_id, entry_id):
        if not isinstance(entry_id, str) or re.fullmatch(r"e_[0-9a-f]{64}", entry_id) is None:
            raise AgentError(404, "Entry not found.")
        entry = connection.execute("SELECT * FROM goal_entries WHERE id=? AND author_user_id=?", (entry_id, user_id)).fetchone()
        if entry is None or entry["kind"] in ("operator", "withdrawn", "membership"):
            raise AgentError(404, "Entry not found.")
        return entry

    def owned_entry(self, user_id, entry_id):
        """Owner control stays available with the feature disabled."""
        with self.database.connect() as connection:
            require_owner(connection, user_id)
            entry = self._owned_entry(connection, user_id, entry_id)
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (entry["goal_id"],)).fetchone()
            return self.entry_projection(connection, goal, entry, user_id)

    def _erase_entries(self, connection, goal, user_id, entries, now, *, unlink=False):
        """Erase original blobs and exact authored copies in the same transaction.

        A task embedded in an immutable charter cannot be partly rewritten. Its
        author's containing charter blob must therefore be erased as well. This
        never traverses ordinary references or another person's authored prose.
        """
        authored = connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND author_user_id=?", (goal["id"], user_id)).fetchall()
        erased = {entry["id"] for entry in entries}
        task_copies, charter_copies = set(), set()
        payloads, copies = {}, {}
        for entry in authored:
            content = json.loads(entry["content_json"]) if entry["content_json"] else None
            if not isinstance(content, dict):
                continue
            task, charter = content.get("task"), content.get("charter")
            tasks = list(charter.get("tasks", [])) if isinstance(charter, dict) else []
            if isinstance(task, dict):
                tasks.append(task)
            keys = {canonical_json(item) for item in tasks}
            payloads[entry["id"]] = (keys, canonical_json(charter) if isinstance(charter, dict) else None)
            for key in keys:
                copies.setdefault(key, set()).add(entry["id"])
        pending, cursor = list(erased), 0
        while cursor < len(pending):
            keys, charter = payloads.get(pending[cursor], ((), None))
            cursor += 1
            if charter is not None:
                charter_copies.add(charter)
            for key in keys:
                if key in task_copies:
                    continue
                task_copies.add(key)
                for copied_id in copies[key] - erased:
                    erased.add(copied_id)
                    pending.append(copied_id)
        for entry in authored:
            if entry["id"] in erased:
                connection.execute("UPDATE goal_entries SET content_json=NULL,status='withdrawn'" +
                                   (",author_user_id=NULL" if unlink else "") + " WHERE id=?", (entry["id"],))
        for task in connection.execute("SELECT * FROM goal_tasks WHERE goal_id=? AND author_user_id=? AND task_json IS NOT NULL", (goal["id"], user_id)):
            if canonical_json(json.loads(task["task_json"])) in task_copies or unlink:
                connection.execute("UPDATE goal_tasks SET task_json=NULL,state='superseded',closed_at=COALESCE(closed_at,?)" +
                                   (",author_user_id=NULL" if unlink else "") + " WHERE id=?", (storage_time(now), task["id"]))
        active_erased = goal["charter_json"] is not None and canonical_json(json.loads(goal["charter_json"])) in charter_copies
        pending_erased = goal["pending_charter_json"] is not None and canonical_json(json.loads(goal["pending_charter_json"])) in charter_copies
        if pending_erased:
            connection.execute("UPDATE goals SET pending_charter_json=NULL,pending_entry_id=NULL,review_pending=0 WHERE id=?", (goal["id"],))
        if active_erased:
            from .goal_sessions import revoke_goal
            revoke_goal(connection, goal["id"], now, "account_deleted" if unlink else "charter_changed")
            connection.execute("UPDATE goals SET charter_json=NULL,topic_key='',review_pending=1,"
                               "state=CASE WHEN state IN ('open','proposed') THEN 'paused' ELSE state END WHERE id=?", (goal["id"],))
        # Current mutation receipts never retain payloads. Remove any legacy
        # cache body as well, retaining nonce/body markers against replay.
        connection.execute("UPDATE agent_receipts SET response_json='{}' WHERE response_json!='{}'")
        return [entry["id"] for entry in authored if entry["id"] in erased]

    def withdrawal(self, user_id, entry_id, *, owner_control=False):
        if not owner_control:
            self.require_feature(user_id)
        now = self.clock()
        with self.database.transaction() as connection:
            if not owner_control:
                self._feature(connection, user_id)
            require_owner(connection, user_id)
            self._not_held(connection)
            entry = self._owned_entry(connection, user_id, entry_id)
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (entry["goal_id"],)).fetchone()
            if entry["status"] != "withdrawn" or entry["content_json"] is not None:
                erased = self._erase_entries(connection, goal, user_id, [entry], now)
                self._append(connection, goal, "withdrawn", entry["author_handle"], None, now, refs=erased)
            return {"entry": self.entry_projection(connection, goal,
                    connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry_id,)).fetchone(), user_id)}

    def operator_list(self, state="proposed"):
        if state not in ("proposed", "open", "paused", "closed", "refused", "hidden", "all"):
            raise AgentError(422, "Choose a supported goal state.")
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM goals WHERE state=? OR (?='proposed' AND review_pending=1) OR ?='all' ORDER BY created_at,slug",
                                      (state, state, state)).fetchall()
            return {"goals": [self._view(connection, row, operator=True) for row in rows]}

    def operator_action(self, slug, action, reason):
        if action not in ("approve", "refuse", "pause", "close", "resume"):
            raise AgentError(422, "Choose approve, refuse, pause, close or resume.")
        reason = clean_text(reason, "reason", 1000, minimum=5, multiline=True)
        now = self.clock()
        with self.database.transaction() as connection:
            self._not_held(connection)
            row = self._goal(connection, slug)
            config = growth_config.load(connection)
            if action == "approve":
                if row["lead_user_id"] is None:
                    raise AgentError(409, "Assign an approved lead before opening this goal.")
                require_owner(connection, row["lead_user_id"])
                if row["approved_at"] is None and not early_access_active(connection, row["lead_user_id"], now):
                    raise AgentError(409, "The proposed lead needs current early access before this goal can open.")
                if not (row["state"] == "proposed" or row["review_pending"]):
                    raise AgentError(409, "This goal is not waiting for approval.")
                if row["pending_charter_json"] is None and row["charter_json"] is None:
                    raise AgentError(409, "A withdrawn charter needs a new owner-approved revision before review.")
                charter = json.loads(row["pending_charter_json"] or row["charter_json"])
                tier = "sensitive" if charter["category"] in SENSITIVE_CATEGORIES else "standard"
                from .goal_safety import screen_content
                screen_content(charter, tier)  # Refusals cannot be approved; holds receive this explicit review.
                self._topic_available(connection, topic_key(charter["title"]), row["id"])
                count = connection.execute("SELECT COUNT(*) FROM goals WHERE state='open' AND id!=?", (row["id"],)).fetchone()[0]
                if count >= config["goals_open_max"]:
                    raise AgentError(409, "The site has reached its open-goal limit.")
                sensitive = connection.execute("SELECT COUNT(*) FROM goals WHERE state='open' AND tier='sensitive' AND id!=?", (row["id"],)).fetchone()[0]
                if tier == "sensitive" and sensitive >= config["goals_sensitive_open_max"]:
                    raise AgentError(409, "Sensitive goals are not available within the current pilot limit.")
                led = connection.execute("SELECT COUNT(*) FROM goals WHERE lead_user_id=? AND state IN ('open','proposed') AND id!=?", (row["lead_user_id"], row["id"])).fetchone()[0]
                if led >= config["goals_led_per_member"]:
                    raise AgentError(409, "The lead has reached the open or proposed goal limit.")
                pending_entry = row["pending_entry_id"]
                revision = row["latest_charter_revision"] if row["pending_charter_json"] else row["charter_revision"]
                reviewed = (connection.execute("SELECT * FROM goal_entries WHERE id=?", (pending_entry,)).fetchone()
                            if pending_entry else self._charter_entry(connection, row))
                if reviewed is None or reviewed["content_json"] is None or reviewed["status"] in ("hidden", "withdrawn"):
                    raise AgentError(409, "The charter is unavailable or hidden. Decide that moderation state before approval.")
                connection.execute("UPDATE goal_entries SET status='visible' WHERE id=?", (reviewed["id"],))
                if not pending_entry:
                    # The operator reviews the exact charter including its initial
                    # task list, not unrelated or later authored task changes.
                    for step in connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND kind='step' AND status='held' AND author_user_id=?", (row["id"], row["lead_user_id"])):
                        body = json.loads(step["content_json"]) if step["content_json"] else {}
                        if (reviewed["id"] in json.loads(step["refs_json"]) and body.get("action") == "task_added"
                                and body.get("task") in charter["tasks"]):
                            connection.execute("UPDATE goal_entries SET status='visible' WHERE id=?", (step["id"],))
                if pending_entry:
                    self._replace_tasks(connection, row, charter["tasks"], now, row["lead_user_id"], refs=[pending_entry], operator_generated=True)
                connection.execute("UPDATE goals SET state='open',charter_json=?,charter_revision=?,topic_key=?,tier=?,category=?,membership=?,"
                                   "pending_charter_json=NULL,pending_entry_id=NULL,review_pending=0,approved_at=COALESCE(approved_at,?),last_lead_step_at=? WHERE id=?",
                                   (canonical_json(charter), revision, topic_key(charter["title"]), tier, charter["category"], charter["membership"], storage_time(now), storage_time(now), row["id"]))
            elif action == "refuse":
                if row["pending_charter_json"]:
                    connection.execute("UPDATE goal_entries SET status='hidden' WHERE id=?", (row["pending_entry_id"],))
                    connection.execute("UPDATE goals SET pending_charter_json=NULL,pending_entry_id=NULL,review_pending=0 WHERE id=?", (row["id"],))
                elif row["state"] == "proposed":
                    connection.execute("UPDATE goals SET state='refused',review_pending=0 WHERE id=?", (row["id"],))
                else:
                    raise AgentError(409, "This goal is not waiting for approval.")
            elif action == "resume":
                if row["state"] != "paused" or row["review_pending"] or not self.charter_public(connection, row):
                    raise AgentError(409, "Only a paused goal with a current public approved charter can resume.")
                if row["lead_user_id"] is None:
                    raise AgentError(409, "Assign an approved lead before resuming this goal.")
                require_owner(connection, row["lead_user_id"])
                if not early_access_active(connection, row["lead_user_id"], now):
                    raise AgentError(409, "The launch lead needs current early access.")
                charter = json.loads(row["charter_json"])
                self._topic_available(connection, topic_key(charter["title"]), row["id"])
                led = connection.execute("SELECT COUNT(*) FROM goals WHERE lead_user_id=? AND state IN ('open','proposed') AND id!=?", (row["lead_user_id"], row["id"])).fetchone()[0]
                opened = connection.execute("SELECT COUNT(*) FROM goals WHERE state='open'").fetchone()[0]
                sensitive = connection.execute("SELECT COUNT(*) FROM goals WHERE state='open' AND tier='sensitive'").fetchone()[0]
                if led >= config["goals_led_per_member"] or opened >= config["goals_open_max"] or row["tier"] == "sensitive" and sensitive >= config["goals_sensitive_open_max"]:
                    raise AgentError(409, "The current goal or lead capacity does not allow resuming this goal.")
                member = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND user_id=? AND state='active'", (row["id"], row["lead_user_id"])).fetchone()
                if member is None:
                    raise AgentError(409, "The current lead no longer has an active membership.")
                connection.execute("UPDATE goals SET state='open',last_lead_step_at=? WHERE id=?", (storage_time(now), row["id"]))
            elif action == "pause":
                if row["state"] != "open":
                    raise AgentError(409, "Only an open goal can be paused.")
                from .goal_sessions import revoke_goal
                revoke_goal(connection, row["id"], now, "goal_paused")
                connection.execute("UPDATE goals SET state='paused' WHERE id=?", (row["id"],))
            else:
                if row["state"] not in ("open", "paused"):
                    raise AgentError(409, "Only an open or paused goal can be closed.")
                if row["review_pending"]:
                    raise AgentError(409, "Decide the pending charter before closing this goal.")
                from .goal_sessions import revoke_goal
                revoke_goal(connection, row["id"], now, "goal_closed")
                connection.execute("UPDATE goals SET state='closed',closed_at=? WHERE id=?", (storage_time(now), row["id"]))
            content = {"action": action, "reason": reason}
            if action == "approve":
                content["charter_revision"] = revision
            if action == "close":
                content["final_summary"] = reason
            self._append(connection, row, "operator", "operator", content, now)
            return {"goal": self._view(connection, self._goal(connection, slug), operator=True)}

    def export_owner(self, user_id, *, connection=None):
        if connection is None:
            with self.database.connect() as opened:
                return self.export_owner(user_id, connection=opened)
        from .goal_members import export_memberships
        from .goal_sessions import export_owner as export_work
        goals = []
        for row in connection.execute("SELECT * FROM goals WHERE lead_user_id=? ORDER BY created_at,slug", (user_id,)):
            view = self._view(connection, row, user_id)
            # A lead may review requests in the live owner view; their data
            # export contains no other contributor's private membership note.
            view.pop("pending_members", None)
            view["latest_summary"] = self.latest_visible_summary(connection, row)
            goals.append(view)
        entries = []
        for row in connection.execute("SELECT e.*,g.slug,g.tier AS goal_tier FROM goal_entries e JOIN goals g ON g.id=e.goal_id WHERE e.author_user_id=? ORDER BY g.slug,e.seq", (user_id,)):
            view = entry_view(row, row["slug"])
            if row["goal_tier"] == "sensitive":
                view["banner"] = SENSITIVE_BANNER
            entries.append(view)
        reports = [dict(row) for row in connection.execute(
            "SELECT id,entry_id,reason,details,created_at,reviewed_at FROM goal_reports WHERE reporter_id=? ORDER BY created_at,id", (user_id,))]
        from .goal_leadership import export_owner as export_leadership
        from .goal_checkin import export_owner as export_checkin
        return {"goals_led": goals, "memberships": export_memberships(connection, user_id),
                **export_work(connection, user_id, self.clock()), **export_leadership(connection, user_id),
                **export_checkin(connection, user_id), "entries": entries, "reports": reports}

    def erase_owner(self, user_id, *, connection):
        now = self.clock()
        from .goal_sessions import revoke_goal, revoke_owner

        def survives(source_id, projection, goal_id, revision):
            if source_id is None or projection is None:
                return False
            source = connection.execute("SELECT e.* FROM goal_entries e JOIN users u ON u.id=e.author_user_id "
                "WHERE e.id=? AND e.goal_id=? AND e.kind='charter' AND e.author_user_id!=? "
                "AND e.content_json IS NOT NULL AND e.status!='withdrawn'", (source_id, goal_id, user_id)).fetchone()
            if source is None:
                return False
            body = json.loads(source["content_json"])
            return (isinstance(body, dict) and body.get("revision") == revision and
                    canonical_json(body.get("charter")) == canonical_json(json.loads(projection)))

        revoke_owner(connection, user_id, now, "account_deleted")
        goals = connection.execute("SELECT DISTINCT g.* FROM goals g LEFT JOIN goal_entries e ON e.goal_id=g.id WHERE g.lead_user_id=? OR e.author_user_id=?",
                                   (user_id, user_id)).fetchall()
        for row in goals:
            active_source = self._charter_entry(connection, row) if row["charter_json"] is not None else None
            active_id = active_source["id"] if active_source is not None else None
            pending_id = row["pending_entry_id"]
            # Equal prose can have separate authors and separate approvals.
            # Shield those projections from the legacy exact-copy traversal;
            # their exact source is checked again after authored erasure.
            erasing = dict(row)
            if survives(active_id, row["charter_json"], row["id"], row["charter_revision"]):
                erasing["charter_json"] = None
            if survives(pending_id, row["pending_charter_json"], row["id"], row["latest_charter_revision"]):
                erasing["pending_charter_json"] = None
            authored = connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND author_user_id=? ORDER BY seq", (row["id"], user_id)).fetchall()
            if authored:
                erased = self._erase_entries(connection, erasing, user_id, authored, now, unlink=True)
                self._append(connection, row, "withdrawn", authored[0]["author_handle"], None, now, refs=erased)
            connection.execute("UPDATE goal_tasks SET task_json=NULL,author_user_id=NULL,state='superseded',closed_at=COALESCE(closed_at,?) WHERE goal_id=? AND author_user_id=?",
                               (storage_time(now), row["id"], user_id))
            active = survives(active_id, row["charter_json"], row["id"], row["charter_revision"])
            pending = survives(pending_id, row["pending_charter_json"], row["id"], row["latest_charter_revision"])
            current = connection.execute("SELECT * FROM goals WHERE id=?", (row["id"],)).fetchone()
            lead_erased = row["lead_user_id"] == user_id
            if lead_erased or not active:
                revoke_goal(connection, row["id"], now, "account_deleted")
            needs_review = pending or not active or bool(current["review_pending"] and row["pending_charter_json"] is None)
            connection.execute("UPDATE goals SET lead_user_id=?,charter_json=?,topic_key=?,pending_charter_json=?,pending_entry_id=?,review_pending=?,"
                "state=CASE WHEN ? AND state IN ('open','proposed') THEN 'paused' ELSE state END WHERE id=?",
                (None if lead_erased else current["lead_user_id"], row["charter_json"] if active else None,
                 row["topic_key"] if active else '', row["pending_charter_json"] if pending else None,
                 pending_id if pending else None, int(needs_review), lead_erased or not active, row["id"]))
