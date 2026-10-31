"""Owner-bound report operations with atomic retries and durable rolling quotas."""

from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
import math
import re

from .report_keys import bearer_key, key_hash, key_metadata, key_name, new_report_key
from .report_schema import ReportError, canonical_report, parse_json_report, validate_report
from .sessions import random_token, storage_time


class ReportService:
    def __init__(self, database, clock):
        self.database = database
        self.clock = clock

    @staticmethod
    def _owner(connection, user_id):
        row = connection.execute("SELECT suspended_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ReportError(401, "Please sign in again.")
        if row["suspended_at"] is not None:
            raise ReportError(403, "This account is not available.")

    @staticmethod
    def _key(connection, authorization):
        hashed = key_hash(bearer_key(authorization))
        row = connection.execute(
            "SELECT k.*, u.suspended_at FROM report_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?", (hashed,)
        ).fetchone()
        if row is None or not hmac.compare_digest(row["key_hash"], hashed) or row["revoked_at"] is not None or row["suspended_at"] is not None:
            raise ReportError(401, "Use a current report key for an available account.")
        return row

    def authenticate(self, authorization):
        with self.database.transaction() as connection:
            row = self._key(connection, authorization)
            connection.execute("UPDATE report_keys SET last_used_at = ? WHERE id = ? AND user_id = ?",
                               (storage_time(self.clock()), row["id"], row["user_id"]))
            return {"user_id": row["user_id"], "key_id": row["id"]}

    def create_key(self, user_id, name):
        name = key_name(name)
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            count = connection.execute("SELECT COUNT(*) FROM report_keys WHERE user_id = ? AND revoked_at IS NULL", (user_id,)).fetchone()[0]
            if count >= 3:
                raise ReportError(409, "You can have up to three active report keys. Revoke one before creating another.")
            token = new_report_key()
            row = {"id": random_token(), "user_id": user_id, "key_hash": key_hash(token), "last_four": token[-4:], "name": name,
                   "created_at": storage_time(self.clock()), "last_used_at": None, "revoked_at": None}
            connection.execute(
                "INSERT INTO report_keys(id,user_id,key_hash,last_four,name,created_at,agent_api) VALUES(?,?,?,?,?,?,1)",
                (row["id"], user_id, row["key_hash"], row["last_four"], name, row["created_at"]),
            )
        return {**key_metadata(row), "key": token}

    def list_keys(self, user_id):
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            return [key_metadata(row) for row in connection.execute(
                "SELECT * FROM report_keys WHERE user_id = ? ORDER BY created_at DESC, id", (user_id,)
            )]

    def _change_key(self, user_id, key_id, *, name=None, revoke=False):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = connection.execute("SELECT * FROM report_keys WHERE id = ? AND user_id = ?", (key_id, user_id)).fetchone()
            if row is None:
                raise ReportError(404, "Report key not found.")
            if revoke and row["revoked_at"] is None:
                connection.execute("UPDATE report_keys SET revoked_at = ? WHERE id = ? AND user_id = ?", (storage_time(self.clock()), key_id, user_id))
            if name is not None:
                connection.execute("UPDATE report_keys SET name = ? WHERE id = ? AND user_id = ?", (name, key_id, user_id))
            return key_metadata(connection.execute("SELECT * FROM report_keys WHERE id = ? AND user_id = ?", (key_id, user_id)).fetchone())

    def revoke_key(self, user_id, key_id):
        return self._change_key(user_id, key_id, revoke=True)

    def rename_key(self, user_id, key_id, name):
        return self._change_key(user_id, key_id, name=key_name(name))

    @staticmethod
    def _idempotency(value):
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is None:
            raise ReportError(400, "Supply an Idempotency-Key of 1 to 128 letters, digits, dots, underscores, colons or hyphens.")
        return sha256(value.encode("ascii")).hexdigest()

    @staticmethod
    def _replay(connection, scope, nonce_hash, body_hash):
        previous = connection.execute("SELECT body_hash, response_json FROM report_receipts WHERE scope = ? AND idempotency_hash = ?", (scope, nonce_hash)).fetchone()
        if previous is None:
            return None
        if not hmac.compare_digest(previous["body_hash"], body_hash):
            raise ReportError(409, "This Idempotency-Key was already used for a different report body.")
        return json.loads(previous["response_json"])

    @staticmethod
    def _settings(connection, user_id):
        row = connection.execute("SELECT paused, cadence_minutes, next_due_at FROM report_settings WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return {"paused": False, "cadence_minutes": None, "next_due_at": None}
        return {"paused": bool(row["paused"]), "cadence_minutes": row["cadence_minutes"], "next_due_at": row["next_due_at"]}

    @staticmethod
    def _quota(connection, user_id, key_id, source, now):
        cutoff = storage_time(now - timedelta(days=1))
        if source == "key":
            rows = connection.execute("SELECT received_at FROM reports WHERE key_id = ? AND received_at > ? ORDER BY received_at", (key_id, cutoff)).fetchall()
        else:
            rows = connection.execute("SELECT received_at FROM reports WHERE user_id = ? AND source = 'paste' AND received_at > ? ORDER BY received_at", (user_id, cutoff)).fetchall()
        dates = [datetime.fromisoformat(row["received_at"].replace("Z", "+00:00")) for row in rows]
        recent = [date for date in dates if date > now - timedelta(minutes=1)]
        waits = []
        if len(dates) >= 60:
            waits.append((dates[-60] + timedelta(days=1) - now).total_seconds())
        if len(recent) >= 10:
            waits.append((recent[-10] + timedelta(minutes=1) - now).total_seconds())
        if waits:
            raise ReportError(429, "Report limit reached: up to 10 per minute and 60 in any 24 hours. Try again later.", max(1, math.ceil(max(waits))))

    def _save(self, connection, user_id, key_id, source, payload, scope, nonce_hash, body_hash, now):
        settings = self._settings(connection, user_id)
        if settings["paused"]:
            raise ReportError(409, "Report intake is paused. Resume it from your dashboard before sending a new report.")
        self._quota(connection, user_id, key_id, source, now)
        report_id, received_at = random_token(), storage_time(now)
        response = {"report_id": report_id, "received_at": received_at, "status": "received"}
        connection.execute(
            "INSERT INTO reports(id,user_id,key_id,source,kind,payload_json,received_at) VALUES(?,?,?,?,?,?,?)",
            (report_id, user_id, key_id, source, payload["kind"], canonical_report(payload).decode("utf-8"), received_at),
        )
        connection.execute(
            "INSERT INTO report_receipts(scope,idempotency_hash,user_id,key_id,body_hash,report_id,response_json) VALUES(?,?,?,?,?,?,?)",
            (scope, nonce_hash, user_id, key_id, body_hash, report_id, json.dumps(response, sort_keys=True)),
        )
        if key_id is not None:
            connection.execute("UPDATE report_keys SET last_used_at = ? WHERE id = ? AND user_id = ?", (received_at, key_id, user_id))
        if settings["cadence_minutes"] is not None:
            connection.execute("UPDATE report_settings SET next_due_at = ? WHERE user_id = ?", (storage_time(now + timedelta(minutes=settings["cadence_minutes"])), user_id))
        return response

    def ingest(self, authorization, body, idempotency_key):
        # Recheck current authority inside the same write transaction as replay,
        # quota and insertion. A revoked key can never recover an old receipt.
        with self.database.transaction() as connection:
            key = self._key(connection, authorization)
            nonce_hash = self._idempotency(idempotency_key)
            if not isinstance(body, bytes):
                raise ReportError(422, "Report body must be UTF-8 JSON.")
            body_hash = sha256(body).hexdigest()
            scope = "key:" + key["id"]
            previous = self._replay(connection, scope, nonce_hash, body_hash)
            if previous is not None:
                # A successful retry is still use of the current credential.
                # Its original receipt, quota and expected check-in stay fixed.
                connection.execute("UPDATE report_keys SET last_used_at = ? WHERE id = ? AND user_id = ?",
                                   (storage_time(self.clock()), key["id"], key["user_id"]))
                return previous
            payload = parse_json_report(body)
            return self._save(connection, key["user_id"], key["id"], "key", payload, scope, nonce_hash, body_hash, self.clock())

    def save_paste(self, user_id, payload, idempotency_nonce):
        # Preview parsing lives outside this method and has no writes. Only the
        # owner's confirmed, signed preview is passed here by the web adapter.
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            nonce_hash = self._idempotency(idempotency_nonce)
            payload = validate_report(payload)
            body_hash = sha256(canonical_report(payload)).hexdigest()
            scope = "paste:" + user_id
            previous = self._replay(connection, scope, nonce_hash, body_hash)
            if previous is not None:
                return previous
            return self._save(connection, user_id, None, "paste", payload, scope, nonce_hash, body_hash, self.clock())

    @staticmethod
    def _report(row):
        return {"id": row["id"], "source": row["source"], "key_id": row["key_id"], "received_at": row["received_at"],
                "handled_at": row["handled_at"], "payload": json.loads(row["payload_json"])}

    @staticmethod
    def _limit(value):
        if type(value) is not int or not 1 <= value <= 200:
            raise ReportError(422, "Report list limit must be between 1 and 200.")
        return value

    def list_reports(self, user_id, limit=50):
        limit = self._limit(limit)
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            return [self._report(row) for row in connection.execute("SELECT * FROM reports WHERE user_id = ? ORDER BY received_at DESC, rowid DESC LIMIT ?", (user_id, limit))]

    def pending_reports(self, user_id, limit=50):
        limit = self._limit(limit)
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            count = connection.execute("SELECT COUNT(*) FROM reports WHERE user_id = ? AND handled_at IS NULL AND kind IN ('question','connection')", (user_id,)).fetchone()[0]
            rows = connection.execute("SELECT * FROM reports WHERE user_id = ? AND handled_at IS NULL AND kind IN ('question','connection') ORDER BY received_at DESC, rowid DESC LIMIT ?", (user_id, limit))
            return {"total": count, "reports": [self._report(row) for row in rows]}

    def mark_handled(self, user_id, report_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = connection.execute("SELECT * FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id)).fetchone()
            if row is None:
                raise ReportError(404, "Report not found.")
            if row["kind"] == "nothing":
                raise ReportError(422, "This report does not need a decision.")
            if row["handled_at"] is None:
                connection.execute("UPDATE reports SET handled_at = ? WHERE id = ? AND user_id = ?", (storage_time(self.clock()), report_id, user_id))
            return self._report(connection.execute("SELECT * FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id)).fetchone())

    def settings(self, user_id):
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            return self._settings(connection, user_id)

    def update_settings(self, user_id, *, paused, cadence_minutes):
        if type(paused) is not bool or (cadence_minutes is not None and (type(cadence_minutes) is not int or not 60 <= cadence_minutes <= 525600)):
            raise ReportError(422, "Choose whether reports are paused and an explicit cadence from one hour to one year, or no expected cadence.")
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            previous = self._settings(connection, user_id)
            due = previous["next_due_at"]
            if cadence_minutes is None:
                due = None
            elif cadence_minutes != previous["cadence_minutes"] or (previous["paused"] and not paused):
                due = storage_time(self.clock() + timedelta(minutes=cadence_minutes))
            connection.execute(
                "INSERT INTO report_settings(user_id,paused,cadence_minutes,next_due_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET paused=excluded.paused,cadence_minutes=excluded.cadence_minutes,next_due_at=excluded.next_due_at",
                (user_id, int(paused), cadence_minutes, due),
            )
            return self._settings(connection, user_id)
