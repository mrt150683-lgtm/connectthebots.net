"""Pseudonymous common-goal memberships and deterministic joining rules.

Eligibility looks only at the asking owner's share-tier answers. Lead approval
receives declared abilities and an owner-approved note, never interview data.
"""
from datetime import datetime, timedelta
import json
import re
import secrets

from . import growth_config
from .agent_common import AgentError, normalize_item, require_approval, require_owner
from .agent_texts import GOAL_CONTRIBUTION_VERSION, GOAL_SENSITIVE_BANNER
from .goal_record import canonical_json
from .goals import CAPABILITIES, INTEREST_TOPICS, _object, _text
from .sessions import storage_time


def _goal_id(goal):
    return goal if type(goal) is int else goal["id"]


def ensure_lead_member(connection, goal, now):
    """Create the lead's membership without inferring consent to work."""
    if goal["lead_user_id"] is None:
        return None
    connection.execute(
        "INSERT OR IGNORE INTO goal_members(goal_id,user_id,handle,state,capabilities_json,joined_at,charter_revision) "
        "VALUES(?,?,?,'active','[]',?,?)",
        (goal["id"], goal["lead_user_id"], goal["lead_handle"], storage_time(now), goal["charter_revision"]))
    return connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND user_id=?", (goal["id"], goal["lead_user_id"])).fetchone()


def member_count(connection, goal_id):
    return connection.execute(
        "SELECT COUNT(*) FROM goal_members m JOIN users u ON u.id=m.user_id WHERE m.goal_id=? AND m.state='active' AND u.suspended_at IS NULL",
        (_goal_id(goal_id),)).fetchone()[0]


def pending_members(connection, goal, viewer_id):
    if viewer_id is None or goal["lead_user_id"] != viewer_id:
        return []
    return [{"handle": row["handle"], "capabilities": json.loads(row["capabilities_json"]), "note": row["note"]}
            for row in connection.execute(
                "SELECT m.handle,m.capabilities_json,m.note FROM goal_members m JOIN users u ON u.id=m.user_id "
                "WHERE m.goal_id=? AND m.state='pending' AND u.suspended_at IS NULL ORDER BY m.joined_at,m.handle", (goal["id"],))]


def display_author(connection, goal, user_id, now):
    """A name is a live opt-in, never part of an immutable entry envelope."""
    goal_id = _goal_id(goal)
    row = connection.execute(
        "SELECT m.handle,m.state,m.show_first_name,u.suspended_at FROM goal_members m JOIN users u ON u.id=m.user_id "
        "WHERE m.goal_id=? AND m.user_id=?", (goal_id, user_id)).fetchone()
    if row is None:
        return None
    result = {"handle": row["handle"]}
    if row["state"] != "active" or not row["show_first_name"] or row["suspended_at"] is not None:
        return result
    allocation = connection.execute(
        "SELECT 1 FROM goal_allocations a JOIN goals g ON g.id=a.goal_id WHERE a.goal_id=? AND a.user_id=? "
        "AND a.stopped_at IS NULL AND a.show_first_name=1 AND a.permission_expires_at>? "
        "AND a.notice_version=? AND a.charter_revision=g.charter_revision",
        (goal_id, user_id, storage_time(now), GOAL_CONTRIBUTION_VERSION)).fetchone()
    if allocation:
        profile = connection.execute("SELECT first_name FROM member_profiles WHERE user_id=?", (user_id,)).fetchone()
        if profile and profile["first_name"]:
            result["first_name"] = profile["first_name"]
    return result


def export_memberships(connection, user_id):
    return [{"goal": row["slug"], "handle": row["handle"], "state": row["state"],
             "capabilities": json.loads(row["capabilities_json"]), "note": row["note"],
             "show_first_name": bool(row["show_first_name"]), "joined_at": row["joined_at"],
             "decided_at": row["decided_at"], "charter_revision": row["charter_revision"], "work_generation": row["work_generation"]}
            for row in connection.execute("SELECT m.*,g.slug FROM goal_members m JOIN goals g ON g.id=m.goal_id WHERE m.user_id=? ORDER BY g.slug", (user_id,))]


class GoalMemberService:
    def __init__(self, database, clock, goals, *, sessions=None):
        self.database, self.clock, self.goals, self.sessions = database, clock, goals, sessions

    @staticmethod
    def _response(goal, result):
        return {**result, "banner": GOAL_SENSITIVE_BANNER} if goal["tier"] == "sensitive" else result

    def _context(self, connection, user_id, slug, *, write=False, open_required=False):
        config = self.goals._feature(connection, user_id)
        require_owner(connection, user_id)
        if write:
            self.goals._not_held(connection)
        self.goals._pause_inactive(connection, config, self.clock())
        goal = self.goals._goal(connection, slug)
        self.goals._visible(goal, user_id)
        if open_required and (goal["state"] != "open" or goal["review_pending"]):
            raise AgentError(409, "This goal is not open for joining or membership changes.")
        if goal["charter_json"] is None:
            raise AgentError(409, "This goal needs a current charter and lead.")
        if goal["approved_at"] is not None and not self.goals.charter_public(connection, goal):
            raise AgentError(409, "This goal's current charter is unavailable for membership changes or eligibility checks.")
        return goal, json.loads(goal["charter_json"]), config

    @staticmethod
    def _capabilities(value, *, allow_empty=False):
        minimum = 0 if allow_empty else 1
        if not isinstance(value, list) or not minimum <= len(value) <= len(CAPABILITIES) or any(not isinstance(item, str) or item not in CAPABILITIES for item in value):
            raise AgentError(422, "Declare capabilities from the supported list.")
        if len(set(value)) != len(value):
            raise AgentError(422, "Do not repeat a declared capability.")
        return list(value)

    def eligibility(self, user_id, slug, capabilities=None, *, connection=None):
        if connection is None:
            with self.database.transaction() as opened:
                return self.eligibility(user_id, slug, capabilities, connection=opened)
        goal, charter, config = self._context(connection, user_id, slug)
        if capabilities is None:
            existing = connection.execute("SELECT capabilities_json FROM goal_members WHERE goal_id=? AND user_id=?", (goal["id"], user_id)).fetchone()
            capabilities = json.loads(existing["capabilities_json"]) if existing else []
        capabilities = self._capabilities(capabilities, allow_empty=True)
        return self._response(goal, self._eligibility(connection, user_id, charter, capabilities, config))

    def _eligibility(self, connection, user_id, charter, capabilities, config):
        joining, missing = charter["joining"], []
        age_days = max(config["goal_min_account_age_days"], joining.get("account_age_days", 0))
        created = connection.execute("SELECT created_at FROM users WHERE id=?", (user_id,)).fetchone()
        try:
            created_at = datetime.fromisoformat(created["created_at"].replace("Z", "+00:00"))
            old_enough = created_at.tzinfo is not None and self.clock() - created_at >= timedelta(days=age_days)
        except (TypeError, ValueError, OverflowError):
            old_enough = False
        if not old_enough:
            missing.append(f"Your account needs to be at least {age_days} days old.")
        if joining.get("interview_complete"):
            interview = connection.execute("SELECT completed_at FROM interview_state WHERE user_id=?", (user_id,)).fetchone()
            if interview is None or interview["completed_at"] is None:
                missing.append("Finish the taste interview.")
        required_capabilities = joining.get("capabilities")
        if required_capabilities:
            mode, required = next(iter(required_capabilities.items()))
            matching = [item in capabilities for item in required]
            if not (all(matching) if mode == "all_of" else any(matching)):
                missing.append("Declare the capabilities required by this goal.")
        interests = joining.get("interests")
        if interests:
            # The SQL itself excludes ask/never/skipped data. Do not load every
            # answer into memory and then discard private tiers afterwards.
            topics = tuple(dict.fromkeys(item["topic"] for item in interests if item["topic"] in INTEREST_TOPICS))
            answers = {}
            if topics:
                placeholders = ",".join("?" for _ in topics)
                for row in connection.execute(
                    "SELECT topic,items_json FROM taste_answers WHERE user_id=? AND tier='share' AND skipped=0 AND topic IN (" + placeholders + ")",
                    (user_id, *topics)):
                    answers[row["topic"]] = {normalize_item(item) for item in json.loads(row["items_json"])}
            if not any(normalize_item(item["item"]) in answers.get(item["topic"], set()) for item in interests):
                missing.append("Share a matching interest from the goal's joining rules.")
        if joining.get("track_record", 0) > 0:
            missing.append("Checked-finding track records are not available yet.")
        return {"eligible": not missing, "missing": missing}

    def member(self, connection, goal, user_id):
        require_owner(connection, user_id)
        row = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND user_id=?", (_goal_id(goal), user_id)).fetchone()
        if row is None or row["state"] != "active":
            raise AgentError(403, "Join this goal and wait for any required lead approval first.")
        return row

    def display_author(self, connection, goal, user_id):
        return display_author(connection, goal, user_id, self.clock())

    @staticmethod
    def _view(row):
        return {"handle": row["handle"], "state": row["state"], "capabilities": json.loads(row["capabilities_json"]),
                "note": row["note"], "show_first_name": bool(row["show_first_name"]), "joined_at": row["joined_at"],
                "decided_at": row["decided_at"], "charter_revision": row["charter_revision"]}

    def _record_counts(self, connection, goal, action):
        active = member_count(connection, goal["id"])
        pending = connection.execute(
            "SELECT COUNT(*) FROM goal_members m JOIN users u ON u.id=m.user_id WHERE m.goal_id=? AND m.state='pending' AND u.suspended_at IS NULL",
            (goal["id"],)).fetchone()[0]
        self.goals._append(connection, goal, "membership", "system", {"action": action, "active": active, "pending": pending}, self.clock())

    def join(self, user_id, slug, body):
        self.goals.require_feature(user_id)
        _object(body, "join", {"capabilities"}, {"note", "approved_by_owner"})
        require_approval(body.get("approved_by_owner"))
        capabilities = self._capabilities(body["capabilities"])
        note = _text(body["note"], "note", 300, multiline=True) if body.get("note") is not None else None
        now = self.clock()
        with self.database.transaction() as connection:
            goal, charter, config = self._context(connection, user_id, slug, write=True, open_required=True)
            eligibility = self._eligibility(connection, user_id, charter, capabilities, config)
            if not eligibility["eligible"]:
                raise AgentError(403, "You do not yet meet this goal's joining rules. " + " ".join(eligibility["missing"]))
            existing = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND user_id=?", (goal["id"], user_id)).fetchone()
            removed = existing is not None and existing["state"] == "removed"
            state = "active" if goal["lead_user_id"] == user_id or charter["membership"] == "open" and not removed else "pending"
            if existing is not None and existing["state"] == "active":
                state = "active"
            occupying = existing is not None and existing["state"] in ("active", "pending")
            occupied = connection.execute("SELECT COUNT(*) FROM goal_members WHERE goal_id=? AND state IN ('active','pending')", (goal["id"],)).fetchone()[0]
            if not occupying and occupied >= config["goal_members_max"]:
                raise AgentError(409, "This goal has reached its membership limit.")
            encoded = canonical_json(capabilities)
            if existing is None:
                handle = "c_" + secrets.token_hex(4)
                while connection.execute("SELECT 1 FROM goal_members WHERE goal_id=? AND handle=?", (goal["id"], handle)).fetchone():
                    handle = "c_" + secrets.token_hex(4)
                connection.execute(
                    "INSERT INTO goal_members(goal_id,user_id,handle,state,capabilities_json,note,joined_at,decided_at,charter_revision) VALUES(?,?,?,?,?,?,?,?,?)",
                    (goal["id"], user_id, handle, state, encoded, note, storage_time(now), storage_time(now) if state == "active" else None, goal["charter_revision"]))
                self._record_counts(connection, goal, "joined" if state == "active" else "requested")
            elif (existing["state"], existing["capabilities_json"], existing["note"], existing["charter_revision"]) != (state, encoded, note, goal["charter_revision"]):
                connection.execute(
                    "UPDATE goal_members SET state=?,capabilities_json=?,note=?,joined_at=?,decided_at=?,charter_revision=? WHERE goal_id=? AND user_id=?",
                    (state, encoded, note, existing["joined_at"] if occupying else storage_time(now),
                     storage_time(now) if state == "active" else None, goal["charter_revision"], goal["id"], user_id))
                self._record_counts(connection, goal, "declaration_updated" if occupying else "requested" if state == "pending" else "joined")
            member = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND user_id=?", (goal["id"], user_id)).fetchone()
            return self._response(goal, {"goal": slug, "member": self._view(member)})

    def pending(self, user_id, slug):
        with self.database.transaction() as connection:
            goal, _, _ = self._context(connection, user_id, slug)
            if goal["lead_user_id"] != user_id:
                raise AgentError(404, "Goal not found.")
            return self._response(goal, {"goal": slug, "members": pending_members(connection, goal, user_id)})

    def decide(self, user_id, slug, handle, body):
        self.goals.require_feature(user_id)
        _object(body, "membership decision", {"decision"})
        if body["decision"] not in ("approve", "decline"):
            raise AgentError(422, "decision must be approve or decline.")
        if not isinstance(handle, str) or re.fullmatch(r"c_[0-9a-f]{8}", handle) is None:
            raise AgentError(404, "Membership request not found.")
        with self.database.transaction() as connection:
            goal, charter, config = self._context(connection, user_id, slug, write=True, open_required=True)
            if goal["lead_user_id"] != user_id:
                raise AgentError(404, "Goal not found.")
            sessions = self.sessions
            if sessions is None:
                from .goal_sessions import GoalSessionService
                sessions = GoalSessionService(self.database, self.clock, self.goals, self)
            sessions.require_lead_session(connection, user_id, goal["id"])
            candidate = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND handle=? AND state='pending'", (goal["id"], handle)).fetchone()
            if candidate is None:
                raise AgentError(404, "Membership request not found.")
            require_owner(connection, candidate["user_id"])
            state = "removed"
            if body["decision"] == "approve":
                eligibility = self._eligibility(connection, candidate["user_id"], charter, json.loads(candidate["capabilities_json"]), config)
                if not eligibility["eligible"]:
                    raise AgentError(409, "This contributor no longer meets the goal's joining rules.")
                if member_count(connection, goal["id"]) >= config["goal_members_max"]:
                    raise AgentError(409, "This goal has reached its membership limit.")
                state = "active"
            connection.execute("UPDATE goal_members SET state=?,decided_at=?,charter_revision=? WHERE goal_id=? AND user_id=?",
                               (state, storage_time(self.clock()), goal["charter_revision"], goal["id"], candidate["user_id"]))
            connection.execute("UPDATE goals SET last_lead_step_at=? WHERE id=?", (storage_time(self.clock()), goal["id"]))
            self._record_counts(connection, goal, "approved" if state == "active" else "declined")
            return self._response(goal, {"goal": slug, "member": {"handle": candidate["handle"], "state": state}})

    def export_owner(self, user_id, *, connection=None):
        if connection is None:
            with self.database.connect() as opened:
                return export_memberships(opened, user_id)
        return export_memberships(connection, user_id)
