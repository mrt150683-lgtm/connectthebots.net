"""Public community counts: totals only, never who.

The counter on public pages shows how many people have an account and how many
of them have connected an agent. Both are plain totals over available accounts
(suspended ones are left out), so no row, name or time can be read from them.
"""


def community_counts(connection):
    one = lambda sql: connection.execute(sql).fetchone()[0]
    return {
        "members": one("SELECT COUNT(*) FROM users WHERE suspended_at IS NULL"),
        # A connected agent is a current agent key that has made at least one call.
        "agents_connected": one(
            "SELECT COUNT(DISTINCT k.user_id) FROM report_keys k JOIN users u ON u.id=k.user_id "
            "WHERE k.agent_api=1 AND k.revoked_at IS NULL AND k.last_used_at IS NOT NULL AND u.suspended_at IS NULL"),
    }
