"""Operator-adjustable growth settings, stored in the database.

Values change through the operator CLI (``growth config set``) and apply on the
next request, without a redeploy. Every value is validated here; a stored value
that no longer validates falls back to its default rather than failing open.
"""
import json

from .sessions import storage_time


FEATURES = {
    # Name: (description, how many of something early access unlocks)
    "extra_matches": "Up to 20 taste matches per check-in instead of 10.",
    "extra_board_creation": "Create up to 10 boards a day instead of 3.",
}

DEFAULTS = {
    "referral_qualifies_on": "interview",       # or "first_match"
    "referral_code_daily_limit": 10,            # entries per code per 24 hours
    "referral_cluster_window_minutes": 60,
    "referral_cluster_threshold": 5,            # entries per code within the window
    "referral_entry_window_days": 14,           # how long after sign-up a code can be added
    "referral_perk_days": 30,                   # early access for both sides
    "leaderboard_window_days": 30,
    "early_access_percent": 10,
    "auto_spawn_min_agents": 3,
    "auto_spawn_window_days": 14,
    "interview_min_topics": 5,
    "checkin_interval_days": 7,
    "posts_per_day": 10,
    "replies_per_day": 50,
    "boards_per_day": 3,
    "report_hide_threshold": 3,
    "public_leaderboard": "on",                 # the /leaderboard page; "off" hides it
    "feature.extra_matches": "early_access",
    "feature.extra_board_creation": "early_access",
}

_INTEGERS = {
    "referral_code_daily_limit": (1, 1000), "referral_cluster_window_minutes": (1, 10080),
    "referral_cluster_threshold": (2, 1000), "referral_entry_window_days": (0, 365),
    "referral_perk_days": (0, 365), "leaderboard_window_days": (1, 365),
    "early_access_percent": (0, 100), "auto_spawn_min_agents": (2, 100),
    "auto_spawn_window_days": (1, 365), "interview_min_topics": (1, 12),
    "checkin_interval_days": (1, 90), "posts_per_day": (1, 500), "replies_per_day": (1, 1000),
    "boards_per_day": (0, 100), "report_hide_threshold": (1, 100),
}
_CHOICES = {
    "referral_qualifies_on": ("interview", "first_match"),
    "public_leaderboard": ("on", "off"),
    **{"feature." + name: ("everyone", "early_access", "off") for name in FEATURES},
}


class GrowthConfigError(ValueError):
    def __init__(self, detail):
        super().__init__(detail)
        self.status, self.detail = 422, detail


def validate(key, value):
    if key in _INTEGERS:
        low, high = _INTEGERS[key]
        if type(value) is not int or not low <= value <= high:
            raise GrowthConfigError(f"{key} must be a whole number from {low} to {high}.")
        return value
    if key in _CHOICES:
        if value not in _CHOICES[key]:
            raise GrowthConfigError(f"{key} must be one of: " + ", ".join(_CHOICES[key]) + ".")
        return value
    raise GrowthConfigError("Unknown growth setting.")


def load(connection):
    values = dict(DEFAULTS)
    for row in connection.execute("SELECT key, value_json FROM growth_config"):
        try:
            values[row["key"]] = validate(row["key"], json.loads(row["value_json"]))
        except (GrowthConfigError, ValueError, TypeError):
            continue
    return values


def parse_cli_value(key, raw):
    if key in _INTEGERS:
        if not raw.isascii() or not raw.lstrip("-").isdigit() or len(raw) > 6:
            raise GrowthConfigError(f"{key} must be a whole number.")
        return validate(key, int(raw))
    return validate(key, raw)


def store(connection, key, value, now):
    value = validate(key, value)
    connection.execute(
        "INSERT INTO growth_config(key,value_json,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
        (key, json.dumps(value), storage_time(now)),
    )
    return load(connection)
