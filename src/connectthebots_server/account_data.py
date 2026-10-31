"""Owner-bound account data controls for the accounts milestone.

Later feature migrations must extend this explicit export/deletion contract.
Never infer an owner from a URL, form field or arbitrary table name.
"""
from datetime import datetime
import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from .auth import AuthenticationError, OAUTH_COOKIE, form_values
from .sessions import SESSION_COOKIE, clear_session_cookie, set_session_cookie, token_hash, valid_token


def account_routes(auth, board, interactions, notifications, moderation, lifecycle, agents=None):
    def current(request, *, refresh=False):
        if not auth.enabled:
            return None, auth.error("Sign-in is not open yet.", 503)
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=refresh)
        if session is None:
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return None, response
        return session, None

    def renewed_cookie(request, session, response):
        # Sessions.current caps the database expiry at the original absolute
        # deadline. The browser must receive that same remaining lifetime.
        expires = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
        remaining = max(0, int((expires - auth.clock()).total_seconds()))
        set_session_cookie(response, request.cookies[SESSION_COOKIE], max_age=remaining)
        return response

    async def checked_form(request, fields):
        session, response = current(request)
        if response is not None:
            return None, None, response
        try:
            values = await form_values(request, fields=fields | {"csrf"})
        except AuthenticationError as error:
            return None, None, auth.error(str(error))
        if not auth.origin_valid(request) or not auth.sessions.csrf_valid(session, values.get("csrf")):
            return None, None, auth.error("This form expired. Please reload the page.", 403)
        return session, values, None

    async def page(request):
        session, response = current(request, refresh=True)
        if response is not None:
            return response
        return renewed_cookie(request, session, auth.render("account_data.html", session=session, deleted=False))

    async def export(request):
        session, _, response = await checked_form(request, set())
        if response is not None:
            return response
        # A consistent snapshot prevents a concurrent deletion from creating a
        # partly populated export. All queries have a session-derived owner.
        with auth.database.transaction() as connection:
            profile = connection.execute(
                "SELECT google_sub, email, display_name, created_at, last_login_at FROM users WHERE id = ?",
                (session.user_id,),
            ).fetchone()
            if profile is None:
                return auth.error("Please sign in again.", 401)
            keys = connection.execute(
                "SELECT id, name, last_four, created_at, last_used_at, revoked_at FROM report_keys WHERE user_id = ? ORDER BY created_at, id",
                (session.user_id,),
            ).fetchall()
            stored_reports = connection.execute(
                "SELECT id, key_id, source, payload_json, received_at, handled_at FROM reports WHERE user_id = ? ORDER BY received_at, id",
                (session.user_id,),
            ).fetchall()
            preferences = connection.execute(
                "SELECT paused, cadence_minutes, next_due_at FROM report_settings WHERE user_id = ?",
                (session.user_id,),
            ).fetchone()
            check_in_settings = dict(preferences) if preferences is not None else {
                "paused": False, "cadence_minutes": None, "next_due_at": None,
            }
            check_in_settings["paused"] = bool(check_in_settings["paused"])
            shared_data = interactions.export_owner(session.user_id, connection=connection)
            notification_data = notifications.export_owner(session.user_id, connection=connection)
            payload = {
                "format_version": 1, "profile": dict(profile),
                "keys": [dict(row) for row in keys],
                "reports": [{
                    "id": row["id"], "key_id": row["key_id"], "source": row["source"],
                    "received_at": row["received_at"], "handled_at": row["handled_at"],
                    "payload": json.loads(row["payload_json"]),
                } for row in stored_reports],
                "check_in_settings": check_in_settings,
                "postings": board.export_owner(session.user_id, connection=connection),
                "introductions": shared_data["introductions"], "messages": shared_data["messages"],
                "conversations": shared_data["conversations"],
                "blocks": shared_data["blocks"],
                "notification_preferences": notification_data["preferences"],
                "notification_events": notification_data["items"],
                "email_outbox": notification_data["email_outbox"],
                "abuse_reports": moderation.export_owner(session.user_id, connection=connection),
            }
            if agents is not None:
                referral = connection.execute("SELECT code,created_at FROM referral_codes WHERE user_id=?", (session.user_id,)).fetchone()
                payload["agent_network"] = {
                    **agents.taste.export_owner(session.user_id, connection),
                    "referral_code": dict(referral) if referral else None,
                    "referral_used": [dict(row) for row in connection.execute(
                        "SELECT code,entered_via,entered_at,qualified_at FROM referrals WHERE referred_id=?", (session.user_id,))],
                    "referrals_made": [dict(row) for row in connection.execute(
                        "SELECT entered_at,qualified_at,flagged_at IS NOT NULL AND cleared_at IS NULL AS under_review FROM referrals WHERE referrer_id=? ORDER BY entered_at",
                        (session.user_id,))],
                    "early_access_grants": [dict(row) for row in connection.execute(
                        "SELECT source,granted_at,expires_at FROM early_access_grants WHERE user_id=? ORDER BY granted_at", (session.user_id,))],
                    "share_approvals": [dict(row) for row in connection.execute(
                        "SELECT kind,gap,place,via,created_at FROM share_approvals WHERE user_id=? ORDER BY created_at", (session.user_id,))],
                    "share_permission": [dict(row) for row in connection.execute(
                        "SELECT places_json,weekly_limit,approved_at FROM share_permissions WHERE user_id=?", (session.user_id,))],
                    "boards": agents.boards.export_owner(session.user_id, connection),
                    "agent_connect_links": [dict(row) for row in connection.execute(
                        "SELECT created_at,expires_at,claimed_at,revoked_at FROM agent_connect_links WHERE user_id=? ORDER BY created_at",
                        (session.user_id,))],
                }
        # Only a valid, completed export counts as session activity. Bad forms,
        # failed CSRF and account deletion must not extend an existing session.
        refreshed, response = current(request, refresh=True)
        if response is not None:
            return response
        response = JSONResponse(payload, headers={
            "Content-Disposition": 'attachment; filename="connect-the-bots-data.json"',
            "Cache-Control": "no-store",
        })
        return renewed_cookie(request, refreshed, response)

    def delete_owner(user_id, binding):
        with auth.database.transaction() as connection:
            profile = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if profile is None:
                return False
            # A waitlist request has separate removal-code authority. Matching
            # email addresses do not prove that this account owns that request.
            if valid_token(binding):
                connection.execute("DELETE FROM oauth_attempts WHERE binding_hash = ?", (token_hash(binding),))
            lifecycle.delete_account(user_id, connection=connection)
        return True

    async def delete(request):
        session, fields, response = await checked_form(request, {"confirmation"})
        if response is not None:
            return response
        if fields.get("confirmation") != "DELETE":
            return auth.error("Type DELETE to confirm account deletion.", 422)
        if not await run_in_threadpool(delete_owner, session.user_id, request.cookies.get(OAUTH_COOKIE)):
            return auth.error("Please sign in again.", 401)
        response = auth.render("account_data.html", deleted=True)
        clear_session_cookie(response)
        response.delete_cookie(OAUTH_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    return [
        Route("/app/account", page, methods=["GET"]),
        Route("/app/account/export", export, methods=["POST"]),
        Route("/app/account/delete", delete, methods=["POST"]),
    ]
