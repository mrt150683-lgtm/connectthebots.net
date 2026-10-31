"""Hashed sessions and purpose-bound CSRF tokens; no Google token storage."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import re
import secrets

from .clock import Clock, timestamp as validate_timestamp
from .db import Database


SESSION_COOKIE = "__Host-ctb_session"
PREAUTH_COOKIE = "__Host-ctb_preauth"
SESSION_SLIDING = timedelta(days=30)
SESSION_ABSOLUTE = timedelta(days=90)
PREAUTH_SECONDS = 600
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
MAC_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def storage_time(value: datetime) -> str:
    """Fixed precision keeps UTC dates correctly ordered in SQLite TEXT columns."""
    validate_timestamp(value)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def token_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


def valid_token(value: str | None) -> bool:
    return isinstance(value, str) and TOKEN_PATTERN.fullmatch(value) is not None


def _mac(secret: str, purpose: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), (purpose + "\0" + value).encode("utf-8"), "sha256").hexdigest()


@dataclass(frozen=True)
class Session:
    user_id: str
    email: str
    display_name: str
    csrf: str
    expires_at: str


class Sessions:
    def __init__(self, database: Database, clock: Clock, secret: str):
        self.database = database
        self.clock = clock
        self.secret = secret

    def create(self, connection, user_id: str, previous_token: str | None = None) -> str:
        """Call inside the account transaction so identity and session commit together."""
        now = self.clock()
        token = random_token()
        if valid_token(previous_token):
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(previous_token),))
        connection.execute("DELETE FROM sessions WHERE expires_at <= ? OR absolute_expires_at <= ?", (storage_time(now), storage_time(now)))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, absolute_expires_at) VALUES (?, ?, ?, ?, ?)",
            (token_hash(token), user_id, storage_time(now), storage_time(now + SESSION_SLIDING), storage_time(now + SESSION_ABSOLUTE)),
        )
        return token

    def current(self, token: str | None, *, refresh: bool = True) -> Session | None:
        if not valid_token(token):
            return None
        now = self.clock()
        hashed = token_hash(token)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT s.token_hash, s.user_id, s.expires_at, s.absolute_expires_at, u.email, u.display_name, u.suspended_at "
                "FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.token_hash = ?", (hashed,)
            ).fetchone()
            if row is None or not hmac.compare_digest(hashed, row["token_hash"]):
                return None
            if row["suspended_at"] is not None or row["expires_at"] <= storage_time(now) or row["absolute_expires_at"] <= storage_time(now):
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashed,))
                return None
            expiry = row["expires_at"]
            if refresh:
                expiry = min(storage_time(now + SESSION_SLIDING), row["absolute_expires_at"])
                connection.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (expiry, hashed))
            return Session(row["user_id"], row["email"], row["display_name"], _mac(self.secret, "session-csrf", token), expiry)

    def csrf_valid(self, session: Session, supplied: str | None) -> bool:
        return isinstance(supplied, str) and MAC_PATTERN.fullmatch(supplied) is not None and hmac.compare_digest(session.csrf, supplied)

    def revoke(self, token: str | None) -> None:
        if valid_token(token):
            with self.database.transaction() as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def preauth(self, existing: str | None = None) -> tuple[str, str]:
        """A signed expiring pre-login cookie enables forms without a user account."""
        if not self.preauth_valid(existing):
            body = random_token() + "." + str(int(self.clock().timestamp()) + PREAUTH_SECONDS)
            existing = body + "." + _mac(self.secret, "preauth-cookie", body)
        return existing, _mac(self.secret, "preauth-csrf", existing)

    def preauth_valid(self, cookie: str | None, supplied: str | None = None) -> bool:
        if not isinstance(cookie, str) or len(cookie) > 140:
            return False
        parts = cookie.split(".")
        if len(parts) != 3 or not valid_token(parts[0]) or not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 11 or MAC_PATTERN.fullmatch(parts[2]) is None:
            return False
        body = ".".join(parts[:2])
        now = int(self.clock().timestamp())
        if not now < int(parts[1]) <= now + PREAUTH_SECONDS:
            return False
        if not hmac.compare_digest(parts[2], _mac(self.secret, "preauth-cookie", body)):
            return False
        return supplied is None or (
            isinstance(supplied, str) and MAC_PATTERN.fullmatch(supplied) is not None
            and hmac.compare_digest(_mac(self.secret, "preauth-csrf", cookie), supplied)
        )


def set_session_cookie(response, token: str, max_age: int = int(SESSION_SLIDING.total_seconds())) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, secure=True, httponly=True, samesite="lax", path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
