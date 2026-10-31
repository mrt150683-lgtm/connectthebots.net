"""Private notification events, explicit preferences and a durable email queue.

No constructor, HTTP read or outbox-mode job opens a network connection.
Notification links grant no access; only unsubscribe carries a narrow capability.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta
from email.utils import format_datetime
import hmac
import json
import re
import sys

from .board import BoardService
from .interactions import InteractionError, InteractionService
from .operator_alerts import OperatorAlertError, OperatorAlerts, held
from .sessions import random_token, storage_time

KINDS = ("security_first_login", "introduction_request", "introduction_accepted", "new_message", "checkin_missed", "weekly_summary")
LABELS = {
    "security_first_login": "First sign-in security email",
    "introduction_request": "Introduction requests",
    "introduction_accepted": "Accepted introductions",
    "new_message": "New messages",
    "checkin_missed": "Missed check-ins",
    "weekly_summary": "Weekly summaries",
}
TITLES = {
    "security_first_login": "Your account was created.",
    "introduction_request": "Someone would like an introduction.",
    "introduction_accepted": "You both agreed to an introduction.",
    "new_message": "You have new messages.",
    "checkin_missed": "An expected check-in has not arrived.",
    "weekly_summary": "Your weekly summary is ready.",
}


class NotificationError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class NotificationService:
    def __init__(self, database, clock, settings, *, operator_alerts=None):
        self.database, self.clock, self.settings = database, clock, settings
        self.operator_alerts = operator_alerts or OperatorAlerts(database, clock, settings)
        # Reuse the domain's use-time authority checks on the SAME transaction.
        self.interactions = InteractionService(database, clock, BoardService(database, clock))

    @staticmethod
    def _owner(connection, user_id):
        row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise NotificationError(401, "Please sign in again.")
        if row["suspended_at"] is not None:
            raise NotificationError(403, "This account is not available.")
        return row

    @staticmethod
    def _preferences(connection, user_id):
        row = connection.execute("SELECT * FROM notification_preferences WHERE user_id=?", (user_id,)).fetchone()
        return {kind: bool(row[kind]) if row else kind == "security_first_login" for kind in KINDS}

    def _ensure_preferences(self, connection, user_id):
        due = storage_time(self.clock() + timedelta(days=7))
        connection.execute("INSERT OR IGNORE INTO notification_preferences(user_id,weekly_next_at) VALUES(?,?)", (user_id, due))
        connection.execute("UPDATE notification_preferences SET weekly_next_at=? WHERE user_id=? AND weekly_next_at IS NULL", (due, user_id))

    def preferences(self, user_id):
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            return self._preferences(connection, user_id)

    def update_preferences(self, user_id, values):
        if type(values) is not dict or values.keys() != set(KINDS) or any(type(value) is not bool for value in values.values()):
            raise NotificationError(422, "Choose on or off for each notification type.")
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            self._ensure_preferences(connection, user_id)
            # These column names come only from the constant allowlist above.
            connection.execute("UPDATE notification_preferences SET " + ",".join(kind + "=?" for kind in KINDS) + " WHERE user_id=?", (*[int(values[kind]) for kind in KINDS], user_id))
            for kind in KINDS:
                if not values[kind]:
                    connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE user_id=? AND kind=? AND state IN ('pending','outbox')", (user_id, kind))
            if not values["new_message"]:
                connection.execute("UPDATE notification_mail_windows SET pending_since=NULL,latest_event_id=NULL WHERE user_id=?", (user_id,))
            return dict(values)

    def _subscription(self, connection, user_id, kind):
        row = connection.execute("SELECT id FROM notification_subscriptions WHERE user_id=? AND kind=?", (user_id, kind)).fetchone()
        if row is None:
            handle = random_token()
            connection.execute("INSERT INTO notification_subscriptions(id,user_id,kind) VALUES(?,?,?)", (handle, user_id, kind))
        else:
            handle = row[0]
        value = handle + "." + kind
        signature = hmac.new(self.settings.session_secret.encode("utf-8"), ("email-unsubscribe-v1\0" + value).encode("ascii"), "sha256").hexdigest()
        return value + "." + signature

    def unsubscribe_token(self, user_id, kind):
        if kind not in KINDS:
            raise NotificationError(400, "This unsubscribe link is invalid.")
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            return self._subscription(connection, user_id, kind)

    def _token(self, connection, token):
        if not isinstance(token, str) or len(token) > 160:
            raise NotificationError(400, "This unsubscribe link is invalid.")
        parts = token.split(".")
        if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parts[0]) or parts[1] not in KINDS or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
            raise NotificationError(400, "This unsubscribe link is invalid.")
        expected = hmac.new(self.settings.session_secret.encode("utf-8"), ("email-unsubscribe-v1\0" + ".".join(parts[:2])).encode("ascii"), "sha256").hexdigest()
        if not hmac.compare_digest(expected, parts[2]):
            raise NotificationError(400, "This unsubscribe link is invalid.")
        row = connection.execute("SELECT user_id,kind FROM notification_subscriptions WHERE id=? AND kind=?", parts[:2]).fetchone()
        if row is None:
            raise NotificationError(400, "This unsubscribe link is invalid.")
        return row

    def token_info(self, token):
        if isinstance(token, str) and token.startswith("operator."):
            self.operator_alerts.settings = self.settings
            try:
                return self.operator_alerts.token_info(token)
            except OperatorAlertError as error:
                raise NotificationError(error.status, error.detail) from None
        with self.database.connect() as connection:
            row = self._token(connection, token)
            return {"kind": row["kind"], "label": LABELS[row["kind"]]}

    def unsubscribe(self, token):
        if isinstance(token, str) and token.startswith("operator."):
            self.operator_alerts.settings = self.settings
            try:
                return self.operator_alerts.unsubscribe(token)
            except OperatorAlertError as error:
                raise NotificationError(error.status, error.detail) from None
        with self.database.transaction() as connection:
            row = self._token(connection, token)
            owner, kind = row["user_id"], row["kind"]
            self._ensure_preferences(connection, owner)
            connection.execute("UPDATE notification_preferences SET " + kind + "=0 WHERE user_id=?", (owner,))
            if kind == "new_message":
                connection.execute("UPDATE notification_mail_windows SET pending_since=NULL,latest_event_id=NULL WHERE user_id=?", (owner,))
            connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE user_id=? AND kind=? AND state IN ('pending','outbox')", (owner, kind))
            return {"kind": kind, "label": LABELS[kind], "unsubscribed": True}

    def _eligible(self, connection, event, *, email=False):
        if email and held(connection, self.settings):
            return False
        if event["cancelled_at"] is not None:
            return False
        owner = connection.execute("SELECT suspended_at FROM users WHERE id=?", (event["user_id"],)).fetchone()
        if owner is None or owner[0] is not None:
            return False
        kind, user_id = event["kind"], event["user_id"]
        if email and (not self._preferences(connection, user_id)[kind] or (kind != "security_first_login" and not self.settings.account_signin_enabled)):
            return False
        try:
            if kind in {"introduction_request", "introduction_accepted"}:
                row = self.interactions._refresh(connection, self.interactions._intro(connection, user_id, event["source_id"]))
                expected = "requested" if kind == "introduction_request" else "accepted"
                recipient = row["recipient_id"] if kind == "introduction_request" else row["requester_id"]
                peer = row["requester_id"] if kind == "introduction_request" else row["recipient_id"]
                return row["status"] == expected and recipient == user_id and self.interactions._active(connection, peer) and not self.interactions._blocked(connection, user_id, peer) and row["context_hash"] is not None
            if kind == "new_message":
                row = self.interactions._conversation(connection, user_id, event["source_id"])
                return row["closed_at"] is None
            if kind == "checkin_missed":
                row = connection.execute("SELECT * FROM report_settings WHERE user_id=?", (user_id,)).fetchone()
                return row is not None and not row["paused"] and row["cadence_minutes"] is not None and row["next_due_at"] == event["source_id"] and self.clock() >= _instant(row["next_due_at"]) + timedelta(hours=6)
            return kind in {"security_first_login", "weekly_summary"}
        except InteractionError:
            return False

    @staticmethod
    def _url(event):
        if event["kind"] in {"introduction_request", "introduction_accepted"}:
            return "/app/introductions/" + event["source_id"]
        if event["kind"] == "new_message":
            return "/app/conversations/" + event["source_id"]
        return "/app/account" if event["kind"] == "security_first_login" else "/app"

    def _summary(self, connection, event):
        if event["kind"] != "weekly_summary":
            return None
        # A historical item keeps its original period. Later authorization
        # changes still exclude blocked or erased peer activity from its counts.
        until = event["created_at"]
        since = storage_time(_instant(until) - timedelta(days=7))
        BoardService._register_checks(connection)
        reports = connection.execute("SELECT COUNT(*) FROM reports WHERE user_id=? AND received_at>? AND received_at<=?", (event["user_id"], since, until)).fetchone()[0]
        activity = connection.execute("SELECT COALESCE(SUM(e.kind IN ('introduction_request','introduction_accepted')),0),COALESCE(SUM(e.kind='new_message'),0) FROM notification_events e WHERE e.user_id=? AND e.created_at>? AND e.created_at<=? AND " + self._visible_sql(), (event["user_id"], since, until, *self._visible_parameters())).fetchone()
        report_word = "report" if reports == 1 else "reports"
        update_word = "update" if activity[0] == 1 else "updates"
        message_word = "message" if activity[1] == 1 else "messages"
        return f"In the seven days ending {until[:10]}: {reports} {report_word} received; {activity[0]} available introduction {update_word}; {activity[1]} {message_word} received in available conversations."

    def _view(self, connection, event):
        return {"id": event["id"], "kind": event["kind"], "title": TITLES[event["kind"]], "url": self._url(event), "created_at": event["created_at"], "read_at": event["read_at"], "summary": self._summary(connection, event)}

    def _prune(self, connection, user_id=None):
        if user_id is None:
            prior = connection.execute("SELECT last_rowid FROM notification_maintenance WHERE name='prune'").fetchone()
            cursor = prior[0] if prior else 0
            events = connection.execute("SELECT rowid AS event_rowid,* FROM notification_events WHERE rowid>? AND cancelled_at IS NULL AND kind NOT IN ('security_first_login','weekly_summary') ORDER BY rowid LIMIT 500", (cursor,)).fetchall()
            if not events and cursor:
                events = connection.execute("SELECT rowid AS event_rowid,* FROM notification_events WHERE cancelled_at IS NULL AND kind NOT IN ('security_first_login','weekly_summary') ORDER BY rowid LIMIT 500").fetchall()
            last = events[-1]["event_rowid"] if events else 0
            connection.execute("INSERT INTO notification_maintenance(name,last_rowid) VALUES('prune',?) ON CONFLICT(name) DO UPDATE SET last_rowid=excluded.last_rowid", (last,))
        else:
            events = connection.execute("SELECT * FROM notification_events WHERE cancelled_at IS NULL AND user_id=? AND kind NOT IN ('security_first_login','weekly_summary')", (user_id,)).fetchall()
        for event in events:
            if not self._eligible(connection, event):
                connection.execute("UPDATE notification_events SET cancelled_at=? WHERE id=?", (storage_time(self.clock()), event["id"]))
                connection.execute("UPDATE email_outbox SET state='cancelled' WHERE event_id=? AND state IN ('pending','outbox')", (event["id"],))

    def list_items(self, user_id, page=1, limit=20):
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 50:
            raise NotificationError(422, "Choose a page from 1 to 10000 and page size from 1 to 50.")
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            self._prune(connection, user_id)
            total, unread = connection.execute("SELECT COUNT(*),COALESCE(SUM(read_at IS NULL),0) FROM notification_events WHERE user_id=? AND cancelled_at IS NULL", (user_id,)).fetchone()
            rows = connection.execute("SELECT * FROM notification_events WHERE user_id=? AND cancelled_at IS NULL ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?", (user_id, limit, (page - 1) * limit))
            return {"items": [self._view(connection, row) for row in rows], "total": total, "unread_total": unread, "page": page, "page_size": limit, "has_next": page * limit < total}

    def _visible_sql(self):
        # A read-only navigation aggregate. It neither refreshes domain state
        # nor opens a write transaction on every rendered page.
        approved = BoardService._APPROVED + BoardService._VISIBLE
        return f"""e.cancelled_at IS NULL AND EXISTS (SELECT 1 FROM users u WHERE u.id=e.user_id AND u.suspended_at IS NULL)
          AND (e.kind IN ('security_first_login','weekly_summary') OR
            (e.kind='checkin_missed' AND EXISTS (SELECT 1 FROM report_settings r WHERE r.user_id=e.user_id AND r.paused=0 AND r.cadence_minutes IS NOT NULL AND r.next_due_at=e.source_id AND r.next_due_at<=?)) OR
            (e.kind IN ('introduction_request','introduction_accepted','new_message')
             AND EXISTS (SELECT 1 FROM users a WHERE a.id=e.actor_id AND a.suspended_at IS NULL)
             AND NOT EXISTS (SELECT 1 FROM user_blocks b WHERE (b.blocker_id=e.user_id AND b.blocked_id=e.actor_id) OR (b.blocker_id=e.actor_id AND b.blocked_id=e.user_id))
             AND ((e.kind='introduction_request' AND EXISTS (SELECT 1 FROM introductions i
                 WHERE i.id=e.source_id AND i.recipient_id=e.user_id AND i.status='requested' AND i.expires_at>?
                 AND i.context_hash IS NOT NULL
                 AND EXISTS ({approved} AND p.user_id=i.requester_id AND g.id=i.requester_grant_id)
                 AND EXISTS ({approved} AND p.user_id=i.recipient_id AND g.id=i.recipient_grant_id))) OR
              (e.kind='introduction_accepted' AND EXISTS (SELECT 1 FROM introductions i WHERE i.id=e.source_id AND i.requester_id=e.user_id AND i.status='accepted' AND i.context_hash IS NOT NULL)) OR
              (e.kind='new_message' AND EXISTS (SELECT 1 FROM conversations c WHERE c.id=e.source_id AND c.closed_at IS NULL AND (c.requester_id=e.user_id OR c.recipient_id=e.user_id))))))"""

    def _visible_parameters(self):
        return (storage_time(self.clock() - timedelta(hours=6)), storage_time(self.clock()))

    def unread_count(self, user_id):
        with self.database.connect() as connection:
            BoardService._register_checks(connection)
            return connection.execute("SELECT COUNT(*) FROM notification_events e WHERE e.user_id=? AND e.read_at IS NULL AND " + self._visible_sql(), (user_id, *self._visible_parameters())).fetchone()[0]

    def mark_read(self, user_id, notification_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            self._prune(connection, user_id)
            found = connection.execute("SELECT 1 FROM notification_events WHERE id=? AND user_id=? AND cancelled_at IS NULL", (notification_id, user_id)).fetchone()
            if found is None:
                raise NotificationError(404, "Notification not found.")
            connection.execute("UPDATE notification_events SET read_at=COALESCE(read_at,?) WHERE id=?", (storage_time(self.clock()), notification_id))

    def record_event(self, connection, user_id, kind, source_id=None, actor_id=None, event_key=None):
        """Internal domain hook. The caller owns its surrounding transaction."""
        if kind not in KINDS:
            raise NotificationError(422, "Unknown notification type.")
        self._owner(connection, user_id)
        self._ensure_preferences(connection, user_id)
        event_key = "security_first_login" if kind == "security_first_login" else event_key
        if not isinstance(event_key, str) or not 1 <= len(event_key) <= 200:
            raise NotificationError(422, "A notification event identifier is required.")
        prior = connection.execute("SELECT id FROM notification_events WHERE user_id=? AND dedupe_key=?", (user_id, kind + ":" + event_key)).fetchone()
        if prior:
            return prior[0]
        event_id, now = random_token(), storage_time(self.clock())
        connection.execute("INSERT INTO notification_events(id,user_id,kind,source_id,actor_id,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?)", (event_id, user_id, kind, source_id, actor_id, kind + ":" + event_key, now))
        event = connection.execute("SELECT * FROM notification_events WHERE id=?", (event_id,)).fetchone()
        if self._eligible(connection, event, email=True):
            if kind == "new_message":
                connection.execute("INSERT INTO notification_mail_windows(user_id,conversation_id,pending_since,latest_event_id) VALUES(?,?,?,?) ON CONFLICT(user_id,conversation_id) DO UPDATE SET pending_since=COALESCE(pending_since,excluded.pending_since),latest_event_id=excluded.latest_event_id", (user_id, source_id, now, event_id))
            else:
                self._queue(connection, event)
        return event_id

    def _render(self, connection, event, outbox_id):
        token = self._subscription(connection, event["user_id"], event["kind"])
        unsubscribe = self.settings.public_base_url + "/email/unsubscribe/" + token
        title = TITLES[event["kind"]]
        text = title + "\n\nOpen your private dashboard to review it:\n" + self.settings.public_base_url + self._url(event)
        if event["kind"] == "weekly_summary":
            text += "\n\n" + self._summary(connection, event)
        text += "\n\nNo reply or agreement is made by this email.\nStop these emails:\n" + unsubscribe + "\n"
        domain = self.settings.public_base_url.split("://", 1)[1].split(":", 1)[0]
        return {"subject": title, "text": text, "headers": {
            "List-Unsubscribe": "<" + unsubscribe + ">", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "Message-ID": "<" + outbox_id + "@" + domain + ">", "Date": format_datetime(self.clock()),
        }}

    def _queue(self, connection, event):
        if not self._eligible(connection, event, email=True):
            return False
        if connection.execute("SELECT 1 FROM email_outbox WHERE event_id=?", (event["id"],)).fetchone():
            return False
        outbox_id, now = random_token(), storage_time(self.clock())
        payload = self._render(connection, event, outbox_id)
        mode = "outbox" if self.settings.email_mode == "outbox" else "pending"
        connection.execute("INSERT INTO email_outbox(id,user_id,kind,created_at,payload_json,event_id,state,available_at) VALUES(?,?,?,?,?,?,?,?)", (outbox_id, event["user_id"], event["kind"], now, json.dumps(payload), event["id"], mode, now))
        return True

    def _scheduled(self, connection):
        if not self.settings.account_signin_enabled:
            return 0
        before = connection.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0]
        now = self.clock()
        # Bell cadence is independent of the optional email preference. Existing
        # accounts begin a fresh week, never a historical catch-up mail batch.
        next_week = storage_time(now + timedelta(days=7))
        connection.execute("INSERT OR IGNORE INTO notification_preferences(user_id,weekly_next_at) SELECT id,? FROM users WHERE suspended_at IS NULL", (next_week,))
        connection.execute("UPDATE notification_preferences SET weekly_next_at=? WHERE weekly_next_at IS NULL AND user_id IN (SELECT id FROM users WHERE suspended_at IS NULL)", (next_week,))
        for row in connection.execute("SELECT r.* FROM report_settings r JOIN users u ON u.id=r.user_id WHERE r.paused=0 AND r.cadence_minutes IS NOT NULL AND r.next_due_at IS NOT NULL AND u.suspended_at IS NULL").fetchall():
            if now >= _instant(row["next_due_at"]) + timedelta(hours=6):
                self.record_event(connection, row["user_id"], "checkin_missed", row["next_due_at"], event_key=row["next_due_at"])
        for row in connection.execute("SELECT p.* FROM notification_preferences p JOIN users u ON u.id=p.user_id WHERE weekly_next_at<=? AND u.suspended_at IS NULL", (storage_time(now),)).fetchall():
            self.record_event(connection, row["user_id"], "weekly_summary", row["weekly_next_at"], event_key=row["weekly_next_at"])
            connection.execute("UPDATE notification_preferences SET weekly_next_at=? WHERE user_id=?", (storage_time(now + timedelta(days=7)), row["user_id"]))
        for row in connection.execute("SELECT * FROM notification_mail_windows WHERE pending_since IS NOT NULL").fetchall():
            due = _instant(row["pending_since"]) + timedelta(hours=1)
            if row["last_queued_at"]:
                due = max(due, _instant(row["last_queued_at"]) + timedelta(hours=1))
            if now < due:
                continue
            outstanding = connection.execute("SELECT 1 FROM email_outbox o JOIN notification_events e ON e.id=o.event_id WHERE o.user_id=? AND o.kind='new_message' AND e.source_id=? AND o.state IN ('pending','sending')", (row["user_id"], row["conversation_id"])).fetchone()
            if outstanding:
                continue
            event = connection.execute("SELECT * FROM notification_events WHERE id=?", (row["latest_event_id"],)).fetchone()
            if event is not None:
                queued = self._queue(connection, event)
                connection.execute("UPDATE notification_mail_windows SET pending_since=NULL,latest_event_id=NULL,last_queued_at=CASE WHEN ? THEN ? ELSE last_queued_at END WHERE user_id=? AND conversation_id=?", (int(queued), storage_time(now), row["user_id"], row["conversation_id"]))
        return connection.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0] - before

    def run_due(self, limit=50, *, transport=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise NotificationError(422, "Delivery batch size must be between 1 and 100.")
        if transport is not None and self.settings.app_env == "production":
            raise ValueError("Injected transports are forbidden in production")
        result = {name: 0 for name in ("queued", "recorded", "accepted", "retried", "failed", "uncertain", "cancelled")}
        self.operator_alerts.settings = self.settings
        with self.database.transaction() as connection:
            if held(connection, self.settings):
                return result
            self._prune(connection)
            result["queued"] = self._scheduled(connection)
            result["uncertain"] = connection.execute("UPDATE email_outbox SET state='uncertain',last_error_code='interrupted',lease_id=NULL,lease_until=NULL WHERE state='sending' AND lease_until<=?", (storage_time(self.clock()),)).rowcount
        if self.settings.email_mode == "outbox":
            result["recorded"] = result["queued"]
            return result
        if self.settings.app_env != "production" and transport is None:
            raise NotificationError(503, "SMTP is available only in production.")
        if transport is None:
            from .notification_transport import SMTPTransport
            transport = SMTPTransport(self.settings)
        for _ in range(limit):
            with self.database.transaction() as connection:
                row = connection.execute("SELECT * FROM email_outbox WHERE state='pending' AND available_at<=? ORDER BY available_at,id LIMIT 1", (storage_time(self.clock()),)).fetchone()
                if row is None:
                    break
                event, recipient = self._delivery(connection, row)
                if recipient is None:
                    connection.execute("UPDATE email_outbox SET state='cancelled' WHERE id=?", (row["id"],))
                    result["cancelled"] += 1
                    continue
                now, lease = self.clock(), random_token()
                if row["kind"] == "new_message":
                    window = connection.execute("SELECT last_attempt_at FROM notification_mail_windows WHERE user_id=? AND conversation_id=?", (row["user_id"], event["source_id"])).fetchone()
                    if window and window[0] and now < _instant(window[0]) + timedelta(hours=1):
                        connection.execute("UPDATE email_outbox SET available_at=? WHERE id=?", (storage_time(_instant(window[0]) + timedelta(hours=1)), row["id"]))
                        continue
                    connection.execute("UPDATE notification_mail_windows SET last_attempt_at=? WHERE user_id=? AND conversation_id=?", (storage_time(now), row["user_id"], event["source_id"]))
                connection.execute("UPDATE email_outbox SET state='sending',attempts=attempts+1,last_attempt_at=?,lease_id=?,lease_until=? WHERE id=?", (storage_time(now), lease, storage_time(now + timedelta(minutes=15)), row["id"]))
                payload = json.loads(row["payload_json"])
                envelope = {"recipient": recipient, "sender": self.settings.smtp_from_address, **payload}
            # Recheck after claiming, immediately before external hand-off. No
            # body/profile content is sent, and in-flight SMTP cannot be unsent.
            with self.database.transaction() as connection:
                current = connection.execute("SELECT * FROM email_outbox WHERE id=? AND state='sending' AND lease_id=?", (row["id"], lease)).fetchone()
                event, recipient = self._delivery(connection, current) if current is not None else (None, None)
                if recipient is None:
                    connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE id=? AND lease_id=?", (row["id"], lease))
                    result["cancelled"] += 1
                    continue
                envelope["recipient"] = recipient
                if row["operator_alert_id"] is not None:
                    payload = self.operator_alerts.render(connection, event, row["id"])
                    connection.execute("UPDATE email_outbox SET payload_json=? WHERE id=?", (json.dumps(payload), row["id"]))
                    envelope.update(payload)
                elif event["kind"] == "weekly_summary":
                    payload = self._render(connection, event, row["id"])
                    connection.execute("UPDATE email_outbox SET payload_json=? WHERE id=?", (json.dumps(payload), row["id"]))
                    envelope.update(payload)
            try:
                outcome = transport.send(envelope)
            except Exception:
                outcome = {"status": "uncertain", "code": "transport_interrupted"}
            status = outcome.get("status") if isinstance(outcome, dict) else None
            if status not in {"accepted", "retry", "failed", "uncertain"}:
                status = "uncertain"
            code = outcome.get("code", "unknown") if isinstance(outcome, dict) else "unknown"
            if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,40}", code) is None:
                code = "unknown"
            with self.database.transaction() as connection:
                current = connection.execute("SELECT * FROM email_outbox WHERE id=? AND lease_id=? AND state='sending'", (row["id"], lease)).fetchone()
                if current is None:
                    continue
                if status == "retry" and current["attempts"] >= 5:
                    status, code = "failed", "retry_exhausted"
                stored = "pending" if status == "retry" else status
                available = storage_time(self.clock() + timedelta(minutes=min(60, 5 * 2 ** (current["attempts"] - 1)))) if status == "retry" else current["available_at"]
                connection.execute("UPDATE email_outbox SET state=?,available_at=?,sent_at=?,last_error_code=?,lease_id=NULL,lease_until=NULL WHERE id=? AND lease_id=?", (stored, available, storage_time(self.clock()) if status == "accepted" else None, None if status == "accepted" else code, row["id"], lease))
                result["retried" if status == "retry" else status] += 1
        return result

    def _delivery(self, connection, row):
        if held(connection, self.settings):
            return None, None
        if row["operator_alert_id"] is not None:
            event = connection.execute("SELECT * FROM operator_alerts WHERE id=?", (row["operator_alert_id"],)).fetchone()
            if row["user_id"] is not None or row["event_id"] is not None or not self.operator_alerts.eligible(connection, event):
                return None, None
            if row["kind"] != "operator_" + event["kind"]:
                return None, None
            return event, self.operator_alerts.recipient
        event = connection.execute("SELECT * FROM notification_events WHERE id=?", (row["event_id"],)).fetchone()
        if event is None or row["user_id"] != event["user_id"] or row["kind"] != event["kind"] or not self._eligible(connection, event, email=True):
            return None, None
        return event, self._owner(connection, row["user_id"])["email"]

    def export_owner(self, user_id, *, connection=None):
        if connection is None:
            with self.database.transaction() as opened:
                return self.export_owner(user_id, connection=opened)
        self._owner(connection, user_id)
        self._prune(connection, user_id)
        events = [dict(row) for row in connection.execute("SELECT id,kind,created_at,read_at,cancelled_at FROM notification_events WHERE user_id=? ORDER BY created_at,id", (user_id,))]
        outbox = [dict(row) for row in connection.execute("SELECT id,kind,created_at,state,sent_at AS accepted_at,attempts,last_attempt_at,last_error_code FROM email_outbox WHERE user_id=? ORDER BY created_at,id", (user_id,))]
        return {"preferences": self._preferences(connection, user_id), "items": events, "email_outbox": outbox}

    def erase_owner(self, user_id, *, connection):
        # Delete peer-facing events too; retained shared conversations must not
        # carry notifications identifying the erased person's earlier actions.
        connection.execute("DELETE FROM notification_events WHERE user_id=? OR actor_id=? OR source_id IN (SELECT id FROM introductions WHERE requester_id=? OR recipient_id=?) OR source_id IN (SELECT id FROM conversations WHERE requester_id=? OR recipient_id=?)", (user_id, user_id, user_id, user_id, user_id, user_id))
        connection.execute("DELETE FROM notification_mail_windows WHERE user_id=? OR conversation_id IN (SELECT id FROM conversations WHERE requester_id=? OR recipient_id=?)", (user_id, user_id, user_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process due private notifications.")
    parser.add_argument("command", choices=("tick",))
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    locks = ExitStack()
    try:
        from .clock import utc_now
        from .config import Settings
        from .db import Database
        settings = Settings.from_env()
        from .recovery_authority import from_settings
        database = Database(settings.database_path, utc_now, recovery_authority=from_settings(settings))
        from .recovery import runtime_lock
        locks.enter_context(runtime_lock(database.path))
        if not database.is_current():
            print("Notification database is unavailable or not current.", file=sys.stderr)
            return 2
        counts = NotificationService(database, utc_now, settings).run_due(args.limit)
        print(json.dumps(counts, sort_keys=True))
        return 0
    except Exception:
        print("Notification processing is unavailable.", file=sys.stderr)
        return 2
    finally:
        locks.close()


if __name__ == "__main__":
    raise SystemExit(main())
