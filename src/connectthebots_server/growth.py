"""Operator CLI actions for growth settings, referral review and board reports.

There are no HTTP routes for any of these. Changing a setting takes effect on
the next request; no redeploy or restart is needed.
"""
from . import growth_config
from .agent_common import AgentError
from .leaderboards import referral_rows, window_start
from .referrals import ReferralService, refresh_early_access
from .sessions import storage_time


def operate(database, clock, args):
    referrals = ReferralService(database, clock)
    if args.action in {"config", "flagged", "board-reports"}:
        with database.connect() as connection:
            if args.action == "config":
                return {"settings": growth_config.load(connection), "features": growth_config.FEATURES}
            if args.action == "flagged":
                return {"flagged": referrals.flagged(connection)}
            return {"board_reports": [dict(row) for row in connection.execute(
                "SELECT id,target_kind,target_id,reason,details,created_at FROM board_content_reports "
                "WHERE resolved_at IS NULL ORDER BY created_at LIMIT 200")]}
    with database.transaction() as connection:
        if args.action == "set":
            return {"settings": growth_config.store(connection, args.key, growth_config.parse_cli_value(args.key, args.value), clock())}
        if args.action == "clear":
            return referrals.clear(connection, args.id)
        if args.action == "flag":
            return referrals.flag(connection, args.id)
        if args.action == "unhide":
            changed = 0
            for table, kind in (("board_posts", "post"), ("board_replies", "reply")):
                changed += connection.execute(f"UPDATE {table} SET hidden_at=NULL WHERE id=?", (args.id,)).rowcount
                connection.execute("UPDATE board_content_reports SET resolved_at=? WHERE target_kind=? AND target_id=?",
                                   (storage_time(clock()), kind, args.id))
            if not changed:
                raise AgentError(404, "Post or reply not found.")
            return {"id": args.id, "visible": True}
        config = growth_config.load(connection)
        now = clock()
        refresh_early_access(connection, now, config, referral_rows(connection, window_start(now, config)), force=True)
        return {"recalculated": True, "early_access_percent": config["early_access_percent"]}
