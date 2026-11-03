"""Lead task maintenance, cited summaries and short session notes.

Create accepts ``{session_id, task}``. Update accepts ``{session_id, reason,
finding_ids, task?, state?}``; terminal tasks cannot be reopened. Summary accepts
``{session_id, text, finding_ids, disputed}``, with disputed citations separate.
Note accepts ``{text}`` against the session id in its URL. Standing T3L permits
task maintenance only during active work, and summaries during completion grace.
"""
import json
import secrets

from .agent_common import AgentError
from .goal_record import canonical_json
from .goals import _object, _text, validate_task
from .sessions import storage_time


def _finding_refs(connection, goal, values, *, states):
    """Resolve public same-goal findings, never author's private exceptions."""
    from .goal_findings import public_finding_content
    if (not isinstance(values, list) or len(values) > 100 or
            any(not isinstance(value, str) for value in values) or len(set(values)) != len(values)):
        raise AgentError(422, "finding references must be a list of at most 100 distinct finding ids.")
    for entry_id in values:
        entry = connection.execute("SELECT * FROM goal_entries WHERE id=? AND goal_id=? AND kind='finding'",
                                   (entry_id, goal["id"])).fetchone()
        if entry is None or not public_finding_content(connection, entry, states=states):
            raise AgentError(422, "Every cited finding must be public and belong to this goal.")
    return list(values)


class GoalTaskService:
    def __init__(self, database, clock, goals, sessions):
        self.database, self.clock, self.goals, self.sessions = database, clock, goals, sessions

    def _lead(self, connection, user_id, slug, session_id, *, completion=False):
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            raise AgentError(404, "Goal session not found.")
        goal = self.goals._goal(connection, slug)
        if completion:
            session = self.sessions.require_session(connection, user_id, session_id, kind="summary")
            if session["goal_id"] != goal["id"] or goal["lead_user_id"] != user_id:
                raise AgentError(404, "Goal session not found.")
            # require_session rechecks current separate lead consent for a lead,
            # as well as the charter, membership and bound allocation identity.
        else:
            session = self.sessions.require_lead_session(connection, user_id, goal["id"], session_id)
        member = self.sessions.members.member(connection, goal, user_id)
        return goal, member, session

    def _entry(self, connection, goal, member, session, user_id, kind, content, *, refs=()):
        content = {**content, "written_by": "assistant"}
        entry = self.goals._append(connection, goal, kind, member["handle"], content, self.clock(),
                                   refs=refs, author_user_id=user_id, session_id=session["id"])
        if kind in ("step", "summary"):
            connection.execute("UPDATE goals SET last_lead_step_at=? WHERE id=?", (storage_time(self.clock()), goal["id"]))
        return entry

    def create(self, user_id, slug, body):
        """Add a full task with ``{session_id, task}`` under current T3L."""
        self.goals.require_feature(user_id)
        _object(body, "task creation", {"session_id", "task"})
        task = validate_task(body["task"])
        with self.database.transaction() as connection:
            goal, member, session = self._lead(connection, user_id, slug, body["session_id"])
            count = connection.execute("SELECT COUNT(*) FROM goal_tasks WHERE goal_id=? AND state='open'", (goal["id"],)).fetchone()[0]
            if count >= 50:
                raise AgentError(409, "This goal already has 50 open tasks. Close or supersede one first.")
            task_id = "t_" + secrets.token_hex(12)
            entry = self._entry(connection, goal, member, session, user_id, "step",
                                {"action": "task_added", "task_id": task_id, "task": task})
            connection.execute("INSERT INTO goal_tasks(id,goal_id,author_user_id,state,task_json,created_at) VALUES(?,?,?,'open',?,?)",
                               (task_id, goal["id"], user_id, canonical_json(task), storage_time(self.clock())))
            return {"task": {"id": task_id, "state": "open", **task}, "entry": entry}

    def update(self, user_id, slug, task_id, body):
        """Change/close/supersede with a reason and public same-goal finding ids."""
        self.goals.require_feature(user_id)
        _object(body, "task update", {"session_id", "reason", "finding_ids"}, {"task", "state"})
        if not ({"task", "state"} & set(body)):
            raise AgentError(422, "Supply the changed task or its new state.")
        reason = _text(body["reason"], "task change reason", 600, multiline=True)
        task = validate_task(body["task"]) if "task" in body else None
        state = body.get("state", "open")
        if not isinstance(state, str) or state not in ("open", "closed", "superseded"):
            raise AgentError(422, "Task state must be open, closed or superseded.")
        with self.database.transaction() as connection:
            goal, member, session = self._lead(connection, user_id, slug, body["session_id"])
            old = connection.execute("SELECT * FROM goal_tasks WHERE id=? AND goal_id=?", (task_id, goal["id"])).fetchone()
            if old is None:
                raise AgentError(404, "Task not found.")
            if old["state"] != "open" or old["task_json"] is None:
                raise AgentError(409, "Only an existing open task can be changed.")
            from .goal_findings import finding_states
            states = finding_states(connection, goal["id"])
            refs = _finding_refs(connection, goal, body["finding_ids"], states=states)
            if state == "closed" and not refs:
                raise AgentError(422, "Closing a task needs the findings that answered it.")
            if state == "open" and (task is None or task == json.loads(old["task_json"])):
                raise AgentError(409, "The task has not changed.")
            action = {"open": "task_changed", "closed": "task_closed", "superseded": "task_superseded"}[state]
            content = {"action": action, "task_id": task_id, "reason": reason, "finding_ids": refs}
            if task is not None:
                content["task"] = task
            entry = self._entry(connection, goal, member, session, user_id, "step", content, refs=refs)
            stored = task if task is not None else json.loads(old["task_json"])
            connection.execute("UPDATE goal_tasks SET state=?,task_json=?,author_user_id=?,closed_at=? WHERE id=?",
                               (state, canonical_json(stored), user_id,
                                None if state == "open" else storage_time(self.clock()), task_id))
            return {"task": {"id": task_id, "state": state, **stored}, "entry": entry}

    def summary(self, user_id, slug, body):
        """Append ``{session_id,text,finding_ids,disputed}``; never cache prose."""
        self.goals.require_feature(user_id)
        _object(body, "summary", {"session_id", "text", "finding_ids", "disputed"})
        text = _text(body["text"], "summary text", 3000, multiline=True)
        with self.database.transaction() as connection:
            goal, member, session = self._lead(connection, user_id, slug, body["session_id"], completion=True)
            from .goal_findings import finding_states
            states = finding_states(connection, goal["id"])
            findings = _finding_refs(connection, goal, body["finding_ids"], states=states)
            disputed = _finding_refs(connection, goal, body["disputed"], states=states)
            if set(findings) & set(disputed):
                raise AgentError(422, "A finding must appear in only one summary citation list.")
            if any(states[entry_id] == "disputed" for entry_id in findings):
                raise AgentError(422, "Disputed findings belong only in the separate disputed list.")
            if any(states[entry_id] != "disputed" for entry_id in disputed):
                raise AgentError(422, "The disputed list may contain only currently disputed findings.")
            entry = self._entry(connection, goal, member, session, user_id, "summary",
                                {"text": text, "finding_ids": findings, "disputed": disputed}, refs=[*findings, *disputed])
            return {"summary": entry}

    def note(self, user_id, session_id, body):
        """Append a contact-free note of at most 1500 characters within T3."""
        self.goals.require_feature(user_id)
        _object(body, "session note", {"text"})
        text = _text(body["text"], "note text", 1500, multiline=True)
        with self.database.transaction() as connection:
            session = self.sessions.require_session(connection, user_id, session_id, kind="note")
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (session["goal_id"],)).fetchone()
            member = self.sessions.members.member(connection, goal, user_id)
            return {"entry": self._entry(connection, goal, member, session, user_id, "note", {"text": text})}
