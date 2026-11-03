"""Private, exact role approvals and operator-confirmed goal leadership changes."""
import json
import secrets

from . import agent_texts, growth_config
from .agent_common import AgentError, clean_text, require_approval, require_owner
from .goal_sessions import _close
from .goals import _object
from .referrals import early_access_active
from .sessions import storage_time
from .topic_boards import topic_key


def quarantine_leadership(connection, now):
    """Invalidate pending role choices in the caller's private quarantine."""
    for table in ("goal_lead_approvals", "goal_lead_handovers"):
        connection.execute(f"UPDATE {table} SET state='revoked',decided_at=? WHERE state='pending'", (storage_time(now),))


def export_owner(connection, user_id):
    # No other person's private consent, account identity or eligibility data.
    return {
        "lead_role_approvals": [dict(row) for row in connection.execute(
            "SELECT g.slug,a.handle,a.charter_revision,a.notice_version,a.approved_at,a.state,a.decided_at "
            "FROM goal_lead_approvals a JOIN goals g ON g.id=a.goal_id WHERE a.user_id=? ORDER BY g.slug", (user_id,))],
        "lead_handovers": [dict(row) for row in connection.execute(
            "SELECT g.slug,h.from_handle,h.target_handle,h.charter_revision,h.approved_at,h.state,h.decided_at "
            "FROM goal_lead_handovers h JOIN goals g ON g.id=h.goal_id WHERE h.user_id=? ORDER BY g.slug", (user_id,))],
    }


class GoalLeadershipService:
    def __init__(self, database, clock, goals, members, work):
        self.database, self.clock, self.goals, self.members, self.work = database, clock, goals, members, work

    def _goal(self, connection, slug):
        goal = self.goals._goal(connection, slug)
        if goal["state"] not in ("open", "paused") or goal["approved_at"] is None or goal["review_pending"] or goal["charter_json"] is None:
            raise AgentError(409, "Leadership changes require an open or paused goal with a reviewed current charter.")
        if not self.goals.charter_public(connection, goal):
            raise AgentError(409, "Restore the current charter's public visibility before changing leadership.")
        return goal

    def _context(self, connection, user_id, slug):
        config = self.goals._feature(connection, user_id)
        require_owner(connection, user_id)
        self.goals._not_held(connection)
        self.goals._pause_inactive(connection, config, self.clock())
        self.goals._visible(self.goals._goal(connection, slug), user_id)
        goal = self._goal(connection, slug)
        return goal, config

    def _eligible(self, connection, goal, user_id, config):
        member = self.members.member(connection, goal, user_id)
        charter = json.loads(goal["charter_json"])
        if not self.members._eligibility(connection, user_id, charter, json.loads(member["capabilities_json"]), config)["eligible"]:
            raise AgentError(409, "The proposed lead no longer meets this goal's joining criteria.")
        if not early_access_active(connection, user_id, self.clock()):
            raise AgentError(403, "A launch lead needs current early access.")
        led = connection.execute("SELECT COUNT(*) FROM goals WHERE lead_user_id=? AND state IN ('open','proposed') AND id!=?", (user_id, goal["id"])).fetchone()[0]
        if led >= config["goals_led_per_member"]:
            raise AgentError(409, "The proposed lead has reached the open or proposed goal limit.")
        return member

    @staticmethod
    def _revision(goal, supplied):
        if type(supplied) is not int or supplied != goal["charter_revision"]:
            raise AgentError(409, "The charter changed. Read the current scope and obtain fresh approval.")

    def _handover(self, connection, goal, target_user_id):
        handover = connection.execute("SELECT * FROM goal_lead_handovers WHERE goal_id=? AND state='pending'", (goal["id"],)).fetchone()
        if (handover is None or handover["user_id"] != goal["lead_user_id"] or handover["from_handle"] != goal["lead_handle"] or
                handover["target_user_id"] != target_user_id or handover["charter_revision"] != goal["charter_revision"]):
            raise AgentError(409, "The current lead must first approve handing this exact charter and role to this contributor.")
        require_owner(connection, handover["user_id"])
        return handover

    @staticmethod
    def _statement(goal, member):
        return agent_texts.GOAL_LEAD_ROLE_STATEMENT.format(**{
            "goal title": json.loads(goal["charter_json"])["title"], "revision": goal["charter_revision"], "handle": member["handle"]})

    def _preview(self, connection, user_id, goal, config):
        member = self._eligible(connection, goal, user_id, config)
        handover = self._handover(connection, goal, user_id) if goal["state"] == "open" else None
        return {"goal": goal["slug"], "handle": member["handle"], "charter_revision": goal["charter_revision"],
                "notice_version": agent_texts.GOAL_LEAD_ROLE_VERSION, "statement": self._statement(goal, member)}, handover

    def preview(self, user_id, slug):
        with self.database.transaction() as connection:
            goal, config = self._context(connection, user_id, slug)
            return self._preview(connection, user_id, goal, config)[0]

    def volunteer(self, user_id, slug, body):
        self.goals.require_feature(user_id)
        _object(body, "lead role approval", {"charter_revision", "approved_by_owner", "notice_version"})
        require_approval(body["approved_by_owner"])
        with self.database.transaction() as connection:
            goal, config = self._context(connection, user_id, slug)
            self._revision(goal, body["charter_revision"])
            if body["notice_version"] != agent_texts.GOAL_LEAD_ROLE_VERSION:
                raise AgentError(409, "Show the current lead-role statement and obtain fresh approval.")
            view, handover = self._preview(connection, user_id, goal, config)
            stamp = storage_time(self.clock())
            connection.execute(
                "INSERT INTO goal_lead_approvals(id,goal_id,user_id,handle,charter_revision,previous_lead_handle,handover_id,"
                "notice_version,approved_at,state) VALUES(?,?,?,?,?,?,?,?,?,'pending') "
                "ON CONFLICT(goal_id,user_id) DO UPDATE SET id=excluded.id,handle=excluded.handle,charter_revision=excluded.charter_revision,"
                "previous_lead_handle=excluded.previous_lead_handle,handover_id=excluded.handover_id,"
                "notice_version=excluded.notice_version,approved_at=excluded.approved_at,state='pending',decided_at=NULL",
                (secrets.token_hex(16), goal["id"], user_id, view["handle"], goal["charter_revision"], goal["lead_handle"],
                 handover["id"] if handover else None, view["notice_version"], stamp))
            return {**view, "approved_at": stamp, "state": "pending"}

    def handover(self, user_id, slug, body):
        self.goals.require_feature(user_id)
        _object(body, "lead handover", {"handle", "charter_revision", "approved_by_owner"})
        require_approval(body["approved_by_owner"])
        with self.database.transaction() as connection:
            goal, config = self._context(connection, user_id, slug)
            self._revision(goal, body["charter_revision"])
            if goal["lead_user_id"] != user_id:
                raise AgentError(403, "Only this goal's current lead can approve a handover.")
            if not isinstance(body["handle"], str):
                raise AgentError(422, "Choose a contributor handle from this goal.")
            target = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND handle=?", (goal["id"], body["handle"])).fetchone()
            if target is None or target["user_id"] == user_id:
                raise AgentError(422, "Choose another active contributor in this goal.")
            self._eligible(connection, goal, target["user_id"], config)
            stamp = storage_time(self.clock())
            connection.execute(
                "INSERT INTO goal_lead_handovers(id,goal_id,user_id,target_user_id,from_handle,target_handle,charter_revision,approved_at,state) "
                "VALUES(?,?,?,?,?,?,?,?,'pending') ON CONFLICT(goal_id) DO UPDATE SET id=excluded.id,user_id=excluded.user_id,target_user_id=excluded.target_user_id,"
                "from_handle=excluded.from_handle,target_handle=excluded.target_handle,charter_revision=excluded.charter_revision,"
                "approved_at=excluded.approved_at,state='pending',decided_at=NULL",
                (secrets.token_hex(16), goal["id"], user_id, target["user_id"], goal["lead_handle"], target["handle"], goal["charter_revision"], stamp))
            return {"goal": slug, "handle": target["handle"], "charter_revision": goal["charter_revision"], "state": "pending_operator", "approved_at": stamp}

    def withdraw(self, user_id, slug):
        """Revocation remains possible without a work lease or active feature."""
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            self.goals._not_held(connection)
            goal = self.goals._goal(connection, slug)
            if connection.execute("SELECT 1 FROM goal_lead_approvals WHERE goal_id=? AND user_id=?", (goal["id"], user_id)).fetchone() is None:
                self.goals._visible(goal, user_id)
            connection.execute("UPDATE goal_lead_approvals SET state='revoked',decided_at=? WHERE goal_id=? AND user_id=? AND state='pending'",
                               (storage_time(self.clock()), goal["id"], user_id))
            return {"goal": slug, "withdrawn": True}

    def cancel_handover(self, user_id, slug):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            self.goals._not_held(connection)
            goal = self.goals._goal(connection, slug)
            self.goals._visible(goal, user_id)
            if goal["lead_user_id"] != user_id:
                raise AgentError(403, "Only the current lead can cancel their handover request.")
            connection.execute("UPDATE goal_lead_handovers SET state='revoked',decided_at=? WHERE goal_id=? AND user_id=? AND state='pending'",
                               (storage_time(self.clock()), goal["id"], user_id))
            return {"goal": slug, "cancelled": True}

    def assign(self, slug, handle, reason):
        reason = clean_text(reason, "reason", 1000, minimum=5, multiline=True)
        now = self.clock()
        with self.database.transaction() as connection:
            self.goals._not_held(connection)
            config = growth_config.load(connection)
            self.goals._pause_inactive(connection, config, now)
            goal = self._goal(connection, slug)
            member = connection.execute("SELECT * FROM goal_members WHERE goal_id=? AND handle=?", (goal["id"], handle)).fetchone() if isinstance(handle, str) else None
            if member is None:
                raise AgentError(404, "Contributor not found.")
            self._eligible(connection, goal, member["user_id"], config)
            approval = connection.execute("SELECT * FROM goal_lead_approvals WHERE goal_id=? AND user_id=? AND state='pending'", (goal["id"], member["user_id"])).fetchone()
            if (approval is None or approval["handle"] != member["handle"] or approval["charter_revision"] != goal["charter_revision"] or
                    approval["previous_lead_handle"] != goal["lead_handle"] or approval["notice_version"] != agent_texts.GOAL_LEAD_ROLE_VERSION):
                raise AgentError(409, "The proposed lead needs fresh approval of this exact current role and charter.")
            if goal["state"] == "open":
                handover = self._handover(connection, goal, member["user_id"])
                if approval["handover_id"] != handover["id"]:
                    raise AgentError(409, "The proposed lead must approve the current handover request.")
            self.goals._topic_available(connection, topic_key(json.loads(goal["charter_json"])["title"]), goal["id"])
            if connection.execute("SELECT COUNT(*) FROM goals WHERE state='open' AND id!=?", (goal["id"],)).fetchone()[0] >= config["goals_open_max"]:
                raise AgentError(409, "The site has reached its open-goal limit.")
            if goal["tier"] == "sensitive" and connection.execute("SELECT COUNT(*) FROM goals WHERE state='open' AND tier='sensitive' AND id!=?", (goal["id"],)).fetchone()[0] >= config["goals_sensitive_open_max"]:
                raise AgentError(409, "Sensitive goals exceed the current pilot capacity.")
            stamp = storage_time(now)
            for owner in {goal["lead_user_id"], member["user_id"]} - {None}:
                connection.execute("UPDATE goal_allocations SET stopped_at=COALESCE(stopped_at,?),stop_reason='lead_changed',show_first_name=0,"
                    "lead_notice_version=NULL,lead_approved_at=NULL,approval_id=lower(hex(randomblob(16))) WHERE goal_id=? AND user_id=?", (stamp, goal["id"], owner))
                connection.execute("UPDATE goal_members SET show_first_name=0,work_generation=work_generation+1 WHERE goal_id=? AND user_id=?", (goal["id"], owner))
                _close(connection, "goal_id=? AND user_id=?", (goal["id"], owner), now, "lead_changed")
            connection.execute("UPDATE goal_lead_approvals SET state=CASE WHEN user_id=? THEN 'consumed' ELSE 'revoked' END,decided_at=? WHERE goal_id=? AND state='pending'", (member["user_id"], stamp, goal["id"]))
            connection.execute("UPDATE goal_lead_handovers SET state='consumed',decided_at=? WHERE goal_id=? AND state='pending'", (stamp, goal["id"]))
            connection.execute("UPDATE goals SET lead_user_id=?,lead_handle=?,state='open',last_lead_step_at=? WHERE id=?", (member["user_id"], member["handle"], stamp, goal["id"]))
            self.goals._append(connection, goal, "operator", "operator", {"action": "assign_lead", "handle": member["handle"], "reason": reason}, now)
            return {"goal": self.goals._view(connection, self.goals._goal(connection, slug), operator=True)}
