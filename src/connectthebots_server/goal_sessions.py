"""Owner-approved goal time allowances and bounded work-session windows.

``until`` includes that UTC date. Permission ends at the following midnight;
both the lease and its five-minute submission grace are capped there. Each
start permanently reserves the full approved minutes in rolling seven-day
accounting, including shortened, stopped and self-reported zero-minute work.
These are permission windows, never measurements of an assistant's compute.
"""
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import secrets

from . import agent_texts, growth_config
from .agent_common import AgentError, clean_text, require_approval, require_owner
from .sessions import storage_time


WINDOW = timedelta(days=7)
GRACE = timedelta(minutes=5)
WINDOW_NOTICE = agent_texts.GOAL_WINDOW_NOTICE
_ALLOCATION_FIELDS = {"sessions_per_week", "minutes_per_session", "until", "show_first_name", "notice_version",
                      "approved_by_owner", "charter_revision", "lead_notice_version", "lead_approved_by_owner"}
_UNSET = object()


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _close(connection, where, params, now, reason):
    stamp = storage_time(now)
    return connection.execute(
        "UPDATE goal_sessions SET ended_at=CASE WHEN ends_at<? THEN ends_at ELSE ? END,end_reason=?,"
        "grace_ends_at=CASE WHEN grace_ends_at>? THEN ? ELSE grace_ends_at END "
        "WHERE (end_reason IS NULL OR (end_reason='expired' AND grace_ends_at>?)) AND " + where,
        (stamp, stamp, reason, stamp, stamp, stamp, *params)).rowcount


def revoke_owner(connection, user_id, now, reason):
    """Revoke work in the caller's protected transaction; retain budget history."""
    stamp = storage_time(now)
    allocations = connection.execute(
        "UPDATE goal_allocations SET stopped_at=COALESCE(stopped_at,?),stop_reason=?,show_first_name=0,"
        "approval_id=lower(hex(randomblob(16))) WHERE user_id=?",
        (stamp, reason, user_id)).rowcount
    connection.execute("UPDATE goal_members SET show_first_name=0,work_generation=work_generation+1 WHERE user_id=?", (user_id,))
    sessions = _close(connection, "user_id=?", (user_id,), now, reason)
    return {"allocations_stopped": allocations, "sessions_ended": sessions}


def revoke_goal(connection, goal_id, now, reason):
    """Cancel active work/grace without renewing or deleting standing approval."""
    return {"sessions_ended": _close(connection, "goal_id=?", (goal_id,), now, reason)}


def quarantine_work(connection, now):
    stamp = storage_time(now)
    allocations = connection.execute(
        "UPDATE goal_allocations SET stopped_at=COALESCE(stopped_at,?),stop_reason='recovery',show_first_name=0,"
        "approval_id=lower(hex(randomblob(16)))", (stamp,)).rowcount
    connection.execute("UPDATE goal_members SET show_first_name=0,work_generation=work_generation+1")
    sessions = _close(connection, "1=1", (), now, "recovery")
    return {"allocations_stopped": allocations, "sessions_ended": sessions}


def export_owner(connection, user_id, now=None):
    allocations = [dict(row) for row in connection.execute(
        "SELECT a.*,g.slug FROM goal_allocations a JOIN goals g ON g.id=a.goal_id WHERE a.user_id=? ORDER BY g.slug", (user_id,))]
    for row in allocations:
        row["active"] = False if row["stopped_at"] is not None else (
            row["permission_expires_at"] > storage_time(now) if now is not None else None)
    return {"allocations": allocations,
            "sessions": [dict(row) for row in connection.execute(
                "SELECT s.*,g.slug FROM goal_sessions s JOIN goals g ON g.id=s.goal_id WHERE s.user_id=? ORDER BY s.started_at,s.id", (user_id,))]}


class GoalSessionService:
    def __init__(self, database, clock, goals, members):
        self.database, self.clock, self.goals, self.members = database, clock, goals, members

    @staticmethod
    def _response(goal, result):
        return {**result, "banner": agent_texts.GOAL_SENSITIVE_BANNER} if goal["tier"] == "sensitive" else result

    def _context(self, connection, user_id, slug, *, open_only=False, check_eligibility=True):
        config = self.goals._feature(connection, user_id)
        require_owner(connection, user_id)
        self.goals._not_held(connection)
        self.goals._pause_inactive(connection, config, self.clock())
        goal = self.goals._goal(connection, slug)
        self.goals._visible(goal, user_id)
        if not self.goals.charter_public(connection, goal):
            raise AgentError(409, "This goal's current charter is unavailable for work or time permission.")
        member = self.members.member(connection, goal, user_id)
        if member["state"] != "active":
            raise AgentError(409, "An active goal membership is required.")
        if open_only and goal["state"] != "open":
            raise AgentError(409, "This goal is not open for work.")
        if check_eligibility:
            eligible = self.members.eligibility(user_id, slug, connection=connection)
            if not eligible["eligible"]:
                raise AgentError(409, "Your agent no longer meets this goal's joining criteria.")
        return goal, member, config

    @staticmethod
    def _stored(connection, goal_id, user_id):
        return connection.execute("SELECT * FROM goal_allocations WHERE goal_id=? AND user_id=?", (goal_id, user_id)).fetchone()

    @staticmethod
    def _allocation_view(row):
        if row is None:
            return None
        return {key: row[key] for key in ("sessions_per_week", "minutes_per_session", "until", "permission_expires_at",
                "notice_version", "approved_at", "charter_revision", "show_first_name", "lead_notice_version", "lead_approved_at",
                "stopped_at", "stop_reason")}

    def _validate(self, connection, user_id, goal, config, body, *, approval):
        if not isinstance(body, dict) or set(body) - _ALLOCATION_FIELDS:
            raise AgentError(422, "Send only the documented allocation fields.")
        if approval:
            require_approval(body.get("approved_by_owner"))
            if body.get("notice_version") != agent_texts.GOAL_CONTRIBUTION_VERSION:
                raise AgentError(409, "Show the current goal contribution statement and obtain fresh approval.")
        revision = body.get("charter_revision", goal["charter_revision"] if not approval else None)
        if type(revision) is not int or revision != goal["charter_revision"]:
            raise AgentError(409, "The goal charter changed. Read its current scope and obtain fresh approval.")
        for key, low, high in (("sessions_per_week", 1, config["goal_sessions_per_week_max"]),
                               ("minutes_per_session", 5, config["goal_session_minutes_max"])):
            if type(body.get(key)) is not int or not low <= body[key] <= high:
                raise AgentError(422, f"{key} must be a whole number from {low} to {high}.")
        raw = body.get("until")
        try:
            until = date.fromisoformat(raw) if isinstance(raw, str) and len(raw) == 10 else None
        except ValueError:
            until = None
        today = self.clock().astimezone(timezone.utc).date()
        if until is None or until.isoformat() != raw or not today <= until <= today + timedelta(days=90):
            raise AgentError(422, "until must be a UTC date from today to 90 days ahead, written YYYY-MM-DD.")
        if type(body.get("show_first_name")) is not bool:
            raise AgentError(422, "show_first_name must be true or false.")
        first_name = None
        if body["show_first_name"]:
            profile = connection.execute("SELECT first_name FROM member_profiles WHERE user_id=?", (user_id,)).fetchone()
            first_name = profile["first_name"] if profile else None
            if not first_name:
                raise AgentError(409, "Choose your first name in your private profile before opting to show it on a goal.")
        lead = goal["lead_user_id"] == user_id
        if approval and lead:
            require_approval(body.get("lead_approved_by_owner"))
            if body.get("lead_notice_version") != agent_texts.GOAL_LEAD_VERSION:
                raise AgentError(409, "Show the separate current lead statement and obtain fresh approval.")
        if not lead and ("lead_notice_version" in body or "lead_approved_by_owner" in body):
            raise AgentError(422, "Only this goal's lead can approve its lead permission.")
        total = connection.execute(
            "SELECT COALESCE(SUM(sessions_per_week*minutes_per_session),0) FROM goal_allocations "
            "WHERE user_id=? AND goal_id!=? AND stopped_at IS NULL AND permission_expires_at>?",
            (user_id, goal["id"], storage_time(self.clock()))).fetchone()[0]
        if total + body["sessions_per_week"] * body["minutes_per_session"] > config["goal_weekly_minutes_total_max"]:
            raise AgentError(422, "Your combined weekly goal allowances exceed the site's weekly minutes limit.")
        return {"sessions_per_week": body["sessions_per_week"], "minutes_per_session": body["minutes_per_session"],
                "until": raw, "permission_expires_at": storage_time(datetime.combine(until + timedelta(days=1), time.min, timezone.utc)),
                "show_first_name": body["show_first_name"], "charter_revision": revision,
                "first_name": first_name, "is_lead": lead}

    def _proposal(self, connection, user_id, goal, member, config, body):
        if goal["state"] not in ("open", "paused") or goal["approved_at"] is None:
            raise AgentError(409, "Only a listed open or paused goal can receive a new time allowance.")
        values = self._validate(connection, user_id, goal, config, body, approval=False)
        title = json.loads(goal["charter_json"])["title"]
        choice = (agent_texts.GOAL_FIRST_NAME_ON.format(**{"first name": values["first_name"]})
                  if values["show_first_name"] else agent_texts.GOAL_FIRST_NAME_OFF)
        statement = agent_texts.GOAL_CONTRIBUTION_STATEMENT.format(
            **{"goal title": title, "n": values["sessions_per_week"], "m": values["minutes_per_session"],
               "date": values["until"], "handle": member["handle"], "first_name_choice": choice})
        proposed = {key: values[key] for key in ("sessions_per_week", "minutes_per_session", "until", "show_first_name", "charter_revision")}
        proposed["notice_version"] = agent_texts.GOAL_CONTRIBUTION_VERSION
        if values["is_lead"]:
            proposed["lead_notice_version"] = agent_texts.GOAL_LEAD_VERSION
        current = self._stored(connection, goal["id"], user_id)
        return self._response(goal, {"proposed": proposed, "permission_expires_at": values["permission_expires_at"],
                "expected_approval_id": current["approval_id"] if current is not None else None,
                "expected_work_generation": member["work_generation"],
                "statement": statement, "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION,
                "lead_statement": agent_texts.GOAL_LEAD_STATEMENT.format(**{"goal title": title}) if values["is_lead"] else None,
                "lead_notice_version": agent_texts.GOAL_LEAD_VERSION if values["is_lead"] else None,
                "charter_revision": goal["charter_revision"], "notice": WINDOW_NOTICE})

    def preview_allocation(self, user_id, slug, body):
        with self.database.transaction() as connection:
            goal, member, config = self._context(connection, user_id, slug)
            return self._proposal(connection, user_id, goal, member, config, body)

    def allocation(self, user_id, slug):
        with self.database.transaction() as connection:
            goal, member, config = self._context(connection, user_id, slug, check_eligibility=False)
            self._expire(connection, user_id, self.clock())
            row = self._stored(connection, goal["id"], user_id)
            defaults = ({key: row[key] for key in ("sessions_per_week", "minutes_per_session", "until", "show_first_name")}
                        if row is not None and row["permission_expires_at"] > storage_time(self.clock()) else
                        {"sessions_per_week": min(3, config["goal_sessions_per_week_max"]),
                         "minutes_per_session": min(20, config["goal_session_minutes_max"]),
                         "until": (self.clock().astimezone(timezone.utc).date() + timedelta(days=30)).isoformat(), "show_first_name": False})
            defaults["show_first_name"] = bool(defaults["show_first_name"])
            # A suggested allowance must fit the remaining aggregate capacity.
            try:
                proposal = self._proposal(connection, user_id, goal, member, config, defaults)
            except AgentError:
                proposal = {"notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION,
                            "lead_notice_version": agent_texts.GOAL_LEAD_VERSION if goal["lead_user_id"] == user_id else None,
                            "charter_revision": goal["charter_revision"], "notice": WINDOW_NOTICE}
            latest = connection.execute("SELECT * FROM goal_sessions WHERE goal_id=? AND user_id=? ORDER BY started_at DESC,id DESC LIMIT 1", (goal["id"], user_id)).fetchone()
            current = latest is not None and (latest["end_reason"] is None or latest["end_reason"] == "expired" and latest["grace_ends_at"] > storage_time(self.clock()))
            return self._response(goal, {"allocation": self._allocation_view(row), "current_session": self._session_view(latest) if current else None,
                    "last_session": self._session_view(latest) if latest is not None else None, **proposal})

    def set_allocation(self, user_id, slug, body, *, expected_approval_id=_UNSET, expected_statement=_UNSET,
                       expected_work_generation=_UNSET):
        with self.database.transaction() as connection:
            goal, member, config = self._context(connection, user_id, slug)
            if goal["state"] not in ("open", "paused") or goal["approved_at"] is None:
                raise AgentError(409, "Only a listed open or paused goal can receive a new time allowance.")
            old = self._stored(connection, goal["id"], user_id)
            if expected_approval_id is not _UNSET and expected_approval_id != (old["approval_id"] if old is not None else None):
                raise AgentError(409, "This allowance changed or was stopped. Read it again and confirm a fresh preview.")
            if expected_work_generation is not _UNSET and (type(expected_work_generation) is not int or
                    expected_work_generation != member["work_generation"]):
                raise AgentError(409, "Goal work was stopped after this preview. Read and confirm a fresh preview.")
            values = self._validate(connection, user_id, goal, config, body, approval=True)
            if expected_statement is not _UNSET and expected_statement != self._proposal(connection, user_id, goal, member, config, body)["statement"]:
                raise AgentError(409, "The contribution statement changed. Read and confirm a fresh preview.")
            now = self.clock()
            # Every replacement is a new approval identity; old sessions/grace
            # cannot inherit it, including when a replacement increases time.
            _close(connection, "goal_id=? AND user_id=?", (goal["id"], user_id), now, "allocation_replaced")
            connection.execute(
                "INSERT INTO goal_allocations(goal_id,user_id,sessions_per_week,minutes_per_session,until,permission_expires_at,"
                "notice_version,approved_at,approval_id,charter_revision,show_first_name,lead_notice_version,lead_approved_at,stopped_at,stop_reason) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(goal_id,user_id) DO UPDATE SET "
                "sessions_per_week=excluded.sessions_per_week,minutes_per_session=excluded.minutes_per_session,until=excluded.until,"
                "permission_expires_at=excluded.permission_expires_at,notice_version=excluded.notice_version,approved_at=excluded.approved_at,"
                "approval_id=excluded.approval_id,charter_revision=excluded.charter_revision,show_first_name=excluded.show_first_name,"
                "lead_notice_version=excluded.lead_notice_version,lead_approved_at=excluded.lead_approved_at,stopped_at=NULL,stop_reason=NULL",
                (goal["id"], user_id, values["sessions_per_week"], values["minutes_per_session"], values["until"], values["permission_expires_at"],
                 agent_texts.GOAL_CONTRIBUTION_VERSION, storage_time(now), secrets.token_hex(16), values["charter_revision"], int(values["show_first_name"]),
                 agent_texts.GOAL_LEAD_VERSION if values["is_lead"] else None, storage_time(now) if values["is_lead"] else None))
            connection.execute("UPDATE goal_members SET show_first_name=? WHERE goal_id=? AND user_id=?",
                               (int(values["show_first_name"]), goal["id"], user_id))
            row = self._stored(connection, goal["id"], user_id)
            return {"allocation": self._allocation_view(row), **self._proposal(connection, user_id, goal, member, config, body)}

    def stop(self, user_id, slug, *, connection=None, reason="owner_stop"):
        if connection is None:
            with self.database.transaction() as active:
                return self.stop(user_id, slug, connection=active, reason=reason)
        require_owner(connection, user_id)
        self.goals._not_held(connection)
        goal = self.goals._goal(connection, slug)
        if connection.execute("SELECT 1 FROM goal_members WHERE goal_id=? AND user_id=?", (goal["id"], user_id)).fetchone() is None:
            self.goals._visible(goal, user_id)
        now = self.clock()
        stopped = connection.execute("UPDATE goal_allocations SET stopped_at=COALESCE(stopped_at,?),stop_reason=?,show_first_name=0,"
                                     "approval_id=lower(hex(randomblob(16))) WHERE goal_id=? AND user_id=?",
                                     (storage_time(now), reason, goal["id"], user_id)).rowcount
        connection.execute("UPDATE goal_members SET show_first_name=0,work_generation=work_generation+1 WHERE goal_id=? AND user_id=?", (goal["id"], user_id))
        ended = _close(connection, "goal_id=? AND user_id=?", (goal["id"], user_id), now, reason)
        return self._response(goal, {"stopped": True, "allocations_stopped": stopped, "sessions_ended": ended})

    def stop_all(self, user_id, *, connection=None, reason="stop_all"):
        if connection is None:
            with self.database.transaction() as active:
                return self.stop_all(user_id, connection=active, reason=reason)
        require_owner(connection, user_id)
        self.goals._not_held(connection)
        sensitive = connection.execute("SELECT 1 FROM goal_members m JOIN goals g ON g.id=m.goal_id WHERE m.user_id=? AND g.tier='sensitive' LIMIT 1", (user_id,)).fetchone()
        result = {"stopped": True, **revoke_owner(connection, user_id, self.clock(), reason)}
        return {**result, "banner": agent_texts.GOAL_SENSITIVE_BANNER} if sensitive else result

    @staticmethod
    def _expire(connection, user_id, now):
        stamp = storage_time(now)
        connection.execute("UPDATE goal_sessions SET ended_at=ends_at,end_reason='expired',minutes_used=NULL "
                           "WHERE user_id=? AND ended_at IS NULL AND ends_at<=?", (user_id, stamp))
        # Permission expiry removes any remaining grace even if the lease had
        # already auto-closed. Its fixed deadline cannot be extended by restore.
        connection.execute("UPDATE goal_sessions SET end_reason='permission_expired',grace_ends_at=ends_at "
            "WHERE user_id=? AND end_reason='expired' AND EXISTS(SELECT 1 FROM goal_allocations a "
            "WHERE a.goal_id=goal_sessions.goal_id AND a.user_id=goal_sessions.user_id AND a.permission_expires_at<=?)", (user_id, stamp))

    def _current_permission(self, connection, user_id, goal, member, now, *, session=None):
        row = self._stored(connection, goal["id"], user_id)
        if row is None or row["stopped_at"] is not None or row["permission_expires_at"] <= storage_time(now):
            raise AgentError(409, "A current approved time allowance is required.")
        if row["notice_version"] != agent_texts.GOAL_CONTRIBUTION_VERSION or row["charter_revision"] != goal["charter_revision"]:
            raise AgentError(409, "The statement or charter changed. Obtain fresh approval before continuing.")
        config = growth_config.load(connection)
        if row["sessions_per_week"] > config["goal_sessions_per_week_max"] or row["minutes_per_session"] > config["goal_session_minutes_max"]:
            raise AgentError(409, "The site limits changed. Choose a new allowance within the current limits.")
        if goal["lead_user_id"] == user_id and (row["lead_notice_version"] != agent_texts.GOAL_LEAD_VERSION or row["lead_approved_at"] is None):
            raise AgentError(409, "This lead needs the separate current lead permission.")
        if session is not None and row["approval_id"] != session["allocation_approval_id"]:
            raise AgentError(409, "This session's permission was replaced.")
        return row

    @staticmethod
    def _session_view(row):
        return {key: row[key] for key in ("id", "task_id", "started_at", "ends_at", "grace_ends_at", "ended_at",
                "minutes_used", "did", "end_reason", "reserved_minutes", "findings_count", "reviews_count", "charter_revision")}

    def start(self, user_id, slug, body):
        if not isinstance(body, dict) or set(body) - {"task_id"}:
            raise AgentError(422, "Send an optional task_id only.")
        if "task_id" in body and (not isinstance(body["task_id"], str) or not body["task_id"]):
            raise AgentError(422, "task_id must identify one open task.")
        now = self.clock()
        with self.database.transaction() as connection:
            goal, member, config = self._context(connection, user_id, slug, open_only=True)
            self._expire(connection, user_id, now)
            allocation = self._current_permission(connection, user_id, goal, member, now)
            slot = connection.execute("SELECT id FROM goal_sessions WHERE user_id=? AND "
                "(end_reason IS NULL OR (end_reason='expired' AND grace_ends_at>?)) LIMIT 1", (user_id, storage_time(now))).fetchone()
            if slot:
                raise AgentError(409, "Another goal session, including its submission grace, already occupies your session slot.")
            rows = connection.execute("SELECT goal_id,started_at,reserved_minutes FROM goal_sessions WHERE user_id=? AND started_at>? ORDER BY started_at,id",
                                      (user_id, storage_time(now - WINDOW))).fetchall()
            waits = []
            own = [row for row in rows if row["goal_id"] == goal["id"]]
            if len(own) >= allocation["sessions_per_week"]:
                waits.append(_parse(own[len(own) - allocation["sessions_per_week"]]["started_at"]) + WINDOW)
            total = sum(row["reserved_minutes"] for row in rows)
            remaining = total + allocation["minutes_per_session"]
            if remaining > config["goal_weekly_minutes_total_max"]:
                for row in rows:
                    remaining -= row["reserved_minutes"]
                    if remaining <= config["goal_weekly_minutes_total_max"]:
                        waits.append(_parse(row["started_at"]) + WINDOW)
                        break
                if remaining > config["goal_weekly_minutes_total_max"]:
                    raise AgentError(409, "The current site minutes limit is lower than this allowance. Choose a smaller allowance.")
            if waits:
                retry = max(1, math.ceil((max(waits) - now).total_seconds()))
                raise AgentError(429, "Your rolling seven-day session or reserved-minutes budget is used. Try again when the oldest required reservation expires.", retry)
            capabilities = set(json.loads(member["capabilities_json"]))
            # Retained linkage counts all findings, including entries whose
            # cleartext was erased. Neither visibility nor erasure redistributes
            # the same recent work as if it had never happened.
            from .goal_findings import finding_states
            states = finding_states(connection, goal["id"])
            tasks = []
            for task_row in connection.execute(
                    "SELECT t.*,COUNT(e.id) AS findings_7d FROM goal_tasks t "
                    "LEFT JOIN goal_findings f ON f.task_id=t.id "
                    "LEFT JOIN goal_entries e ON e.id=f.entry_id AND e.created_at>? "
                    "WHERE t.goal_id=? AND t.state='open' AND t.task_json IS NOT NULL "
                    "GROUP BY t.id ORDER BY findings_7d,t.last_worked_at,t.id",
                    (storage_time(now - WINDOW), goal["id"])):
                task_view = self.goals.task_projection(connection, goal, task_row, states=states)
                if task_view is not None and capabilities.intersection(task_view["capabilities"]):
                    tasks.append(task_row)
            requested = body.get("task_id")
            if requested is not None:
                tasks = [row for row in tasks if row["id"] == requested]
            if not tasks:
                raise AgentError(409, "No open task matches the agent's declared capabilities.")
            task = tasks[0]
            ends = min(now + timedelta(minutes=allocation["minutes_per_session"]), _parse(allocation["permission_expires_at"]))
            grace = min(ends + GRACE, _parse(allocation["permission_expires_at"]))
            session_id = "s_" + secrets.token_hex(16)
            connection.execute("INSERT INTO goal_sessions(id,goal_id,user_id,task_id,allocation_approval_id,charter_revision,started_at,ends_at,grace_ends_at,reserved_minutes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (session_id, goal["id"], user_id, task["id"], allocation["approval_id"], goal["charter_revision"], storage_time(now), storage_time(ends), storage_time(grace), allocation["minutes_per_session"]))
            connection.execute("UPDATE goal_tasks SET last_worked_at=? WHERE id=?", (storage_time(now), task["id"]))
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (session_id,)).fetchone()
            return self._context_view(connection, user_id, goal, row)

    def _context_view(self, connection, user_id, goal, row):
        """Current public work plus eligible review offers, without read counters."""
        from .goal_findings import finding_states, review_offers
        states = finding_states(connection, goal["id"])
        task = connection.execute("SELECT * FROM goal_tasks WHERE id=? AND goal_id=?", (row["task_id"], goal["id"])).fetchone()
        task_view = self.goals.task_projection(connection, goal, task, states=states) if task is not None else None
        return self._response(goal, {"session": self._session_view(row), "session_id": row["id"], "ends_at": row["ends_at"],
                "task": task_view, "reviews": review_offers(connection, user_id, goal, limit=10, now=self.clock()),
                "summary": self.goals.latest_visible_summary(connection, goal), "notice": WINDOW_NOTICE})

    def context(self, user_id, session_id):
        """Recover current task/reviews/summary for an owner's active work lease.

        Completion grace permits submissions only, so it does not permit a new
        context read. This is fresh projection rather than an idempotency cache.
        """
        with self.database.transaction() as connection:
            row = self.require_session(connection, user_id, session_id, kind="work")
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (row["goal_id"],)).fetchone()
            return self._context_view(connection, user_id, goal, row)

    def require_session(self, connection, user_id, session_id, kind=None, consume=False):
        if kind not in (None, "work", "finding", "review", "note", "summary"):
            raise AgentError(422, "Choose a supported session submission kind.")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            raise AgentError(404, "Session not found.")
        now = self.clock()
        self._expire(connection, user_id, now)
        row = connection.execute("SELECT * FROM goal_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if row is None:
            raise AgentError(404, "Session not found.")
        goal = connection.execute("SELECT * FROM goals WHERE id=?", (row["goal_id"],)).fetchone()
        if goal is None:
            raise AgentError(404, "Goal not found.")
        goal, member, config = self._context(connection, user_id, goal["slug"], open_only=True)
        self._current_permission(connection, user_id, goal, member, now, session=row)
        completion = kind in ("finding", "review", "note", "summary")
        if row["end_reason"] not in (None, "expired") or now >= _parse(row["grace_ends_at"] if completion else row["ends_at"]):
            raise AgentError(409, "This session no longer accepts work or submissions.")
        if consume and kind in ("finding", "review"):
            counter = "findings_count" if kind == "finding" else "reviews_count"
            limit = config["goal_findings_per_session"] if kind == "finding" else config["goal_reviews_per_session"]
            if row[counter] >= limit:
                raise AgentError(429, "This session has reached its submission limit.", max(1, math.ceil((_parse(row["grace_ends_at"]) - now).total_seconds())))
            connection.execute("UPDATE goal_sessions SET " + counter + "=" + counter + "+1 WHERE id=?", (row["id"],))
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (row["id"],)).fetchone()
        return row

    def require_lead_session(self, connection, user_id, goal_id, session_id=None):
        goal = connection.execute("SELECT * FROM goals WHERE id=? AND lead_user_id=?", (goal_id, user_id)).fetchone()
        if goal is None:
            raise AgentError(404, "Goal not found.")
        if session_id is None:
            row = connection.execute("SELECT id FROM goal_sessions WHERE goal_id=? AND user_id=? AND end_reason IS NULL ORDER BY started_at DESC LIMIT 1", (goal_id, user_id)).fetchone()
            if row is None:
                raise AgentError(409, "An active lead work session is required.")
            session_id = row["id"]
        row = self.require_session(connection, user_id, session_id, kind="work")
        if row["goal_id"] != goal_id:
            raise AgentError(404, "Session not found.")
        return row

    def end(self, user_id, session_id, body):
        if not isinstance(body, dict) or set(body) != {"minutes_used", "did"}:
            raise AgentError(422, "Send minutes_used and a one-line did report.")
        did = clean_text(body["did"], "did", 300)
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            self.goals._not_held(connection)
            self._expire(connection, user_id, now)
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
            if row is None:
                raise AgentError(404, "Session not found.")
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (row["goal_id"],)).fetchone()
            limit = int((_parse(row["ends_at"]) - _parse(row["started_at"])).total_seconds() // 60)
            used = body["minutes_used"]
            if type(used) is not int or not 0 <= used <= limit:
                raise AgentError(422, f"minutes_used must be a whole number from 0 to {limit}.")
            if row["end_reason"] == "completed":
                if row["minutes_used"] == used and row["did"] == did:
                    return self._response(goal, {"session": self._session_view(row), "notice": WINDOW_NOTICE})
                raise AgentError(409, "This session already has a completion report.")
            if row["end_reason"] not in (None, "expired") or now >= _parse(row["grace_ends_at"]):
                raise AgentError(409, "This session has ended and no longer accepts a completion report.")
            connection.execute("UPDATE goal_sessions SET ended_at=?,minutes_used=?,did=?,end_reason='completed',grace_ends_at=? WHERE id=?",
                (storage_time(min(now, _parse(row["ends_at"]))), used, did, storage_time(min(now, _parse(row["grace_ends_at"]))), session_id))
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (session_id,)).fetchone()
            return self._response(goal, {"session": self._session_view(row), "notice": WINDOW_NOTICE})

    def dashboard(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            self._expire(connection, user_id, self.clock())
            config = growth_config.load(connection)
            from .referrals import early_access_active
            enabled = config["feature.goals"] == "everyone" or (config["feature.goals"] == "early_access" and early_access_active(connection, user_id, self.clock()))
            if enabled:
                self.goals._pause_inactive(connection, config, self.clock())
            rows = connection.execute("SELECT g.*,m.handle,m.state AS membership_state FROM goal_members m JOIN goals g ON g.id=m.goal_id WHERE m.user_id=? ORDER BY g.slug", (user_id,)).fetchall()
            goals, active_count = [], 0
            for row in rows:
                allocation = self._stored(connection, row["id"], user_id)
                sessions = connection.execute("SELECT * FROM goal_sessions WHERE goal_id=? AND user_id=? AND started_at>? ORDER BY started_at,id",
                    (row["id"], user_id, storage_time(self.clock() - WINDOW))).fetchall()
                recent = connection.execute("SELECT id,kind,status,created_at FROM goal_entries WHERE goal_id=? AND author_user_id=? ORDER BY seq DESC LIMIT 5", (row["id"], user_id)).fetchall()
                from .goal_findings import finding_states
                states = finding_states(connection, row["id"])
                active = bool(allocation is not None and allocation["stopped_at"] is None and
                    allocation["permission_expires_at"] > storage_time(self.clock()) and allocation["notice_version"] == agent_texts.GOAL_CONTRIBUTION_VERSION
                    and allocation["charter_revision"] == row["charter_revision"] and row["membership_state"] == "active")
                view = self._allocation_view(allocation)
                if view is not None:
                    view["active"] = active
                active_count += int(active)
                current = next((item for item in reversed(sessions) if item["end_reason"] is None or
                    item["end_reason"] == "expired" and item["grace_ends_at"] > storage_time(self.clock())), None)
                from .goal_members import pending_members
                pending = pending_members(connection, row, user_id)
                charter_available = self.goals.charter_public(connection, row)
                own_pending_lead = connection.execute("SELECT 1 FROM goal_lead_approvals WHERE goal_id=? AND user_id=? AND state='pending'", (row["id"], user_id)).fetchone() is not None
                own_pending_handover = connection.execute("SELECT 1 FROM goal_lead_handovers WHERE goal_id=? AND user_id=? AND state='pending'", (row["id"], user_id)).fetchone() is not None
                title = (json.loads(row["charter_json"])["title"] if row["charter_json"] else "Goal content withdrawn") if self.goals.charter_readable(connection, row, user_id) else "Charter unavailable"
                goal = {"slug": row["slug"], "title": title, "charter_available": charter_available,
                    "own_pending_lead_approval": own_pending_lead, "own_pending_handover": own_pending_handover,
                    "state": row["state"], "tier": row["tier"], "banner": self.goals._view(connection, row, user_id).get("banner"),
                    "handle": row["handle"], "is_lead": row["lead_user_id"] == user_id, "membership_state": row["membership_state"],
                    "allocation": view, "charter_revision": row["charter_revision"],
                    "usage": {"sessions_used": len(sessions), "sessions_allowed": allocation["sessions_per_week"] if allocation is not None else 0,
                              "reported_minutes": sum(item["minutes_used"] or 0 for item in sessions),
                              "reserved_minutes": sum(item["reserved_minutes"] for item in sessions)},
                    "current_session": self._session_view(current) if current is not None else None,
                    "review_pending": bool(row["review_pending"]) if row["lead_user_id"] == user_id else False,
                    "recent_entries": [{**dict(item), **({"finding_status": states.get(item["id"], item["status"])} if item["kind"] == "finding" else {}),
                                        "description": {"charter": "Charter revision", "step": "Task update", "membership": "Membership update", "finding": "Finding", "review": "Review", "summary": "Summary", "note": "Note"}.get(item["kind"], "Goal entry")} for item in recent],
                    "pending_members": pending}
                goals.append(goal)
            return {"enabled": enabled, "goals": goals, "active_count": active_count, "notice": WINDOW_NOTICE}
