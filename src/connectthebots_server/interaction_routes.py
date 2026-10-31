"""Human-only introduction approvals and private two-person conversations."""
import base64
from datetime import datetime
import hmac
import json
import re
import secrets

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse
from starlette.routing import Route

from .auth import AuthenticationError, form_values
from .board import BoardError
from .interactions import InteractionError
from .sessions import SESSION_COOKIE, clear_session_cookie, set_session_cookie


class InteractionTokens:
    """Stateless exact-context forms; a token is never authority without a session."""
    def __init__(self, auth):
        self.auth = auth

    def mac(self, encoded):
        return hmac.new(self.auth.settings.session_secret.encode("utf-8"),
                        ("interaction-form-v1\0" + encoded).encode("ascii"), "sha256").hexdigest()

    def issue(self, owner, purpose, value):
        document = {"owner": owner, "purpose": purpose, "value": value,
                    "expires": int(self.auth.clock().timestamp()) + 600, "nonce": secrets.token_urlsafe(32)}
        encoded = base64.urlsafe_b64encode(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
        if len(encoded) > 40000:
            raise InteractionError(422, "This preview is too large. Please shorten your words.")
        return encoded + "." + self.mac(encoded)

    def verify(self, owner, purpose, token):
        try:
            if not isinstance(token, str) or len(token) > 40065:
                raise ValueError
            encoded, signature = token.split(".")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[0-9a-f]{64}", signature):
                raise ValueError
            if not hmac.compare_digest(self.mac(encoded), signature):
                raise ValueError
            value = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
            now = int(self.auth.clock().timestamp())
            if (value["owner"] != owner or value["purpose"] != purpose
                    or type(value["expires"]) is not int or not now < value["expires"] <= now + 600
                    or not isinstance(value["nonce"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value["nonce"])):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise InteractionError(400, "This form expired or changed. Open the page again before deciding.") from None


def interaction_routes(auth, interactions, board):
    tokens = InteractionTokens(auth)

    def current(request):
        if not auth.enabled:
            return None, auth.error("Introductions and conversations are not open yet.", 503)
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

    async def checked(request, fields):
        session, response = current(request)
        if response is not None:
            return None, None, response
        if request.query_params:
            raise InteractionError(400, "Submit this form without extra parameters.")
        values = await form_values(request, fields=fields | {"csrf"})
        if not auth.origin_valid(request) or not auth.sessions.csrf_valid(session, values.get("csrf")):
            return None, None, auth.error("This form expired. Please reload the page.", 403)
        return session, values, None

    def query(request, allowed):
        values = request.query_params
        if set(values) - allowed or any(len(values.getlist(key)) != 1 for key in values):
            raise InteractionError(400, "Open this page without extra or repeated parameters.")
        return values

    def page_number(request):
        value = query(request, {"page"}).get("page", "1")
        if not re.fullmatch(r"[1-9][0-9]{0,4}", value) or int(value) > 10000:
            raise InteractionError(400, "Page must be a whole number from 1 to 10000.")
        return int(value)

    def route(path, endpoint, methods):
        async def dispatch(request):
            try:
                return await endpoint(request)
            except AuthenticationError as error:
                return auth.error(str(error), 400)
            except (InteractionError, BoardError) as error:
                response = auth.error(error.detail, error.status)
                retry = getattr(error, "retry_after", None)
                if retry is not None:
                    response.headers["Retry-After"] = str(retry)
                return response
        return Route(path, dispatch, methods=methods)

    async def introduction_list(request):
        session, response = current(request)
        if response is not None:
            return response
        result = interactions.list_introductions(session.user_id, page=page_number(request))
        return renew(request, auth.render("introduction_list.html", session=session, result=result))

    async def new(request):
        session, response = current(request)
        if response is not None:
            return response
        values = query(request, {"slug"})
        posting = board.get_public(values.get("slug"), viewer_id=session.user_id)
        own = board.owner_posting(session.user_id)
        return renew(request, auth.render("introduction_new.html", session=session, posting=posting,
                                         can_request=own["visible"] and own["slug"] != posting["slug"]))

    async def preview(request):
        session, values, response = await checked(request, {"target_slug", "note", "reason"})
        if response is not None:
            return response
        value = interactions.prepare_request(session.user_id, values.get("target_slug"), values.get("note", ""), values.get("reason", ""))
        return renew(request, auth.render("introduction_preview.html", session=session, preview=value,
                                         request_token=tokens.issue(session.user_id, "request", value)))

    async def create(request):
        session, values, response = await checked(request, {"request_token"})
        if response is not None:
            return response
        document = tokens.verify(session.user_id, "request", values.get("request_token"))
        result = interactions.request(session.user_id, document["value"], document["nonce"])
        return renew(request, RedirectResponse("/app/introductions/" + result["id"], status_code=303))

    async def introduction(request):
        session, response = current(request)
        if response is not None:
            return response
        query(request, set())
        result = interactions.get_introduction(session.user_id, request.path_params["intro_id"])
        token = tokens.issue(session.user_id, "accept", {"id": result["id"], "hash": result["context_hash"]}) if "accept" in result["actions"] else None
        return renew(request, auth.render("introduction_detail.html", session=session, intro=result, accept_token=token))

    async def accept(request):
        session, values, response = await checked(request, {"accept_token"})
        if response is not None:
            return response
        document = tokens.verify(session.user_id, "accept", values.get("accept_token"))
        if document["value"].get("id") != request.path_params["intro_id"]:
            raise InteractionError(400, "Open this introduction again before deciding.")
        result = interactions.accept(session.user_id, request.path_params["intro_id"], document["value"].get("hash"))
        return renew(request, RedirectResponse("/app/conversations/" + result["conversation"]["id"], status_code=303))

    def intro_action(method):
        async def endpoint(request):
            session, _, response = await checked(request, set())
            if response is not None:
                return response
            await run_in_threadpool(method, session.user_id, request.path_params["intro_id"])
            return renew(request, RedirectResponse("/app/introductions/" + request.path_params["intro_id"], status_code=303))
        return endpoint

    async def conversation_list(request):
        session, response = current(request)
        if response is not None:
            return response
        result = interactions.list_conversations(session.user_id, page=page_number(request))
        return renew(request, auth.render("conversation_list.html", session=session, result=result))

    async def conversation(request):
        session, response = current(request)
        if response is not None:
            return response
        values = query(request, {"before"})
        result = interactions.get_conversation(session.user_id, request.path_params["conversation_id"], before=values.get("before"))
        context = interactions.get_introduction(session.user_id, result["introduction_id"])
        token = tokens.issue(session.user_id, "message", {"id": result["id"]}) if result["state"] == "open" else None
        return renew(request, auth.render("conversation_detail.html", session=session, conversation=result,
                                         message_token=token, older_view="before" in values,
                                         reason=context["reason"] or "You both chose to talk"))

    async def message(request):
        session, values, response = await checked(request, {"text", "message_token"})
        if response is not None:
            return response
        document = tokens.verify(session.user_id, "message", values.get("message_token"))
        if document["value"].get("id") != request.path_params["conversation_id"]:
            raise InteractionError(400, "Open this conversation again before sending.")
        interactions.send_message(session.user_id, request.path_params["conversation_id"], values.get("text", ""), document["nonce"])
        return renew(request, RedirectResponse("/app/conversations/" + request.path_params["conversation_id"], status_code=303))

    def conversation_action(method):
        async def endpoint(request):
            session, _, response = await checked(request, set())
            if response is not None:
                return response
            await run_in_threadpool(method, session.user_id, request.path_params["conversation_id"])
            return renew(request, RedirectResponse("/app/conversations/" + request.path_params["conversation_id"], status_code=303))
        return endpoint

    return [
        route("/app/introductions", introduction_list, ["GET"]), route("/app/introductions", create, ["POST"]),
        route("/app/introductions/new", new, ["GET"]), route("/app/introductions/preview", preview, ["POST"]),
        route("/app/introductions/{intro_id}", introduction, ["GET"]),
        route("/app/introductions/{intro_id}/accept", accept, ["POST"]),
        route("/app/introductions/{intro_id}/decline", intro_action(interactions.decline), ["POST"]),
        route("/app/introductions/{intro_id}/withdraw", intro_action(interactions.withdraw), ["POST"]),
        route("/app/introductions/{intro_id}/block", intro_action(interactions.block_introduction), ["POST"]),
        route("/app/conversations", conversation_list, ["GET"]),
        route("/app/conversations/{conversation_id}", conversation, ["GET"]),
        route("/app/conversations/{conversation_id}/messages", message, ["POST"]),
        route("/app/conversations/{conversation_id}/close", conversation_action(interactions.close_conversation), ["POST"]),
        route("/app/conversations/{conversation_id}/block", conversation_action(interactions.block_conversation), ["POST"]),
    ]
