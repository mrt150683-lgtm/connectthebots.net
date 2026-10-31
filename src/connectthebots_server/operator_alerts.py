"""Private operator alerts share SMTP delivery machinery, never account prefs."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import hmac
import json
import re
import socket
import ssl
from urllib.parse import urlsplit

from .sessions import random_token, storage_time

KINDS = {"abuse_report": "Moderation report alerts", "certificate_expiry": "Certificate expiry alerts"}


class OperatorAlertError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def held(connection, settings):
    from .recovery import connection_held
    return connection_held(connection, settings)


class OperatorAlerts:
    def __init__(self, database, clock, settings):
        self.database, self.clock, self.settings = database, clock, settings

    @property
    def recipient(self):
        return getattr(self.settings, "operator_email", "")

    def _recipient_hash(self):
        return hashlib.sha256(self.recipient.encode("utf-8")).hexdigest()

    def _subscription(self, connection, kind):
        row = connection.execute("SELECT * FROM operator_subscriptions WHERE kind=?", (kind,)).fetchone()
        recipient_hash = self._recipient_hash()
        if row is None or row["recipient_hash"] != recipient_hash:
            connection.execute("INSERT INTO operator_subscriptions(kind,id,recipient_hash,enabled) VALUES(?,?,?,1) ON CONFLICT(kind) DO UPDATE SET id=excluded.id,recipient_hash=excluded.recipient_hash,enabled=1", (kind, random_token(), recipient_hash))
            row = connection.execute("SELECT * FROM operator_subscriptions WHERE kind=?", (kind,)).fetchone()
        value = "operator." + row["id"] + "." + kind
        signature = hmac.new(self.settings.session_secret.encode("utf-8"), ("operator-unsubscribe-v1\0" + value).encode("ascii"), "sha256").hexdigest()
        return value + "." + signature, bool(row["enabled"])

    def unsubscribe_token(self, kind):
        if kind not in KINDS:
            raise OperatorAlertError(400, "This unsubscribe link is invalid.")
        with self.database.transaction() as connection:
            return self._subscription(connection, kind)[0]

    def _token(self, connection, token):
        if not isinstance(token, str) or len(token) > 180:
            raise OperatorAlertError(400, "This unsubscribe link is invalid.")
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "operator" or re.fullmatch(r"[A-Za-z0-9_-]{43}", parts[1]) is None or parts[2] not in KINDS or re.fullmatch(r"[a-f0-9]{64}", parts[3]) is None:
            raise OperatorAlertError(400, "This unsubscribe link is invalid.")
        expected = hmac.new(self.settings.session_secret.encode("utf-8"), ("operator-unsubscribe-v1\0" + ".".join(parts[:3])).encode("ascii"), "sha256").hexdigest()
        if not hmac.compare_digest(expected, parts[3]):
            raise OperatorAlertError(400, "This unsubscribe link is invalid.")
        row = connection.execute("SELECT * FROM operator_subscriptions WHERE id=? AND kind=? AND recipient_hash=?", (parts[1], parts[2], self._recipient_hash())).fetchone()
        if row is None:
            raise OperatorAlertError(400, "This unsubscribe link is invalid.")
        return row

    def token_info(self, token):
        with self.database.connect() as connection:
            row = self._token(connection, token)
            return {"kind": row["kind"], "label": KINDS[row["kind"]]}

    def unsubscribe(self, token):
        with self.database.transaction() as connection:
            row = self._token(connection, token)
            connection.execute("UPDATE operator_subscriptions SET enabled=0 WHERE kind=?", (row["kind"],))
            connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE operator_alert_id IN (SELECT id FROM operator_alerts WHERE kind=?) AND state IN ('pending','outbox')", (row["kind"],))
            return {"kind": row["kind"], "label": KINDS[row["kind"]], "unsubscribed": True}

    def set_enabled(self, kind, enabled):
        if kind not in KINDS or type(enabled) is not bool:
            raise OperatorAlertError(422, "Choose a known operator alert and on or off.")
        with self.database.transaction() as connection:
            token, _ = self._subscription(connection, kind)
            connection.execute("UPDATE operator_subscriptions SET enabled=? WHERE kind=?", (int(enabled), kind))
            if not enabled:
                connection.execute("UPDATE email_outbox SET state='cancelled',lease_id=NULL,lease_until=NULL WHERE operator_alert_id IN (SELECT id FROM operator_alerts WHERE kind=?) AND state IN ('pending','outbox')", (kind,))
            return {"kind": kind, "enabled": enabled}

    def eligible(self, connection, alert):
        if alert is None or not self.recipient or held(connection, self.settings):
            return False
        subscription = connection.execute("SELECT enabled,recipient_hash FROM operator_subscriptions WHERE kind=?", (alert["kind"],)).fetchone()
        if subscription is None or not subscription["enabled"] or subscription["recipient_hash"] != self._recipient_hash():
            return False
        if alert["kind"] == "abuse_report":
            return connection.execute("SELECT 1 FROM abuse_reports WHERE id=?", (alert["report_id"],)).fetchone() is not None
        if alert["kind"] == "certificate_expiry":
            checked = connection.execute("SELECT * FROM operator_certificate_state WHERE singleton=1").fetchone()
            return checked is not None and bool(checked["verified"]) and checked["expires_at"] == alert["certificate_expires_at"]
        return False

    def render(self, connection, alert, outbox_id):
        token, _ = self._subscription(connection, alert["kind"])
        unsubscribe = self.settings.public_base_url + "/email/unsubscribe/" + token
        if alert["kind"] == "abuse_report":
            subject = "A moderation report needs review."
            text = subject + "\n\nReport ID: " + alert["report_id"] + "\nReview it using the operator CLI on the server."
        else:
            subject = "The service certificate expires soon."
            text = subject + "\n\nExpiry: " + alert["certificate_expires_at"] + "\nReview certificate renewal on the server."
        text += "\n\nStop these operator alerts:\n" + unsubscribe + "\n"
        domain = urlsplit(self.settings.public_base_url).hostname
        return {"subject": subject, "text": text, "headers": {"List-Unsubscribe": "<" + unsubscribe + ">", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click", "Message-ID": "<" + outbox_id + "@" + domain + ">", "Date": format_datetime(self.clock())}}

    def _record(self, connection, kind, dedupe_key, *, report_id=None, expires_at=None):
        prior = connection.execute("SELECT id FROM operator_alerts WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        if prior:
            return prior[0]
        alert_id, now = random_token(), storage_time(self.clock())
        connection.execute("INSERT INTO operator_alerts(id,kind,report_id,dedupe_key,created_at,certificate_expires_at) VALUES(?,?,?,?,?,?)", (alert_id, kind, report_id, dedupe_key, now, expires_at))
        alert = connection.execute("SELECT * FROM operator_alerts WHERE id=?", (alert_id,)).fetchone()
        self._subscription(connection, kind)
        if self.eligible(connection, alert):
            outbox_id = random_token()
            payload = self.render(connection, alert, outbox_id)
            state = "outbox" if self.settings.email_mode == "outbox" else "pending"
            connection.execute("INSERT INTO email_outbox(id,user_id,kind,created_at,payload_json,state,available_at,operator_alert_id) VALUES(?,NULL,?,?,?,?,?,?)", (outbox_id, "operator_" + kind, now, json.dumps(payload), state, now, alert_id))
        return alert_id

    def record_report(self, connection, report_id):
        return self._record(connection, "abuse_report", "report:" + report_id, report_id=report_id)

    @staticmethod
    def _certificate(hostname, port):
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as secured:
                certificate = secured.getpeercert()
                expiry = datetime.fromtimestamp(ssl.cert_time_to_seconds(certificate["notAfter"]), timezone.utc)
                fingerprint = hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest()
        return expiry, fingerprint

    def check_certificate(self):
        address = urlsplit(self.settings.public_base_url)
        if address.scheme != "https" or not address.hostname:
            raise OperatorAlertError(503, "Certificate checking requires the configured HTTPS service address.")
        try:
            expiry, fingerprint = self._certificate(address.hostname, address.port or 443)
            now = self.clock()
            if expiry <= now:
                raise ValueError("expired")
        except Exception:
            with self.database.transaction() as connection:
                connection.execute("INSERT INTO operator_certificate_state(singleton,checked_at,verified) VALUES(1,?,0) ON CONFLICT(singleton) DO UPDATE SET fingerprint=NULL,expires_at=NULL,checked_at=excluded.checked_at,verified=0", (storage_time(self.clock()),))
            raise OperatorAlertError(503, "Certificate verification failed. Check renewal and connectivity on the server.") from None
        alert_id = None
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO operator_certificate_state(singleton,fingerprint,expires_at,checked_at,verified) VALUES(1,?,?,?,1) ON CONFLICT(singleton) DO UPDATE SET fingerprint=excluded.fingerprint,expires_at=excluded.expires_at,checked_at=excluded.checked_at,verified=1", (fingerprint, storage_time(expiry), storage_time(now)))
            if expiry <= now + timedelta(days=14):
                week = int(now.timestamp()) // (7 * 86400)
                alert_id = self._record(connection, "certificate_expiry", "certificate:" + fingerprint + ":" + str(week), expires_at=storage_time(expiry))
        return {"verified": True, "expires_at": storage_time(expiry), "alert_recorded": alert_id is not None}
