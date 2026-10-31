"""Referral codes, anti-abuse checks, qualification, perks and early access.

A referral counts only after the new member completes the interview (or, if
the operator configures it, gets a first match). Self-referral is impossible,
entries per code are rate limited, and bursts of entries on one code are
flagged for an operator instead of counting.
"""
from datetime import datetime, timedelta
import math
import re
import secrets

from . import growth_config
from .agent_common import AgentError, require_owner
from .sessions import random_token, storage_time


ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, 1/I/L
CODE = re.compile(r"CTB-[" + ALPHABET + r"]{6}\Z")


def normalize_code(value):
    """Accept 'ctb-7k3qxm', 'CTB 7K3QXM' or '7K3QXM'; return the canonical form."""
    if not isinstance(value, str) or len(value) > 20:
        return None
    compact = re.sub(r"[\s-]", "", value).upper()
    if compact.startswith("CTB"):
        compact = compact[3:]
    candidate = "CTB-" + compact
    return candidate if CODE.fullmatch(candidate) else None


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ensure_code(connection, user_id, now):
    row = connection.execute("SELECT code FROM referral_codes WHERE user_id=?", (user_id,)).fetchone()
    if row is not None:
        return row["code"]
    for _ in range(20):
        code = "CTB-" + "".join(secrets.choice(ALPHABET) for _ in range(6))
        if connection.execute("SELECT 1 FROM referral_codes WHERE code=?", (code,)).fetchone() is None:
            connection.execute("INSERT INTO referral_codes(user_id,code,created_at) VALUES(?,?,?)", (user_id, code, storage_time(now)))
            return code
    raise AgentError(503, "A referral code could not be created. Please try again.")


def features(connection, user_id, now, config):
    active = early_access_active(connection, user_id, now)
    return sorted(name for name in growth_config.FEATURES
                  if config["feature." + name] == "everyone" or (config["feature." + name] == "early_access" and active))


def early_access_active(connection, user_id, now):
    return connection.execute("SELECT 1 FROM early_access_grants WHERE user_id=? AND expires_at>?",
                              (user_id, storage_time(now))).fetchone() is not None


class ReferralService:
    def __init__(self, database, clock):
        self.database, self.clock = database, clock

    def enter(self, connection, referred_id, raw_code, via, config):
        """Record a code entered at sign-up or by the new member's agent."""
        now = self.clock()
        code = normalize_code(raw_code)
        owner = connection.execute("SELECT user_id FROM referral_codes WHERE code=?", (code,)).fetchone() if code else None
        if owner is None:
            raise AgentError(404, "That referral code is not valid.")
        if owner["user_id"] == referred_id:
            raise AgentError(409, "You cannot use your own referral code.")
        if connection.execute("SELECT 1 FROM referrals WHERE referred_id=?", (referred_id,)).fetchone():
            raise AgentError(409, "A referral code is already recorded for this account.")
        if via == "agent":
            account = connection.execute("SELECT created_at FROM users WHERE id=?", (referred_id,)).fetchone()
            state = connection.execute("SELECT completed_at FROM interview_state WHERE user_id=?", (referred_id,)).fetchone()
            if (state is not None and state["completed_at"] is not None) or \
                    _parse(account["created_at"]) + timedelta(days=config["referral_entry_window_days"]) < now:
                raise AgentError(409, "A referral code can be added only during sign-up or before the interview is complete.")
        day = connection.execute("SELECT COUNT(*) FROM referrals WHERE code=? AND entered_at>?",
                                 (code, storage_time(now - timedelta(days=1)))).fetchone()[0]
        flag = None
        if day >= config["referral_code_daily_limit"]:
            if via == "agent":
                raise AgentError(429, "This referral code has reached its daily limit. Try again tomorrow.", 3600)
            # Never block an account sign-up over a referral. Keep it, flagged.
            flag = "rate_limit"
        window = storage_time(now - timedelta(minutes=config["referral_cluster_window_minutes"]))
        recent = connection.execute("SELECT COUNT(*) FROM referrals WHERE code=? AND entered_at>?", (code, window)).fetchone()[0]
        if recent + 1 >= config["referral_cluster_threshold"]:
            flag = flag or "cluster"
            connection.execute("UPDATE referrals SET flagged_at=?,flag_reason='cluster' WHERE code=? AND entered_at>? "
                               "AND flagged_at IS NULL AND qualified_at IS NULL", (storage_time(now), code, window))
        referral_id = random_token()
        connection.execute(
            "INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at,flagged_at,flag_reason) VALUES(?,?,?,?,?,?,?,?)",
            (referral_id, owner["user_id"], referred_id, code, via, storage_time(now), storage_time(now) if flag else None, flag))
        self._try_qualify(connection, referred_id, config)
        return self.status(connection, referred_id)

    def enter_from_agent(self, user_id, raw_code):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            return self.enter(connection, user_id, raw_code, "agent", growth_config.load(connection))

    @staticmethod
    def _condition(connection, user_id, config):
        if config["referral_qualifies_on"] == "interview":
            row = connection.execute("SELECT completed_at FROM interview_state WHERE user_id=?", (user_id,)).fetchone()
            return row is not None and row["completed_at"] is not None
        return connection.execute(
            "SELECT 1 FROM taste_matches_seen WHERE user_id=? OR other_user_id=? UNION ALL "
            "SELECT 1 FROM introductions WHERE status='accepted' AND (requester_id=? OR recipient_id=?) LIMIT 1",
            (user_id, user_id, user_id, user_id)).fetchone() is not None

    def _try_qualify(self, connection, referred_id, config):
        row = connection.execute("SELECT * FROM referrals WHERE referred_id=? AND qualified_at IS NULL "
                                 "AND (flagged_at IS NULL OR cleared_at IS NOT NULL)", (referred_id,)).fetchone()
        if row is None or not self._condition(connection, referred_id, config):
            return False
        now = self.clock()
        connection.execute("UPDATE referrals SET qualified_at=? WHERE id=?", (storage_time(now), row["id"]))
        if config["referral_perk_days"] > 0:
            expires = storage_time(now + timedelta(days=config["referral_perk_days"]))
            for member in (row["referrer_id"], row["referred_id"]):
                connection.execute(
                    "INSERT OR IGNORE INTO early_access_grants(user_id,source,reference,granted_at,expires_at) VALUES(?,?,?,?,?)",
                    (member, "referral_perk", row["id"], storage_time(now), expires))
        return True

    def on_event(self, connection, user_id, event, config):
        if config["referral_qualifies_on"] == event:
            self._try_qualify(connection, user_id, config)

    def status(self, connection, user_id):
        now = self.clock()
        config = growth_config.load(connection)
        code = ensure_code(connection, user_id, now)
        mine = connection.execute("SELECT code,entered_at,qualified_at,flagged_at IS NOT NULL AND cleared_at IS NULL AS held "
                                  "FROM referrals WHERE referred_id=?", (user_id,)).fetchone()
        counts = connection.execute(
            "SELECT COUNT(*) AS entered, SUM(qualified_at IS NOT NULL) AS qualified, "
            "SUM(qualified_at IS NULL AND flagged_at IS NOT NULL AND cleared_at IS NULL) AS under_review "
            "FROM referrals WHERE referrer_id=?", (user_id,)).fetchone()
        grants = [dict(row) for row in connection.execute(
            "SELECT source,granted_at,expires_at FROM early_access_grants WHERE user_id=? AND expires_at>? ORDER BY expires_at DESC",
            (user_id, storage_time(now)))]
        return {"code": code, "qualifies_on": config["referral_qualifies_on"],
                "referred_by": None if mine is None else {"code": mine["code"], "entered_at": mine["entered_at"],
                                                          "qualified_at": mine["qualified_at"], "under_review": bool(mine["held"])},
                "referred": {"entered": counts["entered"], "qualified": counts["qualified"] or 0,
                             "under_review": counts["under_review"] or 0},
                "early_access": {"active": bool(grants), "grants": grants},
                "features": features(connection, user_id, now, config)}

    def summary(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            return self.status(connection, user_id)

    # Operator-only (CLI). There are no HTTP routes for these.
    def flagged(self, connection):
        return [dict(row) for row in connection.execute(
            "SELECT id,code,entered_via,entered_at,flagged_at,flag_reason FROM referrals "
            "WHERE flagged_at IS NOT NULL AND cleared_at IS NULL ORDER BY flagged_at,id LIMIT 200")]

    def clear(self, connection, referral_id):
        row = connection.execute("SELECT * FROM referrals WHERE id=?", (referral_id,)).fetchone()
        if row is None:
            raise AgentError(404, "Referral not found.")
        connection.execute("UPDATE referrals SET cleared_at=COALESCE(cleared_at,?) WHERE id=?", (storage_time(self.clock()), referral_id))
        qualified = self._try_qualify(connection, row["referred_id"], growth_config.load(connection))
        return {"id": referral_id, "cleared": True, "qualified_now": qualified}

    def flag(self, connection, referral_id):
        changed = connection.execute("UPDATE referrals SET flagged_at=?,flag_reason='operator',cleared_at=NULL WHERE id=? AND qualified_at IS NULL",
                                     (storage_time(self.clock()), referral_id)).rowcount
        if not changed:
            raise AgentError(409, "Only an unqualified referral can be flagged.")
        return {"id": referral_id, "flagged": True}


def refresh_early_access(connection, now, config, leaderboard_rows, *, force=False):
    """Daily: the top X% of the referral leaderboard get early access.

    Runs lazily inside a write transaction at most once per 24 hours (or on
    operator demand). The percentage is read from config at that moment.
    """
    state = connection.execute("SELECT calculated_at FROM early_access_state WHERE singleton=1").fetchone()
    if not force and state["calculated_at"] is not None and _parse(state["calculated_at"]) + timedelta(days=1) > now:
        return False
    connection.execute("DELETE FROM early_access_grants WHERE source='leaderboard'")
    ranked = [row for row in leaderboard_rows if row["value"] > 0]
    count = math.ceil(len(ranked) * config["early_access_percent"] / 100) if ranked and config["early_access_percent"] else 0
    reference = now.date().isoformat()
    for row in ranked[:count]:
        connection.execute("INSERT INTO early_access_grants(user_id,source,reference,granted_at,expires_at) VALUES(?,?,?,?,?)",
                           (row["user_id"], "leaderboard", reference, storage_time(now), storage_time(now + timedelta(days=2))))
    connection.execute("UPDATE early_access_state SET calculated_at=? WHERE singleton=1", (storage_time(now),))
    return True
