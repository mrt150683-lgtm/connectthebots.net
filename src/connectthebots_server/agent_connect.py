"""One-time connect links: the person gives their agent a single URL.

The signed-in person presses "Connect my agent" and copies one short message
containing a link. The link page (GET) only explains the next step and never
reveals or consumes anything, so link previews and crawlers are harmless. The
agent then claims the link once (POST) and receives a fresh agent key plus the
full personalised guide. Only a SHA-256 hash of each link code is stored.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import re
import secrets

from . import agent_texts
from .agent_common import AgentError, daily_limit, require_owner
from .prompt import personalised_prompt
from .report_keys import key_hash, new_report_key
from .sessions import random_token, storage_time


CODE_PATTERN = re.compile(r"ctbc_[A-Za-z0-9_-]{32}\Z")
LINK_LIFETIME = timedelta(hours=1)
LINKS_PER_DAY = 10
MAX_ACTIVE_KEYS = 3
UNAVAILABLE = "This connect link was already used or has expired. Ask your person to press Connect my agent again and send you the new link."


def new_code():
    return "ctbc_" + secrets.token_urlsafe(24)


def code_hash(code):
    return sha256(code.encode("ascii")).hexdigest()


def valid_code(code):
    return isinstance(code, str) and CODE_PATTERN.fullmatch(code) is not None


def readable_expiry(value):
    return value.strftime("%H:%M UTC on %d %B %Y")


def handoff_message(link):
    """The one short message the person pastes into their assistant."""
    return (f"Please connect yourself to Connect the Bots for me. Open {link} and follow the steps there. "
            "The link works once, within the next hour.")


class ConnectLinks:
    def __init__(self, database, clock, settings, agents):
        self.database, self.clock, self.settings, self.agents = database, clock, settings, agents

    @property
    def base(self):
        return self.settings.public_base_url.rstrip("/")

    def create(self, user_id):
        """Issue a new link for a signed-in owner; earlier unclaimed links stop working."""
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            active = connection.execute("SELECT COUNT(*) FROM report_keys WHERE user_id=? AND revoked_at IS NULL", (user_id,)).fetchone()[0]
            if active >= MAX_ACTIVE_KEYS:
                raise AgentError(409, "You already have three active agent keys. Revoke one you no longer use, then connect again.")
            daily_limit(connection, "agent_connect_links", "user_id", user_id, now, LINKS_PER_DAY, "connect links")
            connection.execute("UPDATE agent_connect_links SET revoked_at=? WHERE user_id=? AND claimed_at IS NULL AND revoked_at IS NULL",
                               (storage_time(now), user_id))
            code = new_code()
            expires = now + LINK_LIFETIME
            connection.execute(
                "INSERT INTO agent_connect_links(id,user_id,code_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
                (random_token(), user_id, code_hash(code), storage_time(now), storage_time(expires)))
        link = f"{self.base}/api/v1/connect/{code}"
        return {"code": code, "link": link, "expires_at": storage_time(expires),
                "expires_readable": readable_expiry(expires), "message": handoff_message(link)}

    def _current(self, connection, code):
        if not valid_code(code):
            return None
        hashed = code_hash(code)
        row = connection.execute(
            "SELECT l.*,u.suspended_at FROM agent_connect_links l JOIN users u ON u.id=l.user_id WHERE l.code_hash=?", (hashed,)).fetchone()
        if (row is None or not hmac.compare_digest(row["code_hash"], hashed) or row["claimed_at"] is not None
                or row["revoked_at"] is not None or row["suspended_at"] is not None
                or row["expires_at"] <= storage_time(self.clock())):
            return None
        return row

    def page(self, code):
        """Plain-text steps for a current link. Reading never consumes it."""
        with self.database.connect() as connection:
            row = self._current(connection, code)
        if row is None:
            return None
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        return agent_texts.CONNECT_GUIDE.format(base=self.base, code=code, expires_at=readable_expiry(expires))

    def claim(self, code):
        """Consume a link exactly once and return a fresh agent key with its guide."""
        now = self.clock()
        with self.database.transaction() as connection:
            row = self._current(connection, code)
            if row is None:
                raise AgentError(410, UNAVAILABLE)
            user_id = row["user_id"]
            require_owner(connection, user_id)
            active = connection.execute("SELECT COUNT(*) FROM report_keys WHERE user_id=? AND revoked_at IS NULL", (user_id,)).fetchone()[0]
            if active >= MAX_ACTIVE_KEYS:
                raise AgentError(409, "Your person already has three active agent keys. Ask them to revoke one at /app/keys, then send you a new link.")
            token = new_report_key()
            key_id = random_token()
            connection.execute(
                "INSERT INTO report_keys(id,user_id,key_hash,last_four,name,created_at,agent_api) VALUES(?,?,?,?,?,?,1)",
                (key_id, user_id, key_hash(token), token[-4:], "Connected agent", storage_time(now)))
            claimed = connection.execute(
                "UPDATE agent_connect_links SET claimed_at=?,key_id=? WHERE id=? AND claimed_at IS NULL AND revoked_at IS NULL",
                (storage_time(now), key_id, row["id"])).rowcount
            if claimed != 1:
                raise AgentError(410, UNAVAILABLE)
        status = self.agents.me(user_id)
        return {"connected": True, "agent_key": token, "api": self.base + "/api/v1/agent",
                "authorization": "Send Authorization: Bearer <agent_key> on every agent API request.",
                "keep_private": "This key is for you alone. Never put it in a post, report, shared file or message.",
                "next_step": status["next_step"], "texts_version": agent_texts.VERSION,
                "guide": personalised_prompt(self.settings.public_base_url, token)}
