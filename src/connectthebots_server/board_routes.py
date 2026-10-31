"""Public approved postings and owner-only exact-payload publication forms."""
import base64
from datetime import datetime
from hashlib import sha256
import hmac
import json
import math
import re
import secrets
from urllib.parse import urlencode

from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from .auth import AuthenticationError, form_values
from .board import BoardError
from .sessions import SESSION_COOKIE, clear_session_cookie, set_session_cookie


class BoardJsonLimiter:
    """120-token burst, two tokens/second; bounded, hashed address keys only."""
    def __init__(self, clock, secret):
        self.clock, self.secret = clock, secret.encode("utf-8")
        self.buckets = {}

    def admit(self, address):
        now = self.clock().timestamp()
        key = hmac.new(self.secret, address.encode("utf-8"), sha256).hexdigest()
        if key not in self.buckets and len(self.buckets) >= 4096:
            self.buckets = {k: value for k, value in self.buckets.items() if now - value[1] < 600}
            if len(self.buckets) >= 4096:
                return 60
        tokens, previous = self.buckets.get(key, (120.0, now))
        tokens = min(120.0, tokens + max(0, now - previous) * 2)
        if tokens < 1:
            self.buckets[key] = (tokens, max(previous, now))
            return max(1, math.ceil((1 - tokens) / 2))
        self.buckets[key] = (tokens - 1, max(previous, now))
        return 0


class PublicationTokens:
    def __init__(self, auth):
        self.auth = auth

    def mac(self, value):
        return hmac.new(self.auth.settings.session_secret.encode("utf-8"),
                        ("board-publication-v1\0" + value).encode("ascii"), "sha256").hexdigest()

    def issue(self, owner, draft):
        value = {"owner": owner, "revision": draft["revision"], "hash": draft["payload_hash"],
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
            if (value["owner"] != owner or type(value["revision"]) is not int or value["revision"] < 1
                    or not re.fullmatch(r"[0-9a-f]{64}", value["hash"])
                    or type(value["expires"]) is not int or not now < value["expires"] <= now + 600):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise BoardError(400, "This publication preview expired or changed. Preview the saved draft again.") from None


def board_routes(auth, board):
    limiter = BoardJsonLimiter(auth.clock, auth.settings.session_secret)
    approvals = PublicationTokens(auth)

    def current(request):
        if not auth.enabled:
            return None, auth.error("The board is not open yet.", 503)
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=False)
        if session is None:
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return None, response
        return session, None

    def renew(request, response, *, optional=False):
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=True)
        if session is None:
            if optional:
                clear_session_cookie(response)
                return response
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return response
        expiry = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
        set_session_cookie(response, request.cookies[SESSION_COOKIE], max_age=max(0, int((expiry - auth.clock()).total_seconds())))
        return response

    async def checked(request, fields):
        session, response = current(request)
        if response is not None:
            return None, None, response
        values = await form_values(request, fields=fields | {"csrf"})
        if not auth.origin_valid(request) or not auth.sessions.csrf_valid(session, values.get("csrf")):
            return None, None, auth.error("This form expired. Please reload the page.", 403)
        return session, values, None

    def owner_route(path, endpoint, methods):
        async def dispatch(request):
            try:
                return await endpoint(request)
            except AuthenticationError as error:
                return auth.error(str(error), 400)
            except BoardError as error:
                return auth.error(error.detail, error.status)
        return Route(path, dispatch, methods=methods)

    def query(request):
        values = request.query_params
        if set(values) - {"q", "page"} or any(len(values.getlist(key)) != 1 for key in values):
            raise BoardError(400, "Use one search and one page number.")
        page = values.get("page", "1")
        if not re.fullmatch(r"[1-9][0-9]{0,4}", page) or int(page) > 10000:
            raise BoardError(400, "Page must be a whole number from 1 to 10000.")
        return values.get("q", ""), int(page)

    def board_error(error, as_json):
        if as_json:
            return JSONResponse({"error": error.detail}, status_code=error.status)
        return auth.render("board_error.html", status=error.status, message=error.detail, gone=error.status == 410)

    def public_route(path, endpoint, *, as_json=False):
        async def dispatch(request):
            if not auth.enabled:
                return board_error(BoardError(503, "The board is not open yet."), as_json)
            session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=False) if request.cookies.get(SESSION_COOKIE) else None
            if as_json:
                if session is None:
                    retry = limiter.admit(request.client.host if request.client else "unknown")
                    if retry:
                        return JSONResponse({"error": "Please wait before reading the board again."}, status_code=429,
                                            headers={"Retry-After": str(retry)})
            try:
                response = await endpoint(request, session.user_id if session is not None else None)
                return renew(request, response, optional=True) if session is not None and response.status_code == 200 else response
            except BoardError as error:
                return board_error(error, as_json)
        return Route(path, dispatch, methods=["GET"])

    async def public_list(request, viewer_id):
        search, page = query(request)
        result = board.public_list(query=search, page=page, page_size=20, viewer_id=viewer_id)
        previous = "/board?" + urlencode({"q": search, "page": page - 1}) if page > 1 else None
        following = "/board?" + urlencode({"q": search, "page": page + 1}) if result["has_next"] else None
        return auth.render("board_list.html", feed=result, search=search, previous=previous, following=following)

    async def public_json(request, viewer_id):
        search, page = query(request)
        return JSONResponse(board.public_list(query=search, page=page, page_size=20, viewer_id=viewer_id))

    async def detail(request, viewer_id):
        if request.query_params:
            raise BoardError(400, "Open the posting without extra parameters.")
        posting = board.get_public(request.path_params["slug"], viewer_id=viewer_id)
        own_slug = board.owner_posting(viewer_id)["slug"] if viewer_id is not None else None
        return auth.render("board_detail.html", posting=posting, can_request=own_slug != posting["slug"])

    async def detail_json(request, viewer_id):
        if request.query_params:
            raise BoardError(400, "Open the posting without extra parameters.")
        return JSONResponse(board.get_public(request.path_params["slug"], viewer_id=viewer_id))

    async def editor(request):
        session, response = current(request)
        if response is not None:
            return response
        return renew(request, auth.render("posting_editor.html", session=session, posting=board.owner_posting(session.user_id)))

    async def save(request):
        session, fields, response = await checked(request, {"text", "tags", "looking_for", "offering"})
        if response is not None:
            return response
        tags = fields.get("tags", "")
        payload = {"text": fields.get("text", ""), "tags": [tag.strip() for tag in tags.split(",")] if tags.strip() else [],
                   "looking_for": fields.get("looking_for", ""), "offering": fields.get("offering", "")}
        board.save_draft(session.user_id, payload)
        return renew(request, RedirectResponse("/app/posting", status_code=303))

    async def preview(request):
        session, _, response = await checked(request, set())
        if response is not None:
            return response
        draft = board.draft(session.user_id)
        if draft is None:
            raise BoardError(409, "Save a draft before previewing publication.")
        token = approvals.issue(session.user_id, draft)
        return renew(request, auth.render("posting_preview.html", session=session, payload=draft["payload"], publication_token=token))

    async def publish(request):
        session, fields, response = await checked(request, {"publication_token"})
        if response is not None:
            return response
        token = approvals.verify(session.user_id, fields.get("publication_token"))
        posting = board.publish(session.user_id, token["revision"], token["hash"])
        return renew(request, RedirectResponse("/board/" + posting["slug"], status_code=303))

    async def unpublish(request):
        session, _, response = await checked(request, set())
        if response is not None:
            return response
        board.unpublish(session.user_id)
        return renew(request, RedirectResponse("/app/posting", status_code=303))

    return [
        public_route("/board", public_list), public_route("/board.json", public_json, as_json=True),
        public_route("/board/{slug}.json", detail_json, as_json=True), public_route("/board/{slug}", detail),
        owner_route("/app/posting", editor, ["GET"]), owner_route("/app/posting", save, ["POST"]),
        owner_route("/app/posting/preview", preview, ["POST"]),
        owner_route("/app/posting/publish", publish, ["POST"]), owner_route("/app/posting/unpublish", unpublish, ["POST"]),
    ]
