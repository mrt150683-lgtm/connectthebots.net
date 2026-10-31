"""Referral campaign: public places, standing share permission and rank targets.

A person can let their agent post their referral link in a few public places
they name, up to a weekly limit, without asking each time. Everything else
still needs a yes to the exact post. The server cannot see outside posts: a
recorded share is the agent's statement, and the limits below are what the
server can enforce (places, weekly count, no repeat in the same place).
"""
from datetime import timedelta
import json
import math
from urllib.parse import quote, urlsplit

from .agent_common import AgentError, require_approval, require_owner
from .sessions import storage_time


MAX_PLACES = 5
REPEAT_DAYS = 30
OWN_HOSTS = {"connectthebots.net", "www.connectthebots.net"}


def normalize_place(value, own_host=None):
    """Return a canonical https URL for a public place, or raise 422."""
    if not isinstance(value, str) or not 8 < len(value.strip()) <= 300:
        raise AgentError(422, "A place must be an https link of up to 300 characters.")
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        raise AgentError(422, "A place must be a valid https link.") from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or "." not in host or parts.username or parts.password or port not in (None, 443) \
            or any(ord(c) < 33 for c in value.strip()):
        raise AgentError(422, "A place must be a public https link, such as https://forum.example/c/makers.")
    if host in OWN_HOSTS or host == own_host:
        raise AgentError(422, "Never share referral codes on Connect the Bots itself. Choose a place elsewhere.")
    path = parts.path.rstrip("/")
    return f"https://{host}{path}" + (f"?{parts.query}" if parts.query else "")


SHARE_TEXT = ("I'm on Connect the Bots: my AI assistant finds people who share my taste, "
              "and we're only introduced if we both say yes. Join with my link and we both get early access.")


def share_options(link, text=SHARE_TEXT):
    """One-tap share targets for a member's /r/ link. Pure: no network, no tracking.

    Each network URL opens that network's own share screen with the text and link
    filled in; nothing is posted until the person (or their agent, with their yes)
    presses post there. The QR code is drawn by our own server.
    """
    with_link = text if link in text else f"{text} {link}"
    bare = " ".join(text.replace(link, "").split())  # networks that take the link separately

    def q(value):
        return quote(value, safe="")

    return {
        "link": link,
        "text": text,
        "qr": {"svg": f"{link}/qr.svg", "png": f"{link}/qr.png", "download": f"{link}/qr.png?download=1"},
        "networks": [
            {"id": "whatsapp", "name": "WhatsApp", "url": f"https://wa.me/?text={q(with_link)}"},
            {"id": "instagram", "name": "Instagram", "url": None,
             "how": "Instagram has no share link. Save the QR image or copy the link, then add it to a story with the link sticker, or to the bio."},
            {"id": "facebook", "name": "Facebook", "url": f"https://www.facebook.com/sharer/sharer.php?u={q(link)}"},
            {"id": "x", "name": "X", "url": f"https://x.com/intent/post?text={q(bare)}&url={q(link)}"},
            {"id": "bluesky", "name": "Bluesky", "url": f"https://bsky.app/intent/compose?text={q(with_link)}"},
            {"id": "linkedin", "name": "LinkedIn", "url": f"https://www.linkedin.com/sharing/share-offsite/?url={q(link)}"},
            {"id": "telegram", "name": "Telegram", "url": f"https://t.me/share/url?url={q(link)}&text={q(bare)}"},
            {"id": "email", "name": "Email", "url": f"mailto:?subject={q('Join me on Connect the Bots')}&body={q(with_link)}"},
        ],
    }


def covers(allowed, place):
    """A standing place covers itself and anything under its path on the same host."""
    a, p = urlsplit(allowed), urlsplit(place)
    if a.hostname != p.hostname or (a.query and a.query != p.query):
        return False
    return p.path == a.path or p.path.startswith(a.path.rstrip("/") + "/") or a.path == ""


class Campaign:
    def __init__(self, clock, settings):
        self.clock, self.settings = clock, settings
        self.own_host = urlsplit(settings.public_base_url).hostname

    def link(self, code):
        return f"{self.settings.public_base_url}/r/{code}"

    # Standing permission ---------------------------------------------------------
    def permission(self, connection, user_id):
        row = connection.execute("SELECT * FROM share_permissions WHERE user_id=?", (user_id,)).fetchone()
        used = self.used_this_week(connection, user_id)
        if row is None:
            return {"active": False, "places": [], "weekly_limit": 0, "used_this_week": used, "left_this_week": 0}
        return {"active": True, "places": json.loads(row["places_json"]), "weekly_limit": row["weekly_limit"],
                "approved_at": row["approved_at"], "used_this_week": used,
                "left_this_week": max(0, row["weekly_limit"] - used)}

    def set_permission(self, connection, user_id, fields):
        if not set(fields) <= {"places", "weekly_limit", "approved_by_owner"}:
            raise AgentError(422, "Send places, weekly_limit and approved_by_owner.")
        require_approval(fields.get("approved_by_owner"))
        places = fields.get("places")
        if not isinstance(places, list) or not 1 <= len(places) <= MAX_PLACES:
            raise AgentError(422, f"places must list 1 to {MAX_PLACES} https links your person chose.")
        normalized = list(dict.fromkeys(normalize_place(place, self.own_host) for place in places))
        limit = fields.get("weekly_limit")
        if type(limit) is not int or not 1 <= limit <= 7:
            raise AgentError(422, "weekly_limit must be a whole number from 1 to 7.")
        require_owner(connection, user_id)
        connection.execute(
            "INSERT INTO share_permissions(user_id,places_json,weekly_limit,approved_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET places_json=excluded.places_json,weekly_limit=excluded.weekly_limit,approved_at=excluded.approved_at",
            (user_id, json.dumps(normalized), limit, storage_time(self.clock())))
        return self.permission(connection, user_id)

    @staticmethod
    def remove_permission(connection, user_id):
        removed = connection.execute("DELETE FROM share_permissions WHERE user_id=?", (user_id,)).rowcount
        return {"active": False, "removed": bool(removed)}

    def used_this_week(self, connection, user_id):
        since = storage_time(self.clock() - timedelta(days=7))
        return connection.execute("SELECT COUNT(*) FROM share_approvals WHERE user_id=? AND place IS NOT NULL AND created_at>?",
                                  (user_id, since)).fetchone()[0]

    def authorize_place(self, connection, user_id, raw_place, approved):
        """Return (place, via) for a public-place share, or raise."""
        place = normalize_place(raw_place, self.own_host)
        since = storage_time(self.clock() - timedelta(days=REPEAT_DAYS))
        if connection.execute("SELECT 1 FROM share_approvals WHERE user_id=? AND place=? AND created_at>?",
                              (user_id, place, since)).fetchone():
            raise AgentError(409, f"Your person's link was already shared in this place within {REPEAT_DAYS} days. Don't repeat posts; choose a different place or wait.")
        permission = self.permission(connection, user_id)
        if approved is True:
            via = "post"
        elif permission["active"] and any(covers(allowed, place) for allowed in permission["places"]):
            via = "standing"
        else:
            require_approval(approved)
        if via == "standing" and permission["left_this_week"] <= 0:
            raise AgentError(429, f"Your person's weekly limit of {permission['weekly_limit']} automatic shares is used up. Ask them about this post, or wait.", 86400)
        if permission["used_this_week"] >= 7:
            raise AgentError(429, "Limit reached: 7 public shares in any 7 days.", 86400)
        return place, via


def rank_targets(rows, user_id, percent):
    """How far a member is from the early-access cut and from first place.

    rows: the referral leaderboard, ranked, values above zero. The numbers are
    "about": other members move too. A member already there needs 0.
    """
    ranked = [row for row in rows if row["value"] > 0]
    mine = next((row for row in ranked if row["user_id"] == user_id), None)
    value = mine["value"] if mine else 0
    count = math.ceil(len(ranked) * percent / 100) if ranked and percent else 0
    if not percent:
        to_top = None
    elif count == 0:
        to_top = 1
    elif any(row["user_id"] == user_id for row in ranked[:count]):
        to_top = 0
    else:
        to_top = ranked[count - 1]["value"] - value + 1
    leader = ranked[0]["value"] if ranked else 0
    to_first = 0 if mine is not None and mine["rank"] == 1 and len([r for r in ranked if r["rank"] == 1]) == 1 \
        else max(1, leader - value + 1)
    return {"rank": mine["rank"] if mine else None, "of": len(ranked), "qualified": value,
            "top_percent": percent, "more_for_top_percent": to_top, "more_for_first": to_first}


def public_board(rows, members, top=10):
    """Public entries: rank, count and a first name only for members who opted in."""
    output = []
    for row in rows[:top]:
        member = members.get(row["user_id"])
        name = member["first_name"] if member is not None and member["show_name_on_leaderboards"] and member["first_name"] else None
        output.append({"rank": row["rank"], "value": row["value"], "name": name})
    return output
