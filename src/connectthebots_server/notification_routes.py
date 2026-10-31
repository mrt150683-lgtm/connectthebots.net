"""Owner notification controls and a narrowly scoped email unsubscribe capability."""
from datetime import datetime
from email import policy
from email.parser import BytesParser
import re
from urllib.parse import quote

from starlette.responses import RedirectResponse
from starlette.routing import Route

from .auth import AuthenticationError, form_values
from .notifications import KINDS, NotificationError
from .sessions import SESSION_COOKIE, clear_session_cookie, set_session_cookie


PREFERENCES = (
    ("security_first_login", "First sign-in security notice", "A security notice when your account is first created."),
    ("introduction_request", "New introduction requests", "Someone has asked whether you would like an introduction."),
    ("introduction_accepted", "Your request was accepted", "The other person agreed to the introduction you requested."),
    ("new_message", "New messages", "Message alerts are grouped, at most one email per hour for a conversation."),
    ("checkin_missed", "Missed check-ins", "A report is overdue against the check-in time you explicitly chose."),
    ("weekly_summary", "Weekly summary", "A weekly summary of report, introduction and received-message activity, without private message text."),
)


async def one_click_form(request):
    """Accept only RFC8058's one fixed field, with no identity selectors or files."""
    body = await request.body()
    content_types = request.headers.getlist("content-type")
    if len(body) > 1024 or len(content_types) != 1:
        raise ValueError
    content_type = content_types[0]
    if len(content_type) > 255 or not content_type.isascii() or any(ord(char) < 32 for char in content_type):
        raise ValueError
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/x-www-form-urlencoded":
        values = await form_values(request, fields={"List-Unsubscribe"})
        if values != {"List-Unsubscribe": "One-Click"}:
            raise ValueError
        return
    if media_type != "multipart/form-data":
        raise ValueError
    message = BytesParser(policy=policy.default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("ascii") + b"\r\n\r\n" + body)
    boundary = message.get_boundary()
    parameters = message.get_params()
    if (message.defects or any(getattr(header, "defects", ()) for _, header in message.items())
            or not message.is_multipart() or not isinstance(boundary, str)
            or not 1 <= len(boundary) <= 70 or not boundary.isascii()
            or len(parameters) != 2 or parameters[1][0].lower() != "boundary"
            or message.preamble not in (None, "") or message.epilogue not in (None, "", "\r\n")):
        raise ValueError
    parts = list(message.iter_parts())
    if len(parts) != 1:
        raise ValueError
    part = parts[0]
    if (part.defects or any(getattr(header, "defects", ()) for _, header in part.items())
            or part.is_multipart() or len(part.get_all("Content-Disposition", [])) != 1
            or part.get_content_disposition() != "form-data"
            or part.get_params(header="content-disposition") != [("form-data", ""), ("name", "List-Unsubscribe")]
            or part.get_all("Content-Transfer-Encoding", []) or len(part.get_all("Content-Type", [])) > 1 or part.get_filename() is not None
            or part.get_content_type() != "text/plain" or part.get_payload(decode=True) != b"One-Click"):
        raise ValueError


def notification_routes(auth, notifications):
    def current(request):
        if not auth.enabled:
            return None, auth.error("Sign-in is not open yet.", 503)
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=False)
        if session is None:
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return None, response
        return session, None

    def renew(request, response):
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=True)
        if session is None:
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return response
        expires = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
        set_session_cookie(response, request.cookies[SESSION_COOKIE], max_age=max(0, int((expires - auth.clock()).total_seconds())))
        return response

    def page_number(value):
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,4}", value) or int(value) > 10000:
            raise NotificationError(400, "Page must be a whole number from 1 to 10000.")
        return int(value)

    def query(request, allowed):
        values = request.query_params
        if set(values) - allowed or any(len(values.getlist(key)) != 1 for key in values):
            raise NotificationError(400, "Open this page without extra or repeated parameters.")
        return values

    async def checked(request, fields):
        session, response = current(request)
        if response is not None:
            return None, None, response
        query(request, set())
        values = await form_values(request, fields=fields | {"csrf"})
        if not auth.origin_valid(request) or not auth.sessions.csrf_valid(session, values.get("csrf")):
            return None, None, auth.error("This form expired. Please reload the page.", 403)
        return session, values, None

    def route(path, endpoint, methods):
        async def dispatch(request):
            try:
                return await endpoint(request)
            except AuthenticationError as error:
                return auth.error(str(error), 400)
            except NotificationError as error:
                return auth.error(error.detail, error.status)
        return Route(path, dispatch, methods=methods)

    async def listing(request):
        session, response = current(request)
        if response is not None:
            return response
        values = query(request, {"page"})
        result = notifications.list_items(session.user_id, page=page_number(values.get("page", "1")))
        # Destinations are generated by the service, never supplied by a person.
        # Keep the rendering boundary narrow even if a later event adds a URL.
        for item in result["items"]:
            if (item["url"] not in {"/app", "/app/account", "/app/introductions", "/app/conversations"}
                    and not re.fullmatch(r"/app/(introductions|conversations)/[A-Za-z0-9_-]{43}", item["url"])):
                item["url"] = "/app/notifications"
        return renew(request, auth.render("notification_list.html", session=session, result=result))

    async def read(request):
        session, values, response = await checked(request, {"page"})
        if response is not None:
            return response
        page = page_number(values.get("page", "1"))
        notifications.mark_read(session.user_id, request.path_params["notification_id"])
        return renew(request, RedirectResponse("/app/notifications" + ("?page=" + str(page) if page > 1 else ""), status_code=303))

    async def preferences(request):
        session, response = current(request)
        if response is not None:
            return response
        query(request, set())
        values = notifications.preferences(session.user_id)
        return renew(request, auth.render("notification_preferences.html", session=session, preferences=values,
                                         choices=PREFERENCES, email_mode=auth.settings.email_mode))

    async def save_preferences(request):
        session, values, response = await checked(request, set(KINDS))
        if response is not None:
            return response
        if any(value != "on" for key, value in values.items() if key != "csrf"):
            raise NotificationError(400, "Use the email preference checkboxes to make your choices.")
        notifications.update_preferences(session.user_id, {kind: kind in values for kind in KINDS})
        return renew(request, RedirectResponse("/app/notifications/preferences", status_code=303))

    async def unsubscribe(request):
        # Deliberately independent of accounts, cookies and CSRF. This signed
        # single-kind capability may only disable its own email preference.
        try:
            if request.query_params:
                raise ValueError
            token = request.path_params["token"]
            if request.method in {"GET", "HEAD"}:
                info = notifications.token_info(token)
                return auth.render("notification_unsubscribe.html", label=info["label"], complete=False,
                                   unsubscribe_url="/email/unsubscribe/" + quote(token, safe=""))
            await one_click_form(request)
            info = notifications.unsubscribe(token)
            return auth.render("notification_unsubscribe.html", label=info["label"], complete=True)
        except (ValueError, AuthenticationError, NotificationError):
            return auth.render("notification_unsubscribe_error.html", status=400)

    return [
        route("/app/notifications", listing, ["GET"]),
        route("/app/notifications/preferences", preferences, ["GET"]),
        route("/app/notifications/preferences", save_preferences, ["POST"]),
        route("/app/notifications/{notification_id}/read", read, ["POST"]),
        Route("/email/unsubscribe/{token}", unsubscribe, methods=["GET", "POST"]),
    ]
