"""Shared checks for the agent API services: errors, owners, text and limits."""
from datetime import datetime, timedelta
import re
import secrets
import unicodedata

from .sessions import storage_time


class AgentError(ValueError):
    """A fixed, user-safe message with an HTTP status for the agent API."""

    def __init__(self, status, detail, retry_after=None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


APPROVAL_REQUIRED = ("Ask your person first. Send approved_by_owner: true only after they said yes "
                     "to this exact action.")


def require_owner(connection, user_id):
    row = connection.execute("SELECT suspended_at FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise AgentError(401, "Use a current agent key for an available account.")
    if row["suspended_at"] is not None:
        raise AgentError(403, "This account is not available.")


def require_approval(value):
    if value is not True:
        raise AgentError(409, APPROVAL_REQUIRED)


def clean_text(value, label, maximum, *, minimum=1, multiline=False):
    if not isinstance(value, str):
        raise AgentError(422, f"{label} must be text.")
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    allowed = {"\n"} if multiline else set()
    if any((ord(c) < 32 and c not in allowed) or 0xD800 <= ord(c) <= 0xDFFF or unicodedata.category(c) == "Cf" for c in value):
        raise AgentError(422, f"{label} contains control or invisible characters.")
    if not minimum <= len(value) <= maximum:
        raise AgentError(422, f"{label} must be {minimum} to {maximum} characters.")
    return value


def normalize_item(value):
    """Compare taste items and topics by meaning-preserving text only."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def public_id(connection, user_id, now):
    """Return the member's opaque public identifier, creating a profile row once."""
    row = connection.execute("SELECT public_id FROM member_profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is not None:
        return row["public_id"]
    value = "m_" + secrets.token_hex(8)
    connection.execute("INSERT INTO member_profiles(user_id,public_id,updated_at) VALUES(?,?,?)", (user_id, value, storage_time(now)))
    return value


def user_for_public_id(connection, value):
    if not isinstance(value, str) or re.fullmatch(r"m_[0-9a-f]{16}", value) is None:
        return None
    row = connection.execute("SELECT m.user_id FROM member_profiles m JOIN users u ON u.id=m.user_id "
                             "WHERE m.public_id=? AND u.suspended_at IS NULL", (value,)).fetchone()
    return row["user_id"] if row else None


def blocked(connection, left, right):
    return connection.execute(
        "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
        (left, right, right, left)).fetchone() is not None


def daily_limit(connection, table, column, user_id, now, limit, label):
    """Rolling 24-hour count of an owner's rows. Table and column are constants."""
    cutoff = storage_time(now - timedelta(days=1))
    rows = connection.execute(f"SELECT created_at FROM {table} WHERE {column}=? AND created_at>? ORDER BY created_at",
                              (user_id, cutoff)).fetchall()
    if len(rows) >= limit:
        oldest = datetime.fromisoformat(rows[len(rows) - limit]["created_at"].replace("Z", "+00:00"))
        wait = max(1, int((oldest + timedelta(days=1) - now).total_seconds()) + 1)
        raise AgentError(429, f"Limit reached: {limit} {label} in any 24 hours.", wait)
