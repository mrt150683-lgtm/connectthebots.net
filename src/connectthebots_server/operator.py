"""Server-local operator actions. No HTTP caller receives this service."""
from datetime import datetime, time, timedelta, timezone

from .board import BoardService, touch_board
from .operator_alerts import held
from .sessions import random_token, storage_time, token_hash


class OperatorError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


class OperatorService:
    def __init__(self, database, clock, board, interactions, notifications, moderation, *, lifecycle=None):
        self.database, self.clock = database, clock
        self.board, self.interactions, self.notifications = board, interactions, notifications
        self.moderation, self.lifecycle = moderation, lifecycle
        self.settings = notifications.settings

    @staticmethod
    def _pagination(page, limit):
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 100:
            raise OperatorError(422, "Choose a page from 1 to 10000 and a limit from 1 to 100.")

    @staticmethod
    def _user(connection, user_id):
        row = connection.execute("SELECT id,email,display_name,created_at,last_login_at,suspended_at FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise OperatorError(404, "Account not found.")
        return row

    def _not_held(self, connection):
        if held(connection, self.settings):
            raise OperatorError(503, "Recovery review is required before changing account access.")

    def invite_create(self, days=7):
        if type(days) is not int or not 1 <= days <= 30:
            raise OperatorError(422, "Invite validity must be between 1 and 30 days.")
        invite_id, code = random_token(), random_token()
        now = self.clock()
        with self.database.transaction() as connection:
            self._not_held(connection)
            connection.execute("INSERT INTO invites(id,code_hash,created_at,expires_at) VALUES(?,?,?,?)", (invite_id, token_hash(code), storage_time(now), storage_time(now + timedelta(days=days))))
        return {"id": invite_id, "code": code, "created_at": storage_time(now), "expires_at": storage_time(now + timedelta(days=days))}

    def invite_list(self, page=1, limit=20):
        self._pagination(page, limit)
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM invites").fetchone()[0]
            rows = connection.execute("SELECT id,created_at,expires_at,used_at,revoked_at FROM invites ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (limit, (page - 1) * limit))
            return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": limit, "has_next": page * limit < total}

    def invite_revoke(self, invite_id):
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM invites WHERE id=?", (invite_id,)).fetchone() is None:
                raise OperatorError(404, "Invite not found.")
            connection.execute("UPDATE invites SET revoked_at=COALESCE(revoked_at,?) WHERE id=?", (storage_time(self.clock()), invite_id))
        return {"id": invite_id, "revoked": True}

    def user_show(self, user_id):
        with self.database.connect() as connection:
            result = dict(self._user(connection, user_id))
            result["active_keys"] = connection.execute("SELECT COUNT(*) FROM report_keys WHERE user_id=? AND revoked_at IS NULL", (user_id,)).fetchone()[0]
            result["active_sessions"] = connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=? AND expires_at>? AND absolute_expires_at>?", (user_id, storage_time(self.clock()), storage_time(self.clock()))).fetchone()[0]
            return result

    def _withdraw_pending(self, connection, user_id):
        now = storage_time(self.clock())
        connection.execute("UPDATE introductions SET status='withdrawn',resolved_at=? WHERE status='requested' AND (requester_id=? OR recipient_id=?)", (now, user_id, user_id))
        connection.execute("UPDATE notification_events SET cancelled_at=COALESCE(cancelled_at,?) WHERE kind='introduction_request' AND source_id IN (SELECT id FROM introductions WHERE status='withdrawn' AND (requester_id=? OR recipient_id=?))", (now, user_id, user_id))
        connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE event_id IN (SELECT id FROM notification_events WHERE cancelled_at IS NOT NULL) AND state IN ('pending','outbox','sending')")

    def _hide(self, connection, slug):
        posting = connection.execute("SELECT * FROM board_postings WHERE slug=?", (slug,)).fetchone()
        if posting is None:
            raise OperatorError(404, "Posting not found.")
        if not posting["hidden"]:
            changed = touch_board(connection, self.clock)
            connection.execute("UPDATE board_postings SET hidden=1,updated_at=? WHERE slug=?", (changed, slug))
        # Must be terminal in this transaction, even if no participant opens a
        # page between hide and unhide. Existing grants remain exact consent.
        self._withdraw_pending(connection, posting["user_id"])
        return {"slug": slug, "hidden": True}

    def posting_hide(self, slug):
        with self.database.transaction() as connection:
            return self._hide(connection, slug)

    def posting_unhide(self, slug):
        with self.database.transaction() as connection:
            posting = connection.execute("SELECT * FROM board_postings WHERE slug=?", (slug,)).fetchone()
            if posting is None:
                raise OperatorError(404, "Posting not found.")
            if self._user(connection, posting["user_id"])["suspended_at"] is not None:
                raise OperatorError(409, "Review account suspension before unhiding its posting.")
            BoardService._register_checks(connection)
            approved = connection.execute(BoardService._APPROVED + " AND p.slug=?", (slug,)).fetchone()
            if approved is None and posting["active_grant_id"] is not None:
                raise OperatorError(409, "This posting has no current approved version to unhide.")
            if posting["hidden"]:
                changed = touch_board(connection, self.clock)
                connection.execute("UPDATE board_postings SET hidden=0,updated_at=? WHERE slug=?", (changed, slug))
            # A private draft may leave moderation without becoming public.
            # Its owner must still explicitly approve a fresh publication.
            return {"slug": slug, "hidden": False, "visible": approved is not None}

    def user_suspend(self, user_id):
        with self.database.transaction() as connection:
            self._not_held(connection)
            self._user(connection, user_id)
            now = storage_time(self.clock())
            connection.execute("UPDATE users SET suspended_at=COALESCE(suspended_at,?) WHERE id=?", (now, user_id))
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            connection.execute("UPDATE report_keys SET revoked_at=COALESCE(revoked_at,?) WHERE user_id=?", (now, user_id))
            posting = connection.execute("SELECT slug FROM board_postings WHERE user_id=?", (user_id,)).fetchone()
            if posting:
                self._hide(connection, posting[0])
            else:
                self._withdraw_pending(connection, user_id)
            connection.execute("UPDATE conversations SET closed_at=?,closed_reason='unavailable' WHERE closed_at IS NULL AND (requester_id=? OR recipient_id=?)", (now, user_id, user_id))
            connection.execute("UPDATE notification_events SET cancelled_at=COALESCE(cancelled_at,?) WHERE user_id=? OR actor_id=?", (now, user_id, user_id))
            connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE (user_id=? OR event_id IN (SELECT id FROM notification_events WHERE cancelled_at IS NOT NULL)) AND state IN ('pending','outbox','sending')", (user_id,))
            connection.execute("DELETE FROM notification_mail_windows WHERE user_id=? OR conversation_id IN (SELECT id FROM conversations WHERE requester_id=? OR recipient_id=?)", (user_id, user_id, user_id))
            connection.execute("UPDATE notification_preferences SET security_first_login=0,introduction_request=0,introduction_accepted=0,new_message=0,checkin_missed=0,weekly_summary=0 WHERE user_id=?", (user_id,))
            # Ensure even a legacy account cannot regain default-on security mail.
            connection.execute("INSERT OR IGNORE INTO notification_preferences(user_id,security_first_login) VALUES(?,0)", (user_id,))
            connection.execute("INSERT INTO report_settings(user_id,paused) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET paused=1", (user_id,))
            return {"id": user_id, "suspended": True}

    def user_unsuspend(self, user_id):
        with self.database.transaction() as connection:
            self._not_held(connection)
            self._user(connection, user_id)
            connection.execute("UPDATE users SET suspended_at=NULL WHERE id=?", (user_id,))
        return {"id": user_id, "suspended": False}

    def user_delete(self, user_id):
        if self.lifecycle is None:
            raise OperatorError(503, "Account deletion is unavailable.")
        with self.database.transaction() as connection:
            self._user(connection, user_id)
            self.lifecycle.delete_account(user_id, connection=connection)
        # Return no deleted identity in a durable receipt.
        return {"deleted": True}

    def reports_list(self, page=1, limit=20):
        self._pagination(page, limit)
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM abuse_reports").fetchone()[0]
            rows = connection.execute("SELECT id,reporter_id,subject_user_id,target_kind,target_id,reason,created_at FROM abuse_reports ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (limit, (page - 1) * limit))
            return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": limit, "has_next": page * limit < total}

    def report_show(self, report_id):
        with self.database.connect() as connection:
            row = connection.execute("SELECT id,reporter_id,subject_user_id,target_kind,target_id,reason,details,created_at FROM abuse_reports WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise OperatorError(404, "Report not found.")
            return dict(row)

    def numbers(self, days=14):
        """Counts only, for the owner's numbers summary. Never names, emails or IDs."""
        if type(days) is not int or not 1 <= days <= 90:
            raise OperatorError(422, "Choose from 1 to 90 days.")
        now = self.clock()
        since = lambda delta: storage_time(now - delta)
        first_day = (now - timedelta(days=days - 1)).date()
        start = storage_time(datetime.combine(first_day, time(), tzinfo=timezone.utc))

        with self.database.connect() as connection:
            one = lambda sql, *args: connection.execute(sql, args).fetchone()[0]

            def by_day(sql):
                found = {row[0]: row[1] for row in connection.execute(sql, (start,))}
                return {(first_day + timedelta(days=offset)).isoformat(): found.get((first_day + timedelta(days=offset)).isoformat(), 0)
                        for offset in range(days)}

            day, week = since(timedelta(days=1)), since(timedelta(days=7))
            return {
                "as_of": storage_time(now),
                "signups": {
                    "total": one("SELECT COUNT(*) FROM users"),
                    "last_24_hours": one("SELECT COUNT(*) FROM users WHERE created_at>=?", day),
                    "last_7_days": one("SELECT COUNT(*) FROM users WHERE created_at>=?", week),
                    "by_day": by_day("SELECT substr(created_at,1,10),COUNT(*) FROM users WHERE created_at>=? GROUP BY 1"),
                },
                "agents": {
                    "people_with_an_agent_connected": one("SELECT COUNT(DISTINCT user_id) FROM report_keys WHERE revoked_at IS NULL"),
                    "agents_active_last_7_days": one("SELECT COUNT(*) FROM report_keys WHERE revoked_at IS NULL AND last_used_at>=?", week),
                    "connect_links_used": one("SELECT COUNT(*) FROM agent_connect_links WHERE claimed_at IS NOT NULL"),
                    "connected_by_day": by_day("SELECT substr(created_at,1,10),COUNT(*) FROM report_keys WHERE created_at>=? GROUP BY 1"),
                },
                "checkins": {
                    "total": one("SELECT COUNT(*) FROM reports"),
                    "last_7_days": one("SELECT COUNT(*) FROM reports WHERE received_at>=?", week),
                },
                "referrals": {
                    "total": one("SELECT COUNT(*) FROM referrals"),
                    "qualified": one("SELECT COUNT(*) FROM referrals WHERE qualified_at IS NOT NULL"),
                    "flagged": one("SELECT COUNT(*) FROM referrals WHERE flagged_at IS NOT NULL AND cleared_at IS NULL"),
                    "last_7_days": one("SELECT COUNT(*) FROM referrals WHERE entered_at>=?", week),
                },
                "introductions": {
                    "requested": one("SELECT COUNT(*) FROM introductions"),
                    "accepted": one("SELECT COUNT(*) FROM introductions WHERE status='accepted'"),
                },
                "open_conversations": one("SELECT COUNT(*) FROM conversations WHERE closed_at IS NULL"),
            }

    def stats(self):
        with self.database.connect() as connection:
            BoardService._register_checks(connection)
            return {"users": connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                    "suspended_users": connection.execute("SELECT COUNT(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0],
                    "public_postings": connection.execute("SELECT COUNT(*) FROM (" + BoardService._APPROVED + BoardService._VISIBLE + ")").fetchone()[0],
                    "abuse_reports": connection.execute("SELECT COUNT(*) FROM abuse_reports").fetchone()[0],
                    "checkins": connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
                    "open_conversations": connection.execute("SELECT COUNT(*) FROM conversations WHERE closed_at IS NULL").fetchone()[0],
                    "email_states": {row[0]: row[1] for row in connection.execute("SELECT state,COUNT(*) FROM email_outbox GROUP BY state")},
                    "operator_email_configured": bool(getattr(self.settings, "operator_email", "")),
                    "operator_alert_preferences": {kind: bool((connection.execute("SELECT enabled FROM operator_subscriptions WHERE kind=?", (kind,)).fetchone() or [1])[0]) for kind in ("abuse_report", "certificate_expiry")},
                    "recovery_hold": held(connection, self.settings)}
