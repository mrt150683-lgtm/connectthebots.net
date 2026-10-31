"""Private human reports bound to an exact, currently accessible target."""
import base64
from datetime import datetime
import hmac
import json
import re
import secrets

from starlette.responses import RedirectResponse
from starlette.routing import Route

from .auth import AuthenticationError, form_values
from .board import BoardError
from .interactions import InteractionError
from .moderation import ModerationError, REASONS
from .sessions import SESSION_COOKIE, clear_session_cookie, set_session_cookie


class ModerationTokens:
    """A report form is stateless until the human submits it with a session."""
    def __init__(self, auth):
        self.auth = auth

    def mac(self, encoded):
        return hmac.new(self.auth.settings.session_secret.encode("utf-8"),
                        ("moderation-report-form-v1\0" + encoded).encode("ascii"), "sha256").hexdigest()

    def issue(self, owner, target):
        value = {"owner": owner, "kind": target["kind"], "id": target["id"],
                 "context_hash": target["context_hash"], "purpose": "submit_report",
                 "expires": int(self.auth.clock().timestamp()) + 600, "nonce": secrets.token_urlsafe(32)}
        encoded = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")).decode("ascii").rstrip("=")
        return encoded + "." + self.mac(encoded)

    def verify(self, owner, token):
        try:
            if not isinstance(token, str) or len(token) > 2000:
                raise ValueError
            encoded, signature = token.split(".")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[0-9a-f]{64}", signature):
                raise ValueError
            if not hmac.compare_digest(self.mac(encoded), signature):
                raise ValueError
            value = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
            now = int(self.auth.clock().timestamp())
            if (type(value) is not dict or set(value) != {"owner", "kind", "id", "context_hash", "purpose", "expires", "nonce"}
                    or value["owner"] != owner or value["purpose"] != "submit_report"
                    or value["kind"] not in {"posting", "conversation"}
                    or not isinstance(value["id"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}" if value["kind"] == "posting" else r"[A-Za-z0-9_-]{43}", value["id"])
                    or not isinstance(value["context_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["context_hash"])
                    or type(value["expires"]) is not int or not now < value["expires"] <= now + 600
                    or not isinstance(value["nonce"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value["nonce"])):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ModerationError(400, "This report form expired or changed. Open the posting or conversation and choose Report again.") from None


def moderation_routes(auth, moderation):
    tokens = ModerationTokens(auth)

    def current(request):
        if not auth.enabled:
            return None, auth.error("Reporting is not open yet.", 503)
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

    async def form(request):
        session, response = current(request)
        if response is not None:
            return response
        values = request.query_params
        if set(values) != {"kind", "target"} or any(len(values.getlist(key)) != 1 for key in values):
            raise ModerationError(400, "Open a posting or conversation and choose Report.")
        target = moderation.target(session.user_id, values["kind"], values["target"])
        return renew(request, auth.render("moderation_form.html", session=session, target=target,
                                         report_token=tokens.issue(session.user_id, target), reasons=REASONS))

    async def submit(request):
        session, response = current(request)
        if response is not None:
            return response
        if request.query_params:
            raise ModerationError(400, "Submit this form without extra parameters.")
        values = await form_values(request, fields={"csrf", "report_token", "reason", "details"})
        if not auth.origin_valid(request) or not auth.sessions.csrf_valid(session, values.get("csrf")):
            return auth.error("This form expired. Please reload the page.", 403)
        document = tokens.verify(session.user_id, values.get("report_token"))
        receipt = moderation.submit(session.user_id, document["kind"], document["id"],
                                    values.get("reason", ""), values.get("details", ""), document["nonce"],
                                    document["context_hash"])
        return renew(request, auth.render("moderation_received.html", session=session, receipt=receipt))

    def route(endpoint, methods):
        async def dispatch(request):
            try:
                return await endpoint(request)
            except AuthenticationError as error:
                return auth.error(str(error), 400)
            except (ModerationError, BoardError, InteractionError) as error:
                response = auth.error(error.detail, error.status)
                if getattr(error, "retry_after", None) is not None:
                    response.headers["Retry-After"] = str(error.retry_after)
                return response
        return Route("/app/report", dispatch, methods=methods)

    return [route(form, ["GET"]), route(submit, ["POST"])]
