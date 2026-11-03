"""Private goal reports and CLI-only reversible moderation decisions.

Any current account with feature access may report currently public content;
goal membership is not required. Self-reports are refused. Distinct reports
from currently available accounts count toward the shared hiding threshold.
Reports, their details and reporter identities never become public events.
Unhide marks existing reports reviewed. A duplicate cannot re-arm that report;
only new distinct reporters count toward another automatic hiding threshold.
"""
import json
import re
import secrets

from . import growth_config
from .agent_common import AgentError, clean_text, require_owner
from .agent_texts import GOAL_SENSITIVE_BANNER
from .goal_findings import public_entry_content
from .goal_record import canonical_json
from .goals import _object
from .referrals import early_access_active
from .sessions import storage_time
from .topic_boards import REASONS as BOARD_REASONS


REASONS = (*BOARD_REASONS, "medical_claim", "misinformation", "dangerous", "copyright")
THRESHOLD_REASON = "Hidden pending operator review after the distinct-report threshold was reached."
_ENTRY_ID = re.compile(r"e_[0-9a-f]{64}\Z")
_RESTORABLE = ("visible", "pending_peer_check", "held")


def remember_hold(connection, entry_id, reasons):
    """Remember a new safety hold inside its author's ordinary transaction.

    This insert deliberately does not advance recovery authority. A duplicate
    call is a no-op only when the original closed reason codes are unchanged.
    No content or personal data is copied into this metadata.
    """
    from .goal_safety import HOLD_CODES
    if (not isinstance(reasons, (list, tuple)) or not reasons or
            any(not isinstance(reason, str) or reason not in HOLD_CODES for reason in reasons)):
        raise AgentError(422, "Safety hold reasons must use the fixed rule codes.")
    reasons = list(dict.fromkeys(reasons))
    if not connection.in_transaction:
        raise ValueError("Safety hold metadata requires a transaction")
    entry = connection.execute("SELECT status,content_json FROM goal_entries WHERE id=?", (entry_id,)).fetchone()
    if entry is None or entry["status"] != "held" or entry["content_json"] is None:
        raise AgentError(409, "Only a new held entry can retain safety-screen reasons.")
    encoded = canonical_json(reasons)
    previous = connection.execute("SELECT hold_reasons_json FROM goal_entry_moderation WHERE entry_id=?", (entry_id,)).fetchone()
    if previous is not None:
        if previous["hold_reasons_json"] != encoded:
            raise AgentError(409, "The original safety hold reasons cannot be replaced.")
        return
    connection.execute("INSERT INTO goal_entry_moderation(entry_id,hold_reasons_json) VALUES(?,?)", (entry_id, encoded))


class GoalModerationService:
    def __init__(self, database, clock, goals):
        self.database, self.clock, self.goals = database, clock, goals

    @staticmethod
    def _entry(connection, entry_id):
        if not isinstance(entry_id, str) or _ENTRY_ID.fullmatch(entry_id) is None:
            raise AgentError(404, "Goal entry not found.")
        entry = connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry_id,)).fetchone()
        if entry is None:
            raise AgentError(404, "Goal entry not found.")
        return entry

    def _eligible_reporter(self, connection, user_id, config):
        owner = connection.execute("SELECT suspended_at FROM users WHERE id=?", (user_id,)).fetchone()
        if owner is None or owner["suspended_at"] is not None:
            return False
        return config["feature.goals"] == "everyone" or (
            config["feature.goals"] == "early_access" and early_access_active(connection, user_id, self.clock()))

    def _report_count(self, connection, entry_id, config):
        return sum(self._eligible_reporter(connection, row["reporter_id"], config)
                   for row in connection.execute("SELECT DISTINCT reporter_id FROM goal_reports WHERE entry_id=? AND reviewed_at IS NULL", (entry_id,)))

    def _hide(self, connection, goal, entry):
        if entry["status"] not in _RESTORABLE:
            raise AgentError(409, "This entry is already hidden or no longer available.")
        connection.execute("INSERT INTO goal_entry_moderation(entry_id,hidden_from_status) VALUES(?,?) "
                           "ON CONFLICT(entry_id) DO UPDATE SET hidden_from_status=excluded.hidden_from_status",
                           (entry["id"], entry["status"]))
        connection.execute("UPDATE goal_entries SET status='hidden' WHERE id=?", (entry["id"],))
        if entry["kind"] == "charter" and goal["state"] == "open":
            content = json.loads(entry["content_json"])
            if isinstance(content, dict) and content.get("revision") == goal["charter_revision"]:
                from .goal_sessions import revoke_goal
                revoke_goal(connection, goal["id"], self.clock(), "goal_paused")
                connection.execute("UPDATE goals SET state='paused' WHERE id=?", (goal["id"],))

    def _operator_entry(self, connection, goal, entry_id, action, reason, **extra):
        # Only the CLI's explicit reason or our fixed threshold text is public.
        # Never copy an abuse report's reason, details, id or account identity.
        return self.goals._append(connection, goal, "operator", "operator",
            {"action": action, "entry_id": entry_id, "reason": reason, **extra}, self.clock(), refs=[entry_id])

    def report(self, user_id, body):
        """Accept ``{entry_id,reason,details?}``; return only the owner's ack."""
        self.goals.require_feature(user_id)
        _object(body, "goal report", {"entry_id", "reason"}, {"details"})
        if not isinstance(body["reason"], str) or body["reason"] not in REASONS:
            raise AgentError(422, "reason must be one of: " + ", ".join(REASONS) + ".")
        details = clean_text(body.get("details", ""), "report details", 1000, minimum=0, multiline=True)
        with self.database.transaction() as connection:
            config = self.goals._feature(connection, user_id)
            require_owner(connection, user_id)
            self.goals._not_held(connection)
            entry = self._entry(connection, body["entry_id"])
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (entry["goal_id"],)).fetchone()
            if (not self.goals._listed(goal) or entry["author_user_id"] == user_id or
                    not public_entry_content(connection, entry)):
                raise AgentError(404, "This goal entry is not available to report.")
            connection.execute("INSERT OR IGNORE INTO goal_reports(id,entry_id,reporter_id,reason,details,created_at) VALUES(?,?,?,?,?,?)",
                ("r_" + secrets.token_hex(16), entry["id"], user_id, body["reason"], details, storage_time(self.clock())))
            count = self._report_count(connection, entry["id"], config)
            if count >= config["report_hide_threshold"]:
                self._hide(connection, goal, entry)
                self._operator_entry(connection, goal, entry["id"], "hide", THRESHOLD_REASON,
                                     source="report_threshold", reports=count)
            result = {"reported": True, "entry_id": entry["id"]}
            if goal["tier"] == "sensitive":
                result["banner"] = GOAL_SENSITIVE_BANNER
            return result

    def held(self):
        """Oldest held content for the trusted local operator, even feature-off."""
        with self.database.connect() as connection:
            entries = []
            for entry in connection.execute("SELECT e.* FROM goal_entries e JOIN goals g ON g.id=e.goal_id "
                    "WHERE e.status='held' AND e.content_json IS NOT NULL ORDER BY e.created_at,g.slug,e.seq"):
                goal = connection.execute("SELECT * FROM goals WHERE id=?", (entry["goal_id"],)).fetchone()
                metadata = connection.execute("SELECT * FROM goal_entry_moderation WHERE entry_id=?", (entry["id"],)).fetchone()
                reasons = json.loads(metadata["hold_reasons_json"]) if metadata is not None else []
                if not reasons and entry["kind"] == "charter":
                    reasons = ["charter_review"]
                entries.append({**self.goals.entry_projection(connection, goal, entry, operator=True), "reasons": reasons})
            return {"entries": entries}

    def reports(self):
        """Private report rows and full entry content for the local CLI only."""
        with self.database.connect() as connection:
            config, reports = growth_config.load(connection), []
            for report in connection.execute("SELECT r.* FROM goal_reports r JOIN goal_entries e ON e.id=r.entry_id "
                                              "JOIN goals g ON g.id=e.goal_id ORDER BY r.created_at,g.slug,e.seq,r.id"):
                entry = self._entry(connection, report["entry_id"])
                goal = connection.execute("SELECT * FROM goals WHERE id=?", (entry["goal_id"],)).fetchone()
                reports.append({**dict(report), "eligible_reporter": self._eligible_reporter(connection, report["reporter_id"], config),
                                "entry": self.goals.entry_projection(connection, goal, entry, operator=True)})
            return {"reports": reports}

    def action(self, entry_id, action, reason):
        """CLI-only release/hide/unhide with a fresh explicit public reason."""
        if not isinstance(action, str) or action not in ("release", "hide", "unhide"):
            raise AgentError(422, "Choose release, hide or unhide.")
        reason = clean_text(reason, "operator reason", 1000, minimum=5, multiline=True)
        with self.database.transaction() as connection:
            self.goals._not_held(connection)
            entry = self._entry(connection, entry_id)
            if entry["content_json"] is None or entry["status"] == "withdrawn":
                raise AgentError(409, "Erased content cannot be released or restored.")
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (entry["goal_id"],)).fetchone()
            if action == "hide":
                self._hide(connection, goal, entry)
            elif action == "release":
                if entry["kind"] == "charter":
                    raise AgentError(409, "Use goal approve or refuse for charter review; releasing an entry cannot approve a charter.")
                if entry["status"] != "held":
                    raise AgentError(409, "Only a held entry can be released.")
                connection.execute("UPDATE goal_entries SET status=? WHERE id=?",
                                   ("pending_peer_check" if entry["kind"] == "finding" else "visible", entry["id"]))
            else:
                if entry["status"] != "hidden":
                    raise AgentError(409, "Only a hidden entry can be unhidden.")
                metadata = connection.execute("SELECT hidden_from_status FROM goal_entry_moderation WHERE entry_id=?", (entry["id"],)).fetchone()
                if metadata is None or metadata["hidden_from_status"] not in _RESTORABLE:
                    raise AgentError(409, "The prior visibility is unknown. This entry cannot be safely unhidden.")
                connection.execute("UPDATE goal_entries SET status=? WHERE id=?", (metadata["hidden_from_status"], entry["id"]))
                connection.execute("UPDATE goal_entry_moderation SET hidden_from_status=NULL WHERE entry_id=?", (entry["id"],))
                connection.execute("UPDATE goal_reports SET reviewed_at=? WHERE entry_id=? AND reviewed_at IS NULL",
                                   (storage_time(self.clock()), entry_id))
            self._operator_entry(connection, goal, entry_id, action, reason)
            current = self._entry(connection, entry_id)
            return {"entry": self.goals.entry_projection(connection, goal, current, operator=True)}
