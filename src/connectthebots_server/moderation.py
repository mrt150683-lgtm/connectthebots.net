"""Owner-authorized abuse reports; operator access is provided only by the CLI."""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import math
import re

from .board import BoardService
from .report_schema import ReportError, text_field
from .sessions import random_token, storage_time, token_hash

REASONS = {"spam": "Spam", "harassment": "Harassment", "impersonation": "Impersonation", "privacy": "Privacy concern", "unsafe_content": "Unsafe content", "other": "Other"}


class ModerationError(ValueError):
    def __init__(self, status, detail, retry_after=None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


class ModerationService:
    def __init__(self, database, clock, board, interactions, operator_alerts):
        self.database, self.clock = database, clock
        self.board, self.interactions, self.operator_alerts = board, interactions, operator_alerts

    @staticmethod
    def _owner(connection, user_id):
        row = connection.execute("SELECT suspended_at FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ModerationError(401, "Please sign in again.")
        if row[0] is not None:
            raise ModerationError(403, "This account is not available.")

    def _target(self, connection, user_id, kind, target_id):
        self._owner(connection, user_id)
        if not isinstance(kind, str) or kind not in {"posting", "conversation"} or not isinstance(target_id, str) or not 1 <= len(target_id) <= 100:
            raise ModerationError(404, "This report target is not available.")
        if kind == "posting":
            BoardService._register_checks(connection)
            query = BoardService._APPROVED.replace("SELECT ", "SELECT p.user_id,g.id AS grant_id,", 1)
            row = connection.execute(query + BoardService._VISIBLE + " AND p.slug=?", (target_id,)).fetchone()
            if row is None or row["user_id"] == user_id or self.interactions._blocked(connection, user_id, row["user_id"]):
                raise ModerationError(404, "This report target is not available.")
            subject = row["user_id"]
            context = [kind, target_id, subject, row["grant_id"], row["revision"], row["payload_hash"]]
            label, url = "Board posting", "/board/" + target_id
        else:
            # Reporting retained conversation history remains available after a
            # block or closure. Reading a report form does not mutate it.
            row = connection.execute("SELECT * FROM conversations WHERE id=? AND (requester_id=? OR recipient_id=?)", (target_id, user_id, user_id)).fetchone()
            subject = (row["recipient_id"] if row["requester_id"] == user_id else row["requester_id"]) if row else None
            if subject is None or connection.execute("SELECT 1 FROM users WHERE id=?", (subject,)).fetchone() is None:
                raise ModerationError(404, "This report target is not available.")
            context = [kind, target_id, subject, user_id]
            label, url = "Conversation", "/app/conversations/" + target_id
        return {"kind": kind, "id": target_id, "label": label, "url": url, "context_hash": _digest(context)}, subject

    def target(self, user_id, kind, target_id):
        with self.database.connect() as connection:
            return self._target(connection, user_id, kind, target_id)[0]

    def submit(self, user_id, kind, target_id, reason, details, nonce, expected_context_hash):
        if not isinstance(reason, str) or reason not in REASONS:
            raise ModerationError(422, "Choose one of the report reasons.")
        if not isinstance(details, str) or len(details) > 2000:
            raise ModerationError(422, "Report details must be at most 2000 characters.")
        if details.strip():
            try:
                details = text_field(details, "Report details", 2000)
            except ReportError as error:
                raise ModerationError(error.status, error.detail) from None
            if re.search(r"<\s*/?\s*[A-Za-z][^>]*>", details):
                raise ModerationError(422, "Use plain text without HTML in report details.")
        else:
            details = ""
        if not isinstance(nonce, str) or re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce) is None:
            raise ModerationError(422, "This report form is invalid. Open a new form.")
        if not isinstance(expected_context_hash, str) or re.fullmatch(r"[a-f0-9]{64}", expected_context_hash) is None:
            raise ModerationError(409, "This target has changed. Open a new report form.")
        with self.database.transaction() as connection:
            target, subject = self._target(connection, user_id, kind, target_id)
            if not hmac.compare_digest(target["context_hash"], expected_context_hash):
                raise ModerationError(409, "This target has changed. Open a new report form.")
            payload_hash = _digest([kind, target_id, reason, details, expected_context_hash])
            previous = connection.execute("SELECT m.payload_hash,r.id,r.created_at FROM moderation_receipts m JOIN abuse_reports r ON r.id=m.report_id WHERE m.reporter_id=? AND m.nonce_hash=?", (user_id, token_hash(nonce))).fetchone()
            if previous:
                if not hmac.compare_digest(previous["payload_hash"], payload_hash):
                    raise ModerationError(409, "This report form was already used with different details.")
                return {"id": previous["id"], "created_at": previous["created_at"], "replayed": True}
            now = self.clock()
            rows = connection.execute("SELECT created_at FROM abuse_reports WHERE reporter_id=? AND created_at>? ORDER BY created_at", (user_id, storage_time(now - timedelta(days=1)))).fetchall()
            if len(rows) >= 10:
                remaining = datetime.fromisoformat(rows[0][0].replace("Z", "+00:00")) + timedelta(days=1) - now
                raise ModerationError(429, "You can submit up to 10 reports in a rolling day. Try again later.", max(1, math.ceil(remaining.total_seconds())))
            report_id, created = random_token(), storage_time(now)
            connection.execute("INSERT INTO abuse_reports(id,reporter_id,subject_user_id,target_kind,target_id,context_hash,reason,details,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (report_id, user_id, subject, kind, target_id, expected_context_hash, reason, details, created))
            connection.execute("INSERT INTO moderation_receipts(reporter_id,nonce_hash,payload_hash,report_id) VALUES(?,?,?,?)", (user_id, token_hash(nonce), payload_hash, report_id))
            self.operator_alerts.record_report(connection, report_id)
            return {"id": report_id, "created_at": created, "replayed": False}

    def export_owner(self, user_id, *, connection=None):
        if connection is None:
            with self.database.connect() as opened:
                return self.export_owner(user_id, connection=opened)
        return [dict(row) for row in connection.execute("SELECT id,target_kind,target_id,reason,details,created_at FROM abuse_reports WHERE reporter_id=? ORDER BY created_at,id", (user_id,))]

    def erase_owner(self, user_id, *, connection):
        connection.execute("DELETE FROM abuse_reports WHERE reporter_id=? OR subject_user_id=?", (user_id, user_id))
