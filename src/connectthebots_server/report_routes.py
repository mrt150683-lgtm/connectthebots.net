"""Owner-bound report UI and bearer-only ingestion. No cleartext key persistence."""
import base64
from datetime import datetime
import hmac
import json
import re
import secrets

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from .auth import AuthenticationError, form_values
from .board import BoardError
from .dashboard import dashboard_context
from .prompt import personalised_prompt
from .report_schema import ReportError, parse_paste, validate_report
from .sessions import SESSION_COOKIE, clear_session_cookie, set_session_cookie


PREVIEW_LIFETIME = 600
TOKEN_LIMIT = 60000
MAC = re.compile(r"[0-9a-f]{64}\Z")


class ExpiredReportPreview(AuthenticationError):
    """An authentic, owner-bound preview that may be recovered, never saved."""

    def __init__(self, document):
        super().__init__("This report preview expired. Preview the report again before saving it.")
        self.document = document


def _signed(auth, owner, purpose, value):
    document = {"owner": owner, "purpose": purpose,
                "expires": int(auth.clock().timestamp()) + PREVIEW_LIFETIME,
                "nonce": secrets.token_urlsafe(32), "value": value}
    encoded = base64.urlsafe_b64encode(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(auth.settings.session_secret.encode("utf-8"),
                         ("report-form-v1\0" + encoded).encode("ascii"), "sha256").hexdigest()
    if len(encoded) + 65 > TOKEN_LIMIT:
        raise ReportError(422, "This preview is too large. Please shorten the report.")
    return encoded + "." + signature


def _verified(auth, owner, purpose, token):
    try:
        if not isinstance(token, str) or len(token) > TOKEN_LIMIT:
            raise ValueError
        encoded, signature = token.split(".")
        if not MAC.fullmatch(signature) or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise ValueError
        expected = hmac.new(auth.settings.session_secret.encode("utf-8"),
                            ("report-form-v1\0" + encoded).encode("ascii"), "sha256").hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        document = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        now = int(auth.clock().timestamp())
        if (document["owner"] != owner or document["purpose"] != purpose
                or type(document["expires"]) is not int
                or document["expires"] > now + PREVIEW_LIFETIME):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise AuthenticationError("This preview expired or changed. Start again from the original form.") from None
    if document["expires"] <= now:
        if purpose == "paste-confirm":
            raise ExpiredReportPreview(document)
        raise AuthenticationError("This preview expired or changed. Start again from the original form.")
    return document


def _paste_text(payload):
    """Reconstruct v2 field labels from a validated, signed paste preview."""
    lines = ["Kind: " + payload["kind"], "Checked at: " + payload["checked_at"]]
    places = payload["places_checked"]
    lines.extend("Places checked: " + place["name"] + (" | " + place["url"] if "url" in place else "") for place in places)
    if not places:
        lines.append("Places checked: none")
    lines.append("Posts read: " + str(payload["posts_read"]))
    if payload["kind"] == "question":
        lines.extend(("Question: " + payload["question"]["text"], "Why: " + payload["question"]["why"]))
    elif payload["kind"] == "connection":
        value = payload["connection"]
        lines.append("Connection: " + value["who"] + " | " + value["where"])
        for label, key in (("Why now", "why_now"), ("They bring", "they_bring"),
                           ("They could gain", "they_could_gain"), ("I bring", "i_bring"), ("I could gain", "i_could_gain")):
            lines.append(label + ": " + value[key])
        lines.extend("Evidence: " + item["quote_or_link"] + " | " + item["matches"] for item in value["evidence"])
        lines.extend(("Still unknown: " + value["still_unknown"], "Small first step: " + value["first_step"]))
        if "board_slug" in value:
            lines.append("Board slug: " + value["board_slug"])
    return "\n".join(lines) + "\n"


def report_routes(auth, reports, board, interactions, agents=None):
    def current(request):
        if not auth.enabled:
            return None, auth.render("signin.html", enabled=False, mode=auth.settings.signups, status=503)
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=False)
        if session is None:
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return None, response
        return session, None

    def renewed(request, response):
        session, refused = current(request)
        if refused is not None:
            return refused
        session = auth.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=True)
        if session is None:
            response = RedirectResponse("/signin", status_code=303)
            clear_session_cookie(response)
            return response
        expires = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
        remaining = max(0, int((expires - auth.clock()).total_seconds()))
        set_session_cookie(response, request.cookies[SESSION_COOKIE], max_age=remaining)
        return response

    async def checked(request, fields):
        session, response = current(request)
        if response is not None:
            return None, None, response
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
            except ReportError as error:
                response = auth.error(error.detail, error.status)
                if error.retry_after is not None:
                    response.headers["Retry-After"] = str(error.retry_after)
                return response
        return Route(path, dispatch, methods=methods)

    async def dashboard(request):
        session, response = current(request)
        if response is not None:
            return response
        context = dashboard_context(reports, session.user_id, auth.clock())
        context["introductions"] = interactions.list_introductions(session.user_id, limit=5)
        context["growth"] = agents.dashboard(session.user_id) if agents is not None else None
        return renewed(request, auth.render("dashboard.html", session=session, **context))

    async def keys(request):
        session, response = current(request)
        if response is not None:
            return response
        return renewed(request, auth.render("report_keys.html", session=session, keys=reports.list_keys(session.user_id)))

    async def create_key(request):
        session, fields, response = await checked(request, {"name"})
        if response is not None:
            return response
        key = reports.create_key(session.user_id, fields.get("name", ""))
        prompt = personalised_prompt(auth.settings.public_base_url, key["key"])
        download_token = _signed(auth, session.user_id, "prompt-download", prompt)
        return renewed(request, auth.render("report_key_created.html", session=session, key=key,
                                            prompt=prompt, download_token=download_token))

    async def connect_agent(request):
        session, _, response = await checked(request, set())
        if response is not None:
            return response
        from .agent_common import AgentError
        try:
            link = await run_in_threadpool(agents.connect_links.create, session.user_id)
        except AgentError as error:
            response = auth.error(error.detail, error.status)
            if error.retry_after is not None:
                response.headers["Retry-After"] = str(error.retry_after)
            return response
        response = auth.render("connect_link.html", session=session, link=link)
        response.headers["Cache-Control"] = "no-store"
        return renewed(request, response)

    async def download_prompt(request):
        session, fields, response = await checked(request, {"download_token"})
        if response is not None:
            return response
        document = _verified(auth, session.user_id, "prompt-download", fields.get("download_token"))
        response = PlainTextResponse(document["value"], media_type="text/markdown", headers={
            "Content-Disposition": 'attachment; filename="connect-the-bots-private-prompt.md"',
            "Cache-Control": "no-store",
        })
        return renewed(request, response)

    async def revoke_key(request):
        session, _, response = await checked(request, set())
        if response is not None:
            return response
        reports.revoke_key(session.user_id, request.path_params["key_id"])
        return renewed(request, RedirectResponse("/app/keys", status_code=303))

    async def remove_share_permission(request):
        session, _, response = await checked(request, set())
        if response is not None:
            return response
        agents.share_permission(session.user_id, remove=True)
        return renewed(request, RedirectResponse("/app", status_code=303))

    async def rename_key(request):
        session, fields, response = await checked(request, {"name"})
        if response is not None:
            return response
        reports.rename_key(session.user_id, request.path_params["key_id"], fields.get("name", ""))
        return renewed(request, RedirectResponse("/app/keys", status_code=303))

    async def paste(request):
        session, response = current(request)
        if response is not None:
            return response
        return renewed(request, auth.render("report_paste.html", session=session))

    def paste_error(session, text, message, status, retry_after=None):
        response = auth.render("report_paste.html", session=session, report_text=text, error=message, status=status)
        if retry_after is not None:
            response.headers["Retry-After"] = str(retry_after)
        return response

    async def paste_fields(request, fields):
        # Recover only after authentication and origin validation. Malformed
        # forms are not echoed, and neither recovery path saves a draft.
        session, response = current(request)
        if response is not None:
            return None, None, response
        if not auth.origin_valid(request):
            return None, None, auth.error("This form came from another site. Open Paste a report again.", 403)
        try:
            values = await form_values(request, fields=fields | {"csrf"})
        except AuthenticationError:
            return None, None, paste_error(session, "", "This report form could not be read. Paste the original report again.", 400)
        return session, values, None

    def recovered_text(session, token):
        try:
            try:
                document = _verified(auth, session.user_id, "paste-confirm", token)
            except ExpiredReportPreview as error:
                document = error.document
            return _paste_text(validate_report(document["value"]))
        except (AuthenticationError, ReportError, KeyError):
            return ""

    async def preview(request):
        session, fields, response = await paste_fields(request, {"report_text"})
        if response is not None:
            return response
        text = fields.get("report_text", "")
        if not auth.sessions.csrf_valid(session, fields.get("csrf")):
            # A matching explicit Origin is required to retain a submission
            # that lacks a valid session token. The next POST still needs CSRF.
            retained = text if request.headers.get("origin") == auth.settings.public_base_url else ""
            return paste_error(session, retained, "This form was out of date. Nothing was saved. Check the report below and preview it again.", 403)
        try:
            payload = parse_paste(text)
            token = _signed(auth, session.user_id, "paste-confirm", payload)
        except ReportError as error:
            return paste_error(session, text, error.detail, error.status, error.retry_after)
        return renewed(request, auth.render("report_preview.html", session=session, payload=payload, preview_token=token))

    async def confirm(request):
        session, fields, response = await paste_fields(request, {"preview_token"})
        if response is not None:
            return response
        token = fields.get("preview_token")
        if not auth.sessions.csrf_valid(session, fields.get("csrf")):
            text = recovered_text(session, token) if request.headers.get("origin") == auth.settings.public_base_url else ""
            return paste_error(session, text, "This form was out of date. Nothing was saved. Preview the report again before saving it.", 403)
        try:
            document = _verified(auth, session.user_id, "paste-confirm", token)
        except ExpiredReportPreview:
            return paste_error(session, recovered_text(session, token), "This report preview expired after ten minutes. Nothing was saved. Your report is below; preview it again before saving it.", 400)
        except AuthenticationError:
            return paste_error(session, "", "This report preview could not be verified. Nothing was saved. Paste the original report again.", 400)
        payload = validate_report(document["value"])
        try:
            reports.save_paste(session.user_id, payload, document["nonce"])
        except ReportError as error:
            return paste_error(session, _paste_text(payload), error.detail, error.status, error.retry_after)
        return renewed(request, RedirectResponse("/app", status_code=303))

    async def handled(request):
        session, _, response = await checked(request, set())
        if response is not None:
            return response
        reports.mark_handled(session.user_id, request.path_params["report_id"])
        return renewed(request, RedirectResponse("/app", status_code=303))

    async def settings(request):
        session, fields, response = await checked(request, {"paused", "cadence"})
        if response is not None:
            return response
        if fields.get("paused") not in {"true", "false"} or fields.get("cadence") not in {"none", "1440", "10080"}:
            return auth.error("Choose whether intake is paused and an expected check-in, or no schedule.", 422)
        reports.update_settings(session.user_id, paused=fields["paused"] == "true",
                                cadence_minutes=None if fields["cadence"] == "none" else int(fields["cadence"]))
        return renewed(request, RedirectResponse("/app", status_code=303))

    async def ingest(request):
        if not auth.enabled:
            return JSONResponse({"error": "Report intake is not open yet."}, status_code=503)
        try:
            if len(request.headers.getlist("authorization")) != 1 or len(request.headers.getlist("idempotency-key")) != 1:
                return JSONResponse({"error": "One Authorization and one Idempotency-Key header are required."}, status_code=400)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"error": "Use application/json for reports."}, status_code=415)
            receipt = reports.ingest(request.headers.get("authorization"), await request.body(), request.headers.get("idempotency-key"))
            return JSONResponse(receipt, status_code=201)
        except ReportError as error:
            headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
            return JSONResponse({"error": error.detail}, status_code=error.status, headers=headers)

    async def me(request):
        if not auth.enabled:
            return JSONResponse({"error": "Report intake is not open yet."}, status_code=503)
        try:
            if len(request.headers.getlist("authorization")) != 1:
                return JSONResponse({"error": "One Authorization header is required."}, status_code=401)
            identity = reports.authenticate(request.headers.get("authorization"))
            with auth.database.connect() as connection:
                profile = connection.execute("SELECT display_name FROM users WHERE id = ?", (identity["user_id"],)).fetchone()
            if profile is None:
                return JSONResponse({"error": "Report key is not valid."}, status_code=401)
            owned = board.owner_posting(identity["user_id"])
            posting = owned["published"] if owned["visible"] else None
            return JSONResponse({"owner": {"id": identity["user_id"], "display_name": profile["display_name"]},
                                 "posting": posting, "posting_available": posting is not None})
        except (ReportError, BoardError) as error:
            return JSONResponse({"error": error.detail}, status_code=error.status)

    return [
        route("/app", dashboard, ["GET"]),
        route("/app/keys", keys, ["GET"]),
        route("/app/keys/new", keys, ["GET"]),
        route("/app/keys", create_key, ["POST"]),
        route("/app/connect", connect_agent, ["POST"]),
        route("/app/keys/prompt-download", download_prompt, ["POST"]),
        route("/app/keys/{key_id}/revoke", revoke_key, ["POST"]),
        route("/app/keys/{key_id}/rename", rename_key, ["POST"]),
        route("/app/reports/paste", paste, ["GET"]),
        route("/app/reports/preview", preview, ["POST"]),
        route("/app/reports/confirm", confirm, ["POST"]),
        route("/app/reports/{report_id}/handled", handled, ["POST"]),
        route("/app/settings", settings, ["POST"]),
        route("/app/share-permission/remove", remove_share_permission, ["POST"]),
        Route("/api/v1/reports", ingest, methods=["POST"]),
        Route("/api/v1/me", me, methods=["GET"]),
    ]
