"""Leaderboards that count only engagement which needs another person.

Raw post volume never counts. Within a rolling window (default 30 days):
- connections: conversations where both people have sent a message;
- referrals: referrals that qualified and are not held for review;
- replies: board posts ranked by distinct other people who replied.
"""
from datetime import timedelta

from .sessions import storage_time


def _ranked(rows):
    """Competition ranking: equal values share a rank, ties ordered stably."""
    rows = sorted(rows, key=lambda row: (-row["value"], row["public_id"] or "", row["user_id"]))
    output = []
    for index, row in enumerate(rows):
        rank = output[-1]["rank"] if output and output[-1]["value"] == row["value"] else index + 1
        output.append({**row, "rank": rank})
    return output


def _members(connection):
    return {row["user_id"]: row for row in connection.execute(
        "SELECT user_id,public_id,first_name,show_name_on_leaderboards FROM member_profiles")}


def referral_rows(connection, since):
    members = _members(connection)
    rows = connection.execute(
        "SELECT r.referrer_id AS user_id, COUNT(*) AS value FROM referrals r JOIN users u ON u.id=r.referrer_id "
        "WHERE r.qualified_at>? AND u.suspended_at IS NULL AND (r.flagged_at IS NULL OR r.cleared_at IS NOT NULL) GROUP BY r.referrer_id",
        (since,)).fetchall()
    return _ranked([{"user_id": row["user_id"], "value": row["value"],
                     "public_id": members[row["user_id"]]["public_id"] if row["user_id"] in members else None} for row in rows])


def connection_rows(connection, since):
    members = _members(connection)
    rows = connection.execute(
        "SELECT person AS user_id, COUNT(*) AS value FROM ("
        " SELECT c.requester_id AS person FROM conversations c WHERE c.opened_at>? AND c.requester_id IS NOT NULL AND c.recipient_id IS NOT NULL"
        "  AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.sender_id=c.requester_id)"
        "  AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.sender_id=c.recipient_id)"
        " UNION ALL"
        " SELECT c.recipient_id FROM conversations c WHERE c.opened_at>? AND c.requester_id IS NOT NULL AND c.recipient_id IS NOT NULL"
        "  AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.sender_id=c.requester_id)"
        "  AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.sender_id=c.recipient_id)"
        ") JOIN users u ON u.id=person WHERE u.suspended_at IS NULL GROUP BY person", (since, since)).fetchall()
    return _ranked([{"user_id": row["user_id"], "value": row["value"],
                     "public_id": members[row["user_id"]]["public_id"] if row["user_id"] in members else None} for row in rows])


def reply_rows(connection, since):
    members = _members(connection)
    rows = connection.execute(
        "SELECT p.id AS post_id, p.user_id, b.slug AS board, COUNT(DISTINCT r.user_id) AS value "
        "FROM board_posts p JOIN topic_boards b ON b.id=p.board_id JOIN users u ON u.id=p.user_id "
        "JOIN board_replies r ON r.post_id=p.id AND r.user_id<>p.user_id AND r.withdrawn_at IS NULL AND r.hidden_at IS NULL "
        "WHERE r.created_at>? AND p.withdrawn_at IS NULL AND p.hidden_at IS NULL AND u.suspended_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM user_blocks k WHERE (k.blocker_id=p.user_id AND k.blocked_id=r.user_id) OR (k.blocker_id=r.user_id AND k.blocked_id=p.user_id)) "
        "GROUP BY p.id", (since,)).fetchall()
    return _ranked([{"user_id": row["user_id"], "value": row["value"], "post_id": row["post_id"], "board": row["board"],
                     "public_id": members[row["user_id"]]["public_id"] if row["user_id"] in members else None} for row in rows])


def window_start(now, config):
    return storage_time(now - timedelta(days=config["leaderboard_window_days"]))


def _public(row, members, top_kind):
    member = members.get(row["user_id"])
    name = member["first_name"] if member is not None and member["show_name_on_leaderboards"] else None
    entry = {"rank": row["rank"], "value": row["value"], "member": {"public_id": row["public_id"], "name": name}}
    if top_kind == "replies":
        entry.update({"post_id": row["post_id"], "board": row["board"]})
    return entry


def leaderboards(connection, now, config, viewer_id=None, top=10):
    since = window_start(now, config)
    members = _members(connection)
    boards = {"connections": connection_rows(connection, since), "referrals": referral_rows(connection, since),
              "replies": reply_rows(connection, since)}
    result = {"window_days": config["leaderboard_window_days"], "since": since,
              "counts": {"connections": "conversations where both people sent a message",
                         "referrals": "referrals that qualified (interview complete or first match)",
                         "replies": "posts ranked by distinct other people who replied"},
              "boards": {kind: [_public(row, members, kind) for row in rows[:top]] for kind, rows in boards.items()}}
    if viewer_id is not None:
        result["you"] = {kind: next(({"rank": row["rank"], "value": row["value"], "of": len(rows)}
                                     for row in rows if row["user_id"] == viewer_id), None)
                         for kind, rows in boards.items()}
    return result
